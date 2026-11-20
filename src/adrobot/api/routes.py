"""Thin FastAPI handlers; upstream reads and domain rules live elsewhere."""

import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from adrobot.api.create_models import CampaignCreateInput
from adrobot.campaigns.create import (
    CampaignCreator,
    CreateProblem,
    CreateRejected,
    CreateUnavailable,
    InvalidOffer,
)
from adrobot.config import Settings
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
    """Read-only editor, optionally preselected by a verified Create link."""
    raw_id = request.query_params.get("campaign_id")
    selected: int | None = None
    if raw_id is not None:
        if not raw_id or not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 16:
            raise HTTPException(status_code=422, detail="Invalid campaign ID")
        selected = int(raw_id)
        if not 1 <= selected <= JS_SAFE_INTEGER:
            raise HTTPException(status_code=422, detail="Invalid campaign ID")
    return _templates.TemplateResponse(request, "editor.html", {"selected_campaign_id": selected})


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


def _create_api(request: Request) -> CampaignCreator:
    """Resolve the owned adapter and settings; never accept credentials from callers."""
    upstream: KeitaroClient | None = getattr(request.app.state, "keitaro", None)
    if upstream is None:
        raise KeitaroNotConfigured("Keitaro credentials not configured")
    config: Settings = request.app.state.settings
    return CampaignCreator(upstream, config)


def _safe_create_error(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    creation: dict[str, object] | None = None,
) -> JSONResponse:
    content: dict[str, object] = {
        "error": {"code": code, "message": message, "request_id": request.state.request_id}
    }
    if creation is not None:
        content["creation"] = creation
    return JSONResponse(
        status_code=status_code, content=content, headers={"Cache-Control": "no-store"}
    )


@router.get("/api/offers")
async def offers_lookup(request: Request) -> JSONResponse:
    """Read only existing offers; public projection contains just id and name."""
    query = request.query_params.get("query", "").strip()
    if len(query) > 100:
        return _safe_create_error(
            request, code="invalid_query", message="Search is too long", status_code=422
        )
    if not query:
        return JSONResponse(content={"offers": []}, headers={"Cache-Control": "no-store"})
    service = _create_api(request)
    result = await service.search_offers(query)
    return JSONResponse(
        content={"offers": [item.model_dump() for item in result]},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/reference-data")
async def reference_data(request: Request) -> JSONResponse:
    """Expose only safe labels/availability of configured Create prerequisites."""
    service = _create_api(request)
    result = await service.reference_data()
    return JSONResponse(content=result.model_dump(), headers={"Cache-Control": "no-store"})


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("Nonstandard JSON constant")


async def _small_json_object(request: Request) -> object:
    """Bound submitted data before parsing; do not echo invalid user inputs."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ValueError("Expected application/json")
    length = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        length += len(chunk)
        if length > 8192:
            raise ValueError("Request is too large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request must contain valid JSON") from exc


@router.post("/api/campaigns")
async def create_campaign(request: Request) -> JSONResponse:
    """One Create request -> at most one POST per upstream stage; verified readback."""
    # No new CORS configuration. Reject foreign-origin browser requests on the
    # localhost-only interface. JSON content type disallows plain HTML forms.
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        return _safe_create_error(
            request,
            code="invalid_origin",
            message="Cross-origin Create is not allowed",
            status_code=403,
        )
    try:
        raw = await _small_json_object(request)
        submission = CampaignCreateInput.model_validate(raw)
    except (ValueError, ValidationError):
        return _safe_create_error(
            request,
            code="invalid_create_input",
            message="Check Name, GEO and Offer ID",
            status_code=422,
        )
    try:
        service = _create_api(request)
        created = await service.create(submission, request_id=request.state.request_id)
    except CreateUnavailable as exc:
        return _safe_create_error(
            request, code=exc.code, message=str(exc), status_code=exc.status_code
        )
    except CreateRejected as exc:
        return _safe_create_error(
            request, code=exc.code, message=str(exc), status_code=exc.status_code
        )
    except InvalidOffer as exc:
        return _safe_create_error(
            request, code=exc.code, message=str(exc), status_code=exc.status_code
        )
    except CreateProblem as exc:
        return _safe_create_error(
            request,
            code=exc.code,
            message=str(exc),
            status_code=exc.status_code,
            creation={
                "status": exc.status,
                "campaign_id": exc.campaign_id,
                "stage": exc.stage,
                "last_confirmed_stage": exc.last_confirmed_stage,
            },
        )
    return JSONResponse(
        content=created.model_dump(),
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )
