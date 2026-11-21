"""B1 ASGI/middleware + actual SQLite thread-completion cancellation probes."""

import asyncio
import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from b1_support import FakeKeitaro, app_for, db_rows, migrate, stream
from sqlalchemy import Engine, event
from sqlalchemy.exc import OperationalError

from adrobot.config import Settings
from adrobot.editor import fetch_coordinator
from adrobot.main import create_app

EDITOR = "/api/campaigns/501/editor"


async def _client(app, action: Callable[[httpx.AsyncClient], Awaitable[None]]) -> None:
    """ASGITransport doesn't automatically run lifespan, so run the real one."""
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await action(client)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("cancel_owned_job", [False, True])
def test_cancel_during_actual_sql_then_second_fetch_cannot_overtake(
    tmp_path: Path, failure: bool, cancel_owned_job: bool
) -> None:
    db = tmp_path / "cancel.db"
    migrate(db)
    fake = FakeKeitaro([stream()])
    app = app_for(db, fake)
    initial_offers = [
        (101, 201, 25, "active"),
        (102, 202, 25, "active"),
    ]
    sql_started = threading.Event()
    sql_unblock = threading.Event()
    only_once = threading.Event()

    async def exercise(client: httpx.AsyncClient) -> None:
        assert (await client.get(EDITOR)).status_code == 200
        engine = app.state.editor_coordinator.engine
        assert engine is not None
        fake.streams = [stream(pairs=[initial_offers[0]])]

        def intercept(_connection, _cursor, statement, _params, _context, _executemany):
            if statement.lstrip().upper().startswith("UPDATE ") and not only_once.is_set():
                only_once.set()
                sql_started.set()
                if not sql_unblock.wait(timeout=7):
                    raise AssertionError("SQL completion never released in cancellation test")
                if failure:
                    raise OperationalError(
                        "forced UPDATE", {}, sqlite3.OperationalError("forced SQL error")
                    )

        event.listen(engine, "before_cursor_execute", intercept)
        try:
            first = asyncio.create_task(client.get(EDITOR))
            assert await asyncio.to_thread(sql_started.wait, 4), "First SQL was not reached"
            assert len([p for p in fake.calls if p.endswith("/streams")]) == 2
            if cancel_owned_job:
                jobs = app.state.editor_coordinator._jobs
                assert len(jobs) == 1
                next(iter(jobs)).cancel()
            else:
                first.cancel()
            # Flush cancellation to the request task without releasing SQL.
            await asyncio.sleep(0)
            fake.streams = [stream(pairs=initial_offers)]
            second = asyncio.create_task(client.get(EDITOR))
            # The second request may have arrived at the HTTP middleware, but
            # must not enter even its FIRST remote GET before SQL has completed.
            for _ in range(3):
                await asyncio.sleep(0)
            assert len([p for p in fake.calls if p.endswith("/streams")]) == 2
            assert db_rows(db)[1][10] is None  # original B is not yet marked removed
            sql_unblock.set()
            result = await asyncio.wait_for(second, timeout=8)
            assert result.status_code == 200, result.text
            assert result.json()["streams"][0]["known_offers"][1]["status"] == "Active"
            assert db_rows(db)[1][10] is None
            assert len([p for p in fake.calls if p.endswith("/streams")]) == 3
            first_result = await asyncio.gather(first, return_exceptions=True)
            assert first_result
            await asyncio.sleep(0)  # scheduled lock release callback
            assert not app.state.editor_coordinator._locks[501].locked()
        finally:
            sql_unblock.set()
            event.remove(engine, "before_cursor_execute", intercept)

    asyncio.run(_client(app, exercise))


def test_busy_begin_immediate_fails_503_then_recovers(tmp_path: Path) -> None:
    db = tmp_path / "busy.db"
    migrate(db)
    fake = FakeKeitaro()
    app = app_for(db, fake)

    async def exercise(client: httpx.AsyncClient) -> None:
        locker = sqlite3.connect(db, isolation_level=None)
        locker.execute("BEGIN IMMEDIATE")
        try:
            response = await asyncio.wait_for(client.get(EDITOR), 6)
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "local_archive_busy"
            assert response.headers["cache-control"] == "no-store"
            assert db_rows(db) == []
        finally:
            locker.rollback()
            locker.close()
        assert (await client.get(EDITOR)).status_code == 200
        assert len(db_rows(db)) == 2

    asyncio.run(_client(app, exercise))


