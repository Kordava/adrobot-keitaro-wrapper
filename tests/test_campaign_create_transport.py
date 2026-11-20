"""HTTP transport error boundaries for definite failures and unknown writes."""

from create_fakes import FakeCreateKeitaro, create_request, fake_app
from fastapi.testclient import TestClient


def test_unparseable_campaign_post_reply_is_unknown_and_not_retried() -> None:
    fake = FakeCreateKeitaro()
    fake.malformed_post_at = 1
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["creation"]["status"] == "UNKNOWN"
    assert len(fake.posts) == 1


def test_remote_500_might_have_committed_and_is_unknown() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_post_at = 2
    fake.fail_post_status = 500
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 502
    assert response.json()["creation"] == {
        "status": "UNKNOWN",
        "campaign_id": 501,
        "stage": "create_geo_redirect",
        "last_confirmed_stage": "campaign_readback_verified",
    }
    assert len(fake.posts) == 2


def test_all_mutating_requests_are_only_post_to_known_endpoints() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 201
    writes = [item for item in fake.requests if item.method != "GET"]
    assert len(writes) == 3
    assert [item.method for item in writes] == ["POST", "POST", "POST"]
    assert [item.url.path for item in writes] == [
        "/admin_api/v1/campaigns",
        "/admin_api/v1/streams",
        "/admin_api/v1/streams",
    ]
    assert not any("Api-Key" in item.url.params for item in writes)


def test_offer_lookup_timeout_is_error_not_false_empty_result() -> None:
    import httpx

    from adrobot.config import Settings
    from adrobot.main import create_app

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic-only timeout")

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example", keitaro_api_key="SYNTHETIC", _env_file=None
        ),
        keitaro_transport=httpx.MockTransport(timeout),
    )
    with TestClient(app) as client:
        result = client.get("/api/offers?query=42")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "keitaro_unavailable"
    assert result.headers["Cache-Control"] == "no-store"


def test_async_202_reply_without_confirmed_created_id_is_unknown() -> None:
    fake = FakeCreateKeitaro()
    fake.fail_post_at = 1
    fake.fail_post_status = 202
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
