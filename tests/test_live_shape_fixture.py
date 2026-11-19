"""Offline regression on observed Keitaro field shapes; data is synthetic."""

import httpx
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app


def test_live_stream_schema_survives_read_mapping_without_share_math() -> None:
    payload = [
        {
            "id": 2001,
            "name": "Geo XY → Google",
            "campaign_id": 501,
            "position": 1,
            "state": "active",
            "schema": "redirect",
            "action_type": "http",
            "action_payload": "https://example.org",
            "offers": [],
            "filters": [
                {
                    "id": 7001,
                    "stream_id": 2001,
                    "name": "country",
                    "mode": "accept",
                    "payload": ["XY"],
                    "oid": 7001,
                }
            ],
        },
        {
            "id": 2002,
            "name": "Offers",
            "campaign_id": 501,
            "position": 2,
            "state": "active",
            "schema": "landings",
            "action_type": "http",
            "action_payload": None,
            "offers": [
                {"id": 301, "stream_id": 2002, "offer_id": 42, "state": "active", "share": 100}
            ],
            "filters": [],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/admin_api/v1/campaigns/501/streams"
        assert "api_key" not in request.url.params
        assert request.headers["Api-Key"] == "synthetic-key"
        return httpx.Response(200, json=payload)

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example", keitaro_api_key="synthetic-key", _env_file=None
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 200
    streams = response.json()["streams"]
    assert streams[0]["kind"] == "non_offer"
    assert streams[1]["offers"] == [
        {"binding_id": 301, "offer_id": 42, "state": "active", "share": 100}
    ]
    assert "displayed_share" not in response.text