def _raw_scope() -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "GET",
        "scheme": "http",
        "path": EDITOR,
        "raw_path": EDITOR.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "http_version": "1.1",
        "client": ("127.0.0.1", 9999),
        "server": ("testserver", 80),
    }


@pytest.mark.parametrize(("last", "status"), [(b"x", 400), (b"{}", 400), (b"", 200)])
def test_chunked_body_requires_empty_end_of_stream(
    tmp_path: Path, last: bytes, status: int
) -> None:
    db = tmp_path / "body.db"
    migrate(db)
    fake = FakeKeitaro()
    app = app_for(db, fake)

    async def run() -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        response_frames: list[dict[str, Any]] = []
        queue.put_nowait({"type": "http.request", "body": b"", "more_body": True})
        awaiting_tail = asyncio.Event()

        async def receive():
            if queue.empty():
                awaiting_tail.set()
            return await queue.get()

        async def send(message):
            response_frames.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                queue.put_nowait({"type": "http.disconnect"})

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(_raw_scope(), receive, send))
            await asyncio.wait_for(awaiting_tail.wait(), 3)
            assert fake.calls == []
            queue.put_nowait({"type": "http.request", "body": last, "more_body": False})
            await asyncio.wait_for(task, 5)
        assert response_frames[0]["type"] == "http.response.start"
        assert response_frames[0]["status"] == status
        assert bool(fake.calls) == (status == 200)
        assert len(db_rows(db)) == (2 if status == 200 else 0)

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [True, False])
def test_missing_end_of_stream_prevents_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disconnect: bool
) -> None:
    db = tmp_path / "incomplete.db"
    migrate(db)
    fake = FakeKeitaro()
    app = app_for(db, fake)
    monkeypatch.setattr(fetch_coordinator, "GET_BODY_TIMEOUT_SECONDS", 0.1)

    async def run() -> None:
        incoming = asyncio.Queue()
        frames = []
        incoming.put_nowait({"type": "http.request", "body": b"", "more_body": True})
        if disconnect:
            incoming.put_nowait({"type": "http.disconnect"})

        async def send(message):
            frames.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                incoming.put_nowait({"type": "http.disconnect"})

        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(_raw_scope(), incoming.get, send), 3)
        assert frames[0]["status"] == 408
        body = b"".join(frame.get("body", b"") for frame in frames)
        code = "editor_request_disconnected" if disconnect else "incomplete_get_body"
        assert json.loads(body)["error"]["code"] == code

    asyncio.run(run())
    assert fake.calls == []
    assert db_rows(db) == []


def test_receive_error_after_empty_body_stops_fetch_before_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "receive-error.db"
    migrate(db)
    upstream_started = asyncio.Event()
    watcher_finished = asyncio.Event()
    response_sent = asyncio.Event()
    frames: list[dict[str, Any]] = []
    dml: list[str] = []
    fake = FakeKeitaro()
    original_watcher = fetch_coordinator.watch_disconnect

    async def observe_watcher(request, abort):
        try:
            await original_watcher(request, abort)
        finally:
            watcher_finished.set()

    monkeypatch.setattr(fetch_coordinator, "watch_disconnect", observe_watcher)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/streams"):
            upstream_started.set()
            await watcher_finished.wait()
        return fake.handler(request)

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="SYNTHETIC",
            database_url="sqlite:///" + str(db),
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )

    receive_count = 0

    async def receive():
        nonlocal receive_count
        receive_count += 1
        if receive_count == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        if receive_count == 2:
            await upstream_started.wait()
            raise OSError("synthetic receive failure")
        await response_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        frames.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_sent.set()

    def trace(_connection, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            dml.append(statement)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            event.listen(Engine, "before_cursor_execute", trace)
            try:
                await asyncio.wait_for(app(_raw_scope(), receive, send), 6)
            finally:
                event.remove(Engine, "before_cursor_execute", trace)

    asyncio.run(exercise())
    assert upstream_started.is_set() and watcher_finished.is_set()
    assert frames[0]["status"] == 408
    body = b"".join(frame.get("body", b"") for frame in frames)
    assert json.loads(body)["error"]["code"] == "editor_request_disconnected"
    assert b"synthetic receive failure" not in body
    assert dml == []
    assert db_rows(db) == []


def test_disconnect_processed_during_remote_wait_prevents_sql(tmp_path: Path) -> None:
    db = tmp_path / "disconnect.db"
    migrate(db)
    started = asyncio.Event()
    proceed = asyncio.Event()
    calls: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/streams"):
            started.set()
            await proceed.wait()
            return httpx.Response(200, json=[stream()])
        if req.url.path.endswith("/offers"):
            return httpx.Response(
                200,
                json=[
                    {"id": 101, "name": "Name A", "state": "active"},
                    {"id": 102, "name": "Name B", "state": "active"},
                ],
            )
        raise AssertionError(req.url.path)

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="secret",
            database_url="sqlite:///" + str(db),
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )

    async def run() -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        frames: list[dict[str, Any]] = []
        queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

        async def receive():
            return await queue.get()

        async def send(message):
            frames.append(message)

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(_raw_scope(), receive, send))
            await asyncio.wait_for(started.wait(), 3)
            queue.put_nowait({"type": "http.disconnect"})
            # Give watcher a scheduling point to process the delivered event.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            proceed.set()
            await asyncio.wait_for(task, 6)
        assert any(p.endswith("/streams") for p in calls)
        assert not db_rows(db)

    asyncio.run(run())


