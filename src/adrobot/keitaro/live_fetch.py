"""Opt-in read-only smoke of the real FastAPI Fetch route, never a write.

Usage after configuring .env locally:
    python -m adrobot.keitaro.live_fetch NUMERIC_CAMPAIGN_ID

No upstream credentials, campaign details, offer names or remote JSON are printed.
The command uses the exact FastAPI route and Keitaro client used by the UI.
"""

import argparse
import asyncio
import json
import sys

import httpx
from pydantic import ValidationError

from adrobot.config import Settings, get_settings
from adrobot.keitaro.read_models import ReadonlySnapshot, validate_campaign_id


async def fetch_through_app(
    campaign_id: int,
    settings: Settings,
    *,
    keitaro_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int | str]:
    """Exercise a live (or injected fake) GET through FastAPI without a server port.

    Fail closed on non-200 responses. Output contains only counters and route status;
    upstream bodies and authentication headers cannot reach the diagnostic output.
    """
    validate_campaign_id(campaign_id)
    # main constructs its public app eagerly; CLI settings must be validated first.
    from adrobot.main import create_app

    app = create_app(settings, keitaro_transport=keitaro_transport)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            base_url="http://adrobot.local",
            transport=httpx.ASGITransport(app=app),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(f"/api/campaigns/{campaign_id}/editor")
    if response.status_code != 200:
        try:
            error_code = response.json()["error"]["code"]
        except (KeyError, TypeError, ValueError):
            error_code = "unexpected_response"
        raise RuntimeError(f"Fetch failed: HTTP {response.status_code}, {error_code}")
    try:
        snapshot = ReadonlySnapshot.model_validate(response.json())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Fetch failed: response contract mismatch") from exc
    if snapshot.mapping_status != "remote_only":
        raise RuntimeError("Fetch failed: unexpected response mapping")
    return {
        "status": "ok",
        "campaign_id": campaign_id,
        "streams": len(snapshot.streams),
        "offer_streams": sum(stream.kind == "offers" for stream in snapshot.streams),
        "bindings": sum(len(stream.offers) for stream in snapshot.streams),
    }


def main() -> int:
    """Return success only after the application Fetch has fully validated the read."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_id", type=int, help="positive, numeric test campaign ID")
    args = parser.parse_args()
    try:
        validate_campaign_id(args.campaign_id)
    except ValueError:
        parser.error("campaign_id must be a positive safe integer")
    try:
        result = asyncio.run(fetch_through_app(args.campaign_id, get_settings()))
    except ValidationError:
        print("Fetch failed: invalid configuration", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as exc:
        # Only our own explicitly sanitized status/error strings may be emitted.
        print(str(exc) if isinstance(exc, RuntimeError) else "Invalid input", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
