"""App-owned serialized Fetch with a cancellation-safe SQL completion barrier.

Only a *concurrent.futures.Future* that completes after Session.close may
release a campaign lock after executor submission. An asyncio wrapper future
can be cancelled earlier and MUST NOT be used as the release condition.
"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress

from fastapi import Request
from sqlalchemy import Engine

from adrobot.db.archive import create_archive_engine, sync_campaign
from adrobot.editor.schemas import EditorSnapshot
from adrobot.editor.service import EditorProblem, read_remote_campaign
from adrobot.keitaro.client import KeitaroClient

logger = logging.getLogger("adrobot.editor")
GET_BODY_TIMEOUT_SECONDS = 3.0


async def require_empty_get_body(request: Request) -> None:
    """Wait for *actual* end of a wholly empty GET body before remote/SQL work."""
    try:
        async with asyncio.timeout(GET_BODY_TIMEOUT_SECONDS):
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    raise EditorProblem("editor_request_disconnected", "Request disconnected", 408)
                if message["type"] != "http.request":
                    raise EditorProblem("unexpected_get_body", "Unexpected GET body", 400)
                if message.get("body", b""):
                    raise EditorProblem("unexpected_get_body", "GET body is not allowed", 400)
                if not message.get("more_body", False):
                    return
    except TimeoutError as exc:
        raise EditorProblem("incomplete_get_body", "GET request body is incomplete", 408) from exc


async def watch_disconnect(request: Request, abort: asyncio.Event) -> None:
    """Abort on disconnect, unexpected input, or a failed receive after EOS."""
    try:
        await request.receive()
    except Exception as exc:
        logger.info("editor receive failed", extra={"error_type": type(exc).__name__})
    # The body gate already saw EOS. Further frames cannot extend that body.
    abort.set()


class FetchCoordinator:
    """Lifetime owner of campaign locks, background jobs and SQL futures."""

    def __init__(self, database_url: str, *, max_workers: int = 4) -> None:
        self._database_url = database_url
        self._engine: Engine | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="adrobot-db"
        )
        self._locks: dict[int, asyncio.Lock] = {}
        self._jobs: set[asyncio.Task[EditorSnapshot]] = set()
        self._sql_futures: set[Future[EditorSnapshot]] = set()
        self._closing = False

    @property
    def engine(self) -> Engine | None:
        """Expose for narrow DB inspection in tests; not a public API."""
        return self._engine

    def _get_engine(self) -> Engine:
        if self._engine is None:
            try:
                self._engine = create_archive_engine(self._database_url)
            except ValueError as exc:
                raise EditorProblem(
                    "local_archive_unavailable", "Local archive is unavailable", 503
                ) from exc
        return self._engine

    @staticmethod
    def _observe_task(task: asyncio.Task[EditorSnapshot]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # The request already received the expected error or was cancelled.
            # Do not leak exception payloads or leave "never retrieved" warnings.
            logger.info("owned fetch finished with error", extra={"error_type": type(exc).__name__})

    def _observe_sql(
        self,
        future: Future[EditorSnapshot],
        loop: asyncio.AbstractEventLoop,
        release: Callable[[], None],
    ) -> None:
        """Real SQL worker completion, including rollback and Session.close."""
        try:
            future.exception()
        except FutureCancelledError:
            pass
        finally:
            self._sql_futures.discard(future)
            # A lock may be released by another callback, never from the worker
            # thread. The app's shutdown drains futures before closing its loop.
            loop.call_soon_threadsafe(release)

    async def _run(
        self, campaign_id: int, upstream: KeitaroClient, abort: asyncio.Event
    ) -> EditorSnapshot:
        lock = self._locks.setdefault(campaign_id, asyncio.Lock())
        await lock.acquire()  # Before the FIRST remote GET, not just before SQL.
        delegated = False
        try:
            if abort.is_set():
                raise EditorProblem("editor_request_disconnected", "Request disconnected", 408)
            remote = await read_remote_campaign(upstream, campaign_id)
            if abort.is_set():
                raise EditorProblem("editor_request_disconnected", "Request disconnected", 408)
            engine = self._get_engine()
            loop = asyncio.get_running_loop()
            # No await between the final abort check and executor.submit().
            if abort.is_set():
                raise EditorProblem("editor_request_disconnected", "Request disconnected", 408)
            future = self._executor.submit(sync_campaign, engine, remote)
            self._sql_futures.add(future)
            future.add_done_callback(lambda done: self._observe_sql(done, loop, lock.release))
            delegated = True
            # Cancellation of this asyncio await can cancel only its wrapper.
            # The original concurrent future owns the lock until SQL is DONE.
            return await asyncio.wrap_future(future)
        finally:
            if not delegated:
                lock.release()

    async def fetch(
        self, campaign_id: int, upstream: KeitaroClient, abort: asyncio.Event
    ) -> EditorSnapshot:
        """Single shield: cancelling the HTTP task never cancels the owned job."""
        if self._closing:
            raise EditorProblem("local_archive_unavailable", "Editor is shutting down", 503)
        job = asyncio.create_task(self._run(campaign_id, upstream, abort))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        job.add_done_callback(self._observe_task)
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            abort.set()
            raise

    async def close(self) -> None:
        """Drain all in-flight SQL before disposing engine or stopping threads."""
        self._closing = True
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
        if self._sql_futures:
            await asyncio.gather(
                *(asyncio.wrap_future(item) for item in tuple(self._sql_futures)),
                return_exceptions=True,
            )
        # The done callbacks are scheduled via call_soon_threadsafe.
        await asyncio.sleep(0)
        self._executor.shutdown(wait=True, cancel_futures=False)
        if self._engine is not None:
            self._engine.dispose()


async def fetch_for_request(
    request: Request,
    campaign_id: int,
    upstream: KeitaroClient,
    coordinator: FetchCoordinator,
) -> EditorSnapshot:
    """ASGI empty-body gate then exactly one disconnect watcher during Fetch."""
    await require_empty_get_body(request)
    abort = asyncio.Event()
    watcher = asyncio.create_task(watch_disconnect(request, abort))
    try:
        return await coordinator.fetch(campaign_id, upstream, abort)
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
