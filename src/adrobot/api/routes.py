"""Thin FastAPI handlers; upstream reads and domain rules live elsewhere."""

import logging
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Request, Response
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.exceptions import KeitaroError, KeitaroNotConfigured
from adrobot.keitaro.read_models import JS_SAFE_INTEGER, ReadonlySnapshot, build_readonly_snapshot

router = APIRouter()
logger = logging.getLogger("adrobot.fetch")
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "web/templates"))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Home page; campaign creation belongs to TASK-02."""
    return _templates.TemplateResponse(request, "index.html")


@router.get("/editor", response_class=HTMLResponse)
async def editor(request: Request) -> HTMLResponse:
    """Read-only editor shell; interactive draft belongs to TASK-03."""
    return _templates.TemplateResponse(request, "editor.html")


@router.get("/health")
async def health() -> dict[str, str]:
    """Local liveness check: no dependency on Keitaro or SQLite."""
    return {"status": "ok"}


@router.get("/api/campaigns/{campaign_id}/editor", response_model=ReadonlySnapshot)
async def campaign_streams(
    request: Request,
    campaign_id: Annotated[int, ApiPath(gt=0, le=JS_SAFE_INTEGER)],
    response: Response,
) -> ReadonlySnapshot:
    """Return validated remote streams/bindings without an archive or writes.

    TASK-03 will extend this route into the complete editor snapshot, including
    local binding history and pinned shares. The provisional response is tagged
    `remote_only` so callers cannot mistake it for a publishable draft.
    """
    response.headers["Cache-Control"] = "no-store"
    started = perf_counter()
    context = {
        "action": "readonly_fetch",
        "request_id": request.state.request_id,
        "campaign_id": campaign_id,
    }
    logger.info("readonly fetch started", extra={**context, "stage": "start"})
    try:
        upstream: KeitaroClient | None = getattr(request.app.state, "keitaro", None)
        if upstream is None:
            raise KeitaroNotConfigured(
                "Keitaro credentials not configured",
                failure_kind="configuration",
                failure_stage="config",
            )
        streams = await upstream.get_campaign_streams(campaign_id)
        snapshot = build_readonly_snapshot(campaign_id, streams)
    except KeitaroError as exc:
        details: dict[str, object] = {
            **context,
            "stage": "failed",
            "duration_ms": round((perf_counter() - started) * 1000, 1),
            "status_code": exc.status_code,
            "error_code": exc.code,
            "failure_kind": exc.failure_kind,
            "failure_stage": exc.failure_stage,
        }
        if exc.stream_id is not None:
            details["stream_id"] = exc.stream_id
        if exc.upstream_status is not None:
            details["upstream_status"] = exc.upstream_status
        logger.warning("readonly fetch failed", extra=details)
        raise
    logger.info(
        "readonly fetch complete",
        extra={
            **context,
            "stage": "complete",
            "duration_ms": round((perf_counter() - started) * 1000, 1),
            "status_code": 200,
            "stream_ids": [stream.id for stream in snapshot.streams],
            "stream_count": len(snapshot.streams),
            "offer_stream_count": sum(stream.kind == "offers" for stream in snapshot.streams),
            "binding_count": sum(len(stream.offers) for stream in snapshot.streams),
        },
    )
    return snapshot
