"""End-to-end offline Create sequencing through the real backend adapter."""

from create_fakes import FakeCreateKeitaro, create_request, fake_app
from fastapi.testclient import TestClient


def test_success_has_one_campaign_and_two_stream_posts_with_full_readback() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = create_request(client, geo="au, MX,au")
        editor = client.get(response.json().get("editor_url", "/editor"))
    assert response.status_code == 201
    assert response.json() == {
        "status": "SUCCESS",
        "campaign_id": 501,
        "editor_url": "/editor?campaign_id=501",
    }
    assert editor.status_code == 200
    assert 'value="501"' in editor.text
    assert 'data-autofetch="true"' in editor.text
    assert len(fake.posts) == 3
    campaign_path, campaign = fake.posts[0]
    assert campaign_path == "/admin_api/v1/campaigns"
    assert campaign["name"] == "Test Campaign"
    assert campaign["type"] == "position"
    assert campaign["domain_id"] == 10
    assert campaign["group_id"] == "20"
    assert campaign["traffic_source_id"] == 30
    assert campaign["alias"].startswith("adrobot-")
    assert len(campaign["alias"]) == len("adrobot-") + 16
    assert fake.posts[1][0] == "/admin_api/v1/streams"
    assert fake.posts[2][0] == "/admin_api/v1/streams"
    geo, fallback = (item[1] for item in fake.posts[1:])
    assert geo["type"] == "regular"
    assert geo["schema"] == "redirect"
    assert geo["position"] == 1
    assert geo["action_type"] == "http"
    assert geo["action_payload"] == "https://google.com"
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert fallback["type"] == "default"
    assert fallback["position"] == 2
    assert fallback["schema"] == "landings"
    assert fallback["offers"] == [{"offer_id": 42, "share": 100, "state": "active"}]
    assert not any(path.endswith("/offers") for path, _ in fake.posts)
    assert all("Api-Key" in item.headers for item in fake.requests)
    assert "SYNTHETIC-SECRET-KEY" not in response.text
    assert "PRIVATE" not in response.text
    assert "https://google.com" not in response.text


def test_wrong_campaign_rotation_is_partial_without_stream_writes() -> None:
    fake = FakeCreateKeitaro()
    fake.readback_type = "weight"
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert response.json()["creation"] == {
        "status": "PARTIAL",
        "campaign_id": 501,
        "stage": "campaign_created",
        "last_confirmed_stage": "campaign_post_accepted",
    }
    assert [path for path, _ in fake.posts] == ["/admin_api/v1/campaigns"]


def test_missing_campaign_rotation_is_partial() -> None:
    fake = FakeCreateKeitaro()
    fake.ignore_campaign_type = True
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert response.json()["creation"]["status"] == "PARTIAL"
    assert response.json()["creation"]["last_confirmed_stage"] == "campaign_post_accepted"
    assert len(fake.posts) == 1


def test_incorrect_campaign_post_rotation_is_partial_before_flow_writes() -> None:
    fake = FakeCreateKeitaro()
    fake.campaign_reply_type = "weight"
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert len(fake.posts) == 1
    assert response.json()["creation"]["stage"] == "campaign_created"


def test_definitively_rejected_second_write_is_partial_with_known_id() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_post_at = 2
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert response.json()["creation"] == {
        "status": "PARTIAL",
        "campaign_id": 501,
        "stage": "create_geo_redirect",
        "last_confirmed_stage": "campaign_readback_verified",
    }
    assert len(fake.posts) == 2
    assert all(request.method != "DELETE" for request in fake.requests)


def test_timeout_after_first_possible_mutation_is_unknown_without_repeat() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_transport_at = 1
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["creation"] == {
        "status": "UNKNOWN",
        "campaign_id": None,
        "stage": "create_campaign",
        "last_confirmed_stage": "none",
    }
    assert len(fake.posts) == 1


def test_timeout_after_possible_fallback_post_preserves_last_known_id() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_transport_at = 3
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["creation"] == {
        "status": "UNKNOWN",
        "campaign_id": 501,
        "stage": "create_offer_fallback",
        "last_confirmed_stage": "geo_post_accepted",
    }
    assert len(fake.posts) == 3
    assert not any(request.method == "DELETE" for request in fake.requests)


def test_failed_readback_is_unknown_even_if_all_posts_succeeded() -> None:
    fake = FakeCreateKeitaro()
    fake.readback_status = 503
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    # The first readback is deliberately required before any stream write.
    assert response.status_code == 502
    assert response.json()["creation"]["status"] == "UNKNOWN"
    assert response.json()["creation"]["last_confirmed_stage"] == "campaign_post_accepted"
    assert len(fake.posts) == 1


def test_unexpected_third_stream_never_signals_success() -> None:
    fake = FakeCreateKeitaro()
    fake.extra_stream = True
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert response.json()["creation"]["stage"] == "readback"
    assert len(fake.posts) == 3


def test_incomplete_streams_readback_never_signals_success() -> None:
    fake = FakeCreateKeitaro()
    fake.incomplete_streams = True
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["creation"]["status"] == "UNKNOWN"
    assert len(fake.posts) == 3