def test_late_disconnect_preserves_sql_ownership_through_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "shutdown.db"
    migrate(db)
    fake = FakeKeitaro()
    sql_started = threading.Event()
    release_sql = threading.Event()
    other_started = asyncio.Event()
    release_other = asyncio.Event()
    disconnected = asyncio.Event()
    shutdown_started = asyncio.Event()
    shutdown_entered = asyncio.Event()
    lifespan_ready = asyncio.Event()
    original_watcher = fetch_coordinator.watch_disconnect

    async def observe_watcher(request, abort):
        await original_watcher(request, abort)
        if request.url.path == EDITOR:
            disconnected.set()

    monkeypatch.setattr(fetch_coordinator, "watch_disconnect", observe_watcher)

    def hold_insert(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("INSERT INTO KNOWN_OFFER_BINDINGS"):
            sql_started.set()
            assert release_sql.wait(8), "Test did not release the archive worker"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/campaigns/502/streams"):
            other_started.set()
            await release_other.wait()
            return httpx.Response(200, json=[stream(12, campaign_id=502)])
        return fake.handler(request)

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="SYNTHETIC",
            database_url="sqlite:///" + str(db),
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )

    async def keep_app_running() -> None:
        async with app.router.lifespan_context(app):
            coordinator = app.state.editor_coordinator
            close = coordinator.close

            async def observe_close():
                shutdown_entered.set()
                await close()

            monkeypatch.setattr(coordinator, "close", observe_close)
            lifespan_ready.set()
            await shutdown_started.wait()

    async def run() -> None:
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})

        async def send(_message):
            pass

        event.listen(Engine, "before_cursor_execute", hold_insert)
        lifecycle = asyncio.create_task(keep_app_running())
        try:
            await asyncio.wait_for(lifespan_ready.wait(), 3)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                first = asyncio.create_task(app(_raw_scope(), incoming.get, send))
                try:
                    assert await asyncio.to_thread(sql_started.wait, 5)
                    incoming.put_nowait({"type": "http.disconnect"})
                    await asyncio.wait_for(disconnected.wait(), 3)

                    other = asyncio.create_task(client.get("/api/campaigns/502/editor"))
                    try:
                        await asyncio.wait_for(other_started.wait(), 3)
                        shutdown_started.set()
                        await asyncio.wait_for(shutdown_entered.wait(), 3)
                        assert app.state.editor_coordinator._locks[501].locked()
                        assert not first.done()
                        assert not lifecycle.done()
                        assert db_rows(db) == []
                    finally:
                        release_sql.set()
                        release_other.set()
                        response = await asyncio.wait_for(other, 5)
                    assert response.status_code == 200, response.text
                finally:
                    release_sql.set()
                    await asyncio.wait_for(first, 5)
        finally:
            shutdown_started.set()
            await asyncio.wait_for(lifecycle, 5)
            event.remove(Engine, "before_cursor_execute", hold_insert)

    asyncio.run(run())
    assert {row[0] for row in db_rows(db)} == {501, 502}
    assert len(db_rows(db)) == 4
