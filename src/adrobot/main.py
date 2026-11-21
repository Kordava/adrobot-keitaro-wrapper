"""Application factory and lifespan ownership of the Keitaro HTTP connection pool."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from adrobot.api.errors import keitaro_exception_handler
from adrobot.api.routes import router
from adrobot.config import Settings, get_settings
from adrobot.editor.fetch_coordinator import FetchCoordinator
from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.exceptions import KeitaroError
from adrobot.logging_setup import setup_logging

logger = logging.getLogger("adrobot")


def create_app(
    settings: Settings | None = None,
    *,
    keitaro_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build a testable app; mock transport is injected only in tests.

    A production app owns one pooled client; no caller-provided destination URL.
    """
    config = settings or get_settings()
    setup_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.keitaro = None
        app.state.editor_coordinator = FetchCoordinator(config.database_url)
        if config.keitaro_configured:
            assert config.keitaro_url is not None
            assert config.keitaro_api_key is not None
            async with httpx.AsyncClient(
                base_url=config.keitaro_url,
                headers={"Api-Key": config.keitaro_api_key.get_secret_value()},
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
                follow_redirects=False,
                trust_env=False,
                verify=True,
                transport=keitaro_transport,
            ) as client:
                app.state.keitaro = KeitaroClient(client)
                try:
                    yield
                finally:
                    await app.state.editor_coordinator.close()
        else:
            try:
                yield
            finally:
                await app.state.editor_coordinator.close()

    app = FastAPI(title="Adrobot", lifespan=lifespan)
    app.state.settings = config
    app.add_exception_handler(KeitaroError, keitaro_exception_handler)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlate requests and time their responses without logging request bodies."""
        started = perf_counter()
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        segments = request.url.path.split("/")
        if (
            len(segments) == 5
            and segments[1:3] == ["api", "campaigns"]
            and segments[3]
            and segments[4] in {"editor", "remote-snapshot"}
        ):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "http request complete",
            extra={
                "request_id": request.state.request_id,
                "action": request.method,
                "stage": "response",
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            },
        )
        return response

    from pathlib import Path

    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parent / "web/static")), name="static"
    )
    app.include_router(router)
    return app


app = create_app()
