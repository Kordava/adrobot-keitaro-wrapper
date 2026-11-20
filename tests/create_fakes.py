"""Deterministic Keitaro fake; no network, keys or real campaign mutations."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app


class FakeCreateKeitaro:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.campaign: dict[str, Any] | None = None
        self.streams: list[dict[str, Any]] = []
        self.campaign_reply_type: str | None = None
        self.readback_type: str | None = None
        self.extra_stream = False
        self.fail_post_at: int | None = None
        self.fail_post_status = 422
        self.fail_transport_at: int | None = None
        self.malformed_post_at: int | None = None
        self.readback_status: int | None = None
        self.incomplete_streams = False
        self.offer_state = "active"
        self.invalid_default = False
        self.fail_offer_status: int | None = None
        self.ignore_campaign_type = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST":
            import json

            payload: dict[str, Any] = json.loads(request.content)
            self.posts.append((path, payload))
            nth = len(self.posts)
            if self.fail_transport_at == nth:
                raise httpx.ReadTimeout("fake-only timeout")
            if self.fail_post_at == nth:
                return httpx.Response(self.fail_post_status, json={"internal": "private"})
            if self.malformed_post_at == nth:
                return httpx.Response(200, text="not-json")
            if path == "/admin_api/v1/campaigns":
                self.campaign = {
                    "id": 501,
                    "name": payload["name"],
                    "alias": payload["alias"],
                    "type": payload["type"],
                    "state": payload["state"],
                    "domain_id": payload["domain_id"],
                    "group_id": int(payload["group_id"]),
                    "traffic_source_id": payload["traffic_source_id"],
                    "token": "PRIVATE-CAMPAIGN-TOKEN",
                }
                if self.ignore_campaign_type:
                    del self.campaign["type"]
                reply: dict[str, object] = {"id": 501}
                if self.campaign_reply_type is not None:
                    reply["type"] = self.campaign_reply_type
                return httpx.Response(200, json=reply)
            if path == "/admin_api/v1/streams":
                stream_id = 2001 + len(self.streams)
                stream: dict[str, Any] = {
                    **payload,
                    "id": stream_id,
                    "filters": [
                        dict(item, id=110, stream_id=stream_id)
                        for item in payload.get("filters", [])
                    ],
                    "offers": [
                        dict(item, id=5000, stream_id=stream_id)
                        for item in payload.get("offers", [])
                    ],
                    "landings": [],
                    "private_field": "PRIVATE-STREAM",
                }
                self.streams.append(stream)
                return httpx.Response(201, json={"id": stream_id})
            return httpx.Response(404)
        if request.method != "GET":
            return httpx.Response(405)
        if path == "/admin_api/v1/offers/42":
            if self.fail_offer_status is not None:
                return httpx.Response(self.fail_offer_status)
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Approved Offer",
                    "state": self.offer_state,
                    "action_payload": "PRIVATE-OFFER-URL",
                },
            )
        if path.startswith("/admin_api/v1/offers/"):
            return httpx.Response(404)
        if path == "/admin_api/v1/offers":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "name": "Approved Offer",
                        "state": self.offer_state,
                        "action_payload": "PRIVATE-OFFER-URL",
                    },
                    {"id": 43, "name": "Second Offer", "state": "active"},
                    {"id": 44, "name": "Inactive Offer", "state": "disabled"},
                ],
            )
        resources = {
            "/admin_api/v1/domains": (10, "Test Domain"),
            "/admin_api/v1/campaign_groups": (20, "Test Group"),
            "/admin_api/v1/traffic_sources": (30, "Test Source"),
        }
        if path in resources:
            id_, name = resources[path]
            return httpx.Response(
                200,
                json=[
                    {
                        "id": id_,
                        "name": name,
                        "state": "disabled" if self.invalid_default and id_ == 20 else "active",
                        "extra_secret": "PRIVATE-DEFAULT",
                    }
                ],
            )
        if path == "/admin_api/v1/stream_filters":
            return httpx.Response(200, json=[{"value": "country", "modes": {"accept": "Accept"}}])
        if path == "/admin_api/v1/streams_actions":
            return httpx.Response(200, json=[{"key": "http", "name": "HTTP"}])
        if path == "/admin_api/v1/campaigns/501":
            if self.readback_status is not None:
                return httpx.Response(self.readback_status)
            if self.campaign is None:
                return httpx.Response(404)
            data = self.campaign.copy()
            if self.readback_type is not None:
                data["type"] = self.readback_type
            return httpx.Response(200, json=data)
        if path == "/admin_api/v1/campaigns/501/streams":
            if self.readback_status is not None:
                return httpx.Response(self.readback_status)
            data = list(self.streams)
            if self.extra_stream:
                data.append(
                    {
                        "id": 3000,
                        "campaign_id": 501,
                        "name": "unexpected",
                        "position": 3,
                        "schema": "action",
                        "state": "active",
                        "filters": [],
                        "offers": [],
                    }
                )
            headers = {"Content-Range": "items 0-1/3"} if self.incomplete_streams else {}
            return httpx.Response(200, json=data, headers=headers)
        return httpx.Response(404)


def fake_app(
    fake: FakeCreateKeitaro, *, write: bool = True, mapping: bool = True, **kwargs: Any
) -> Any:
    return create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="SYNTHETIC-SECRET-KEY",
            create_domain_id=10,
            create_group_id=20,
            create_traffic_source_id=30,
            create_offer_action_type="http",
            create_mapping_verified=mapping,
            create_writes_enabled=write,
            _env_file=None,
            **kwargs,
        ),
        keitaro_transport=httpx.MockTransport(fake),
    )


def create_request(
    client: TestClient, *, name: str = " Test Campaign ", geo: str = "AU", offer_id: int = 42
) -> httpx.Response:
    return client.post("/api/campaigns", json={"name": name, "geo": geo, "offer_id": offer_id})