def test_preflight_rejects_bad_offer_and_defaults_without_post() -> None:
    for mode in ("missing_group", "offer_disabled", "offer_missing"):
        fake = FakeCreateKeitaro()
        if mode == "missing_group":
            fake.missing_group = True
        elif mode == "offer_disabled":
            fake.offer_state = "disabled"
        else:
            fake.fail_offer_status = 404
        with TestClient(fake_app(fake)) as client:
            response = create_request(client)
        assert response.status_code in (422, 503)
        assert fake.posts == []


def test_feature_gates_block_create_before_any_remote_request() -> None:
    for kwargs in ({"write": False}, {"mapping": False}):
        fake = FakeCreateKeitaro()
        with TestClient(fake_app(fake, **kwargs)) as client:
            response = create_request(client)
        assert response.status_code == 503
        assert fake.posts == []
        assert fake.requests == []


def test_first_campaign_write_http_422_is_not_reported_as_partial_creation() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_post_at = 1
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "create_rejected"
    assert "creation" not in response.json()
    assert len(fake.posts) == 1


def test_final_campaign_type_weight_is_partial_after_two_flow_writes() -> None:
    import httpx

    class WrongFinalRotation(FakeCreateKeitaro):
        def __init__(self) -> None:
            super().__init__()
            self.campaign_gets = 0

        def __call__(self, request: httpx.Request) -> httpx.Response:
            result = super().__call__(request)
            if request.url.path == "/admin_api/v1/campaigns/501" and result.status_code == 200:
                self.campaign_gets += 1
                if self.campaign_gets == 2:
                    value = result.json()
                    value["type"] = "weight"
                    return httpx.Response(200, json=value)
            return result

    fake = WrongFinalRotation()
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 409
    assert response.json()["creation"] == {
        "status": "PARTIAL",
        "campaign_id": 501,
        "stage": "readback",
        "last_confirmed_stage": "fallback_post_accepted",
    }
    assert len(fake.posts) == 3


def test_inconsistent_nested_stream_fields_never_succeed() -> None:
    import copy

    import httpx

    scenarios = {
        "wrong_geo": lambda data: data[0]["filters"][0].update(payload=["US"]),
        "wrong_redirect": lambda data: data[0].update(action_payload="PRIVATE-OTHER-URL"),
        "missing_type": lambda data: data[0].pop("type"),
        "wrong_fallback_type": lambda data: data[1].update(type="regular"),
        "wrong_fallback_action_type": lambda data: data[1].update(action_type="redirect"),
        "missing_fallback_action_type": lambda data: data[1].pop("action_type"),
        "extra_fallback_landing": lambda data: data[1].update(
            landings=[{"landing_id": 999, "share": 100, "state": "active"}]
        ),
        "missing_fallback_landings": lambda data: data[1].pop("landings"),
        "invalid_fallback_landings": lambda data: data[1].update(landings=None),
        "wrong_offer": lambda data: data[1]["offers"][0].update(offer_id=999),
        "wrong_share": lambda data: data[1]["offers"][0].update(share=50),
        "wrong_position": lambda data: data[0].update(position=3),
    }

    class WrongStream(FakeCreateKeitaro):
        def __init__(self, mutation: object) -> None:
            super().__init__()
            self.mutation = mutation

        def __call__(self, request: httpx.Request) -> httpx.Response:
            result = super().__call__(request)
            if (
                request.url.path == "/admin_api/v1/campaigns/501/streams"
                and result.status_code == 200
            ):
                data = copy.deepcopy(result.json())
                self.mutation(data)  # type: ignore[operator]
                return httpx.Response(200, json=data)
            return result

    for scenario, mutation in scenarios.items():
        fake = WrongStream(mutation)
        with TestClient(fake_app(fake)) as client:
            response = create_request(client)
        assert response.status_code == 409, scenario
        assert response.json()["creation"]["status"] == "PARTIAL", scenario
        assert response.json()["creation"]["campaign_id"] == 501, scenario
        assert response.json()["creation"]["stage"] == "readback", scenario
        assert response.json()["creation"]["last_confirmed_stage"] == "fallback_post_accepted", (
            scenario
        )
        assert "PRIVATE" not in response.text
        assert len(fake.posts) == 3


def test_fallback_action_type_is_required_in_final_readback() -> None:
    """The fallback action matters even when both positions and schemas match."""
    import copy

    import httpx

    class WrongAction(FakeCreateKeitaro):
        def __init__(self, bad_action: object) -> None:
            super().__init__()
            self.bad_action = bad_action

        def __call__(self, request: httpx.Request) -> httpx.Response:
            reply = super().__call__(request)
            if (
                request.method == "GET"
                and request.url.path == "/admin_api/v1/campaigns/501/streams"
                and reply.status_code == 200
            ):
                rows = copy.deepcopy(reply.json())
                if self.bad_action is None:
                    rows[1].pop("action_type")
                else:
                    rows[1]["action_type"] = self.bad_action
                return httpx.Response(200, json=rows)
            return reply

    for invalid in (None, "redirect", 123):
        fake = WrongAction(invalid)
        with TestClient(fake_app(fake)) as client:
            response = create_request(client)
        assert response.status_code == 409, invalid
        assert response.json()["error"]["code"] == "create_partial", invalid
        assert response.json()["creation"] == {
            "status": "PARTIAL",
            "campaign_id": 501,
            "stage": "readback",
            "last_confirmed_stage": "fallback_post_accepted",
        }, invalid
        assert len(fake.posts) == 3, invalid
