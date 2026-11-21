"""Public app contract and safety regressions for Create UI/API."""

from create_fakes import FakeCreateKeitaro, create_request, fake_app
from fastapi.testclient import TestClient


def test_public_search_returns_only_id_name_and_is_case_insensitive() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = client.get("/api/offers?query=approved")
        exact = client.get("/api/offers?query=42")
        invalid = client.get("/api/offers?query=999")
    assert response.status_code == 200
    assert response.json() == {"offers": [{"id": 42, "name": "Approved Offer"}]}
    assert exact.json() == response.json()
    assert invalid.json() == {"offers": []}
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text
    assert fake.posts == []


def test_reference_data_uses_actual_default_labels() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = client.get("/api/reference-data")
    assert response.json() == {
        "available": True,
        "message": "Create references are valid; remote writes require separate authorization",
        "domain": "Test Domain",
        "group": "Test Group",
        "traffic_source": "Test Source",
    }
    assert "PRIVATE" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert fake.posts == []


def test_invalid_create_inputs_do_not_reach_keitaro_and_are_not_echoed() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        for json_data in (
            {"name": " ", "geo": "AU", "offer_id": 42},
            {"name": "X", "geo": "INVALID", "offer_id": 42},
            {"name": "X", "geo": "AU", "offer_id": "42"},
            {"name": "X", "geo": "AU", "offer_id": 42, "sensitive": "PRIVATE"},
        ):
            response = client.post("/api/campaigns", json=json_data)
            assert response.status_code == 422
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()["error"]["code"] == "invalid_create_input"
            assert "PRIVATE" not in response.text
        response = client.post("/api/campaigns", content="NOT JSON")
        assert response.status_code == 422
        assert client.get("/api/offers?query=" + "x" * 101).status_code == 422
    assert fake.requests == []


def test_cross_site_create_post_is_rejected() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = client.post(
            "/api/campaigns",
            headers={"Origin": "https://foreign.example"},
            json={"name": "X", "geo": "AU", "offer_id": 42},
        )
    assert response.status_code == 403
    assert fake.requests == []


def test_editor_link_selects_campaign_and_bare_editor_remains_manual() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
        url = response.json()["editor_url"]
        editor = client.get(url)
        plain = client.get("/editor")
    assert url == "/editor?campaign_id=501"
    assert editor.status_code == 200
    assert 'value="501" data-autofetch="true"' in editor.text
    assert 'data-autofetch="true"' not in plain.text
    assert 'value="501"' not in plain.text


def test_invalid_editor_id_is_rejected_without_keitaro_get() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        for raw in ("0", "-1", "abc", "9007199254740992", "1<script>", "1.5", "١"):
            response = client.get("/editor", params={"campaign_id": raw})
            assert response.status_code == 422
    assert fake.requests == []


def test_existing_readonly_fetch_is_unaffected_and_safe() -> None:
    fake = FakeCreateKeitaro()
    fake.streams = [
        {
            "id": 2001,
            "campaign_id": 501,
            "name": "Offers",
            "position": 1,
            "schema": "landings",
            "state": "active",
            "filters": [],
            "offers": [
                {"id": 777, "stream_id": 2001, "offer_id": 42, "state": "active", "share": 100}
            ],
            "action_payload": "PRIVATE-REDIRECT",
        }
    ]
    with TestClient(fake_app(fake, write=False, mapping=False)) as client:
        diagnostic = client.get("/api/campaigns/501/remote-snapshot")
    assert diagnostic.status_code == 200
    assert diagnostic.json()["mapping_status"] == "remote_only"
    assert diagnostic.json()["streams"][0]["offers"][0]["share"] == 100
    assert "PRIVATE" not in diagnostic.text
    assert fake.posts == []


def test_create_result_never_contains_secret_or_upstream_redirect() -> None:
    fake = FakeCreateKeitaro()
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 201
    assert set(response.json()) == {"status", "campaign_id", "editor_url"}
    assert "SYNTHETIC-SECRET-KEY" not in response.text
    assert "PRIVATE" not in response.text
    assert "https://google.com" not in response.text


def test_partial_response_separates_attempted_and_last_confirmed_stage() -> None:
    """A failed fallback POST must not be reported as a confirmed fallback."""
    fake = FakeCreateKeitaro()
    fake.fail_post_at = 3
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)

    assert response.status_code == 409
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert response.json()["creation"] == {
        "status": "PARTIAL",
        "campaign_id": 501,
        "stage": "create_offer_fallback",
        "last_confirmed_stage": "geo_post_accepted",
    }
    assert "SYNTHETIC-SECRET-KEY" not in response.text
    assert "PRIVATE" not in response.text
    assert len(fake.posts) == 3
