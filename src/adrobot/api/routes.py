"""Thin FastAPI handlers; upstream reads and domain rules live elsewhere."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Path as ApiPath, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.exceptions import KeitaroNotConfigured
from adrobot.keitaro.read_models import ReadonlySnapshot, build_readonly_snapshot

router = APIRouter()
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
    campaign_id: Annotated[int, ApiPath(gt=0)],
    response: Response,
) -> ReadonlySnapshot:
    """Return validated remote streams/bindings without an archive or writes.

    TASK-03 will extend this route into the complete editor snapshot, including
    local binding history and pinned shares. The provisional response is tagged
    `remote_only` so callers cannot mistake it for a publishable draft.
    """
    response.headers["Cache-Control"] = "no-store"
    upstream: KeitaroClient | None = getattr(request.app.state, "keitaro", None)
    if upstream is None:
        raise KeitaroNotConfigured("Keitaro credentials not configured")
    streams = await upstream.get_campaign_streams(campaign_id)
    return build_readonly_snapshot(campaign_id, streams)
