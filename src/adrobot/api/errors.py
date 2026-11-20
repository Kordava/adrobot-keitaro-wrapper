"""Central safe mapping of predictable upstream failures."""

from fastapi import Request
from fastapi.responses import JSONResponse

from adrobot.keitaro.exceptions import KeitaroError


async def keitaro_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return structured safe errors without upstream body, secrets or traceback."""
    if not isinstance(exc, KeitaroError):
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store"},
        content={
            "error": {"code": exc.code, "message": str(exc), "request_id": request.state.request_id}
        },
    )
