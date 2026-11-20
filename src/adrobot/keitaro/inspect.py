"""Read-only Keitaro stream shape inspector; never dumps values or credentials.

Usage: python -m adrobot.keitaro.inspect CAMPAIGN_ID

This diagnostic is intentionally separate from the application API. It reports
only JSON field names, scalar types and counts, not upstream field values.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from adrobot.config import get_settings
from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.exceptions import KeitaroError
from adrobot.keitaro.read_models import validate_campaign_id


def describe_type(value: object) -> str:
    """Return a safe JSON shape label without including the value itself."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (float, int)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _field_types(row: Mapping[str, Any]) -> dict[str, str]:
    """Summarize field types, never values, for integration debugging."""
    return {key: describe_type(row[key]) for key in sorted(row)}


def describe_streams(streams: list[dict[str, Any]]) -> dict[str, object]:
    """Summarize stream and nested-offer schemas without returning private data."""
    result: list[dict[str, object]] = []
    for stream in streams:
        offers = stream.get("offers")
        offer_sample = (
            next((offer for offer in offers if isinstance(offer, dict)), None)
            if isinstance(offers, list)
            else None
        )
        result.append(
            {
                "stream_fields": _field_types(stream),
                "offers_count": len(offers) if isinstance(offers, list) else None,
                "offer_fields": _field_types(offer_sample) if offer_sample is not None else None,
                "offers_field_type": describe_type(offers) if "offers" in stream else "absent",
            }
        )
    return {"stream_count": len(streams), "streams": result}


async def inspect(campaign_id: int) -> dict[str, object]:
    """Read one campaign through the same API client used by the application."""
    validate_campaign_id(campaign_id)
    settings = get_settings()
    if not settings.keitaro_configured:
        raise RuntimeError("Keitaro connection is not configured")
    assert settings.keitaro_url is not None
    assert settings.keitaro_api_key is not None
    async with httpx.AsyncClient(
        base_url=settings.keitaro_url,
        headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        verify=True,
        follow_redirects=False,
        trust_env=False,
    ) as transport:
        streams = await KeitaroClient(transport).get_campaign_streams(campaign_id)
        return describe_streams([stream.model_dump(by_alias=True) for stream in streams])


def main() -> int:
    """Emit schema-only JSON, returning nonzero on integration errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_id", type=int, help="positive test campaign ID")
    args = parser.parse_args()
    try:
        validate_campaign_id(args.campaign_id)
    except ValueError:
        parser.error("campaign_id must be a positive safe integer")
    try:
        result = asyncio.run(inspect(args.campaign_id))
    except KeitaroError as exc:
        print(f"Keitaro inspection failed: {exc.code}", file=sys.stderr)
        return 1
    except ValidationError:
        print("Keitaro inspection failed: invalid configuration", file=sys.stderr)
        return 2
    except RuntimeError:
        print("Keitaro inspection failed: missing configuration", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
