"""Campaign group wire validation through the real Create API and service."""

import httpx
import pytest
from create_fakes import FakeCreateKeitaro, create_request, fake_app
from fastapi.testclient import TestClient

GROUP = {"id": 20, "name": "Test Group", "type": "campaigns", "position": 0}


class GroupReplyKeitaro(FakeCreateKeitaro):
    def __init__(self, reply: httpx.Response | httpx.TransportError) -> None:
        super().__init__()
        self.reply = reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin_api/v1/groups":
            self.requests.append(request)
            assert request.method == "GET"
            assert list(request.url.params.multi_items()) == [("type", "campaigns")]
            if isinstance(self.reply, httpx.TransportError):
                raise self.reply
            return self.reply
        return super().__call__(request)


def test_group_without_state_is_accepted_for_labels_and_create() -> None:
    fake = GroupReplyKeitaro(httpx.Response(200, json=[GROUP]))
    with TestClient(fake_app(fake)) as client:
        labels = client.get("/api/reference-data")
        response = create_request(client)
    assert labels.status_code == 200
    assert labels.json()["group"] == "Test Group"
    assert response.status_code == 201
    assert response.json()["status"] == "SUCCESS"
    assert len(fake.posts) == 3
    assert fake.posts[0][1]["group_id"] == "20"
    assert not any(item.url.path.endswith("/campaign_groups") for item in fake.requests)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{**GROUP, "id": 21}],
        [{**GROUP, "type": "offers"}],
        [{key: value for key, value in GROUP.items() if key != "type"}],
        [{key: value for key, value in GROUP.items() if key != "id"}],
        [{key: value for key, value in GROUP.items() if key != "name"}],
        *[[{**GROUP, "id": value}] for value in (0, -1, True, "20", 20.0, 2**53, None)],
        *[[{**GROUP, "name": value}] for value in ("", 20, None)],
        [GROUP, GROUP],
        [GROUP, {**GROUP, "id": 21, "type": "offers"}],
        [None],
        {"data": [GROUP]},
        None,
    ],
)
def test_invalid_group_catalog_prevents_any_write(payload: object) -> None:
    fake = GroupReplyKeitaro(httpx.Response(200, json=payload))
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code in (502, 503)
    assert fake.posts == []
    assert all(request.method == "GET" for request in fake.requests)


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (httpx.Response(200, text="broken-json"), 502),
        (httpx.Response(200, text='[{"id":NaN}]'), 502),
        (httpx.Response(200, json=[GROUP], headers={"Content-Range": "items 0-0/2"}), 502),
        (
            httpx.Response(200, json=[GROUP], headers={"Link": '</groups?page=2>; rel="next"'}),
            502,
        ),
        *[
            (httpx.Response(code), expected)
            for code, expected in (
                (401, 502),
                (403, 502),
                (404, 404),
                (422, 502),
                (429, 503),
                (500, 503),
                (206, 503),
                (302, 503),
            )
        ],
        (httpx.ReadTimeout("synthetic timeout"), 503),
        (httpx.ConnectError("synthetic network error"), 503),
    ],
)
def test_failed_or_incomplete_group_read_prevents_any_write(
    reply: httpx.Response | httpx.TransportError, status: int
) -> None:
    fake = GroupReplyKeitaro(reply)
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == status
    assert fake.posts == []
    assert all(request.method == "GET" for request in fake.requests)


@pytest.mark.parametrize(("count", "status"), [(20000, 201), (20001, 502)])
def test_group_catalog_size_boundary(count: int, status: int) -> None:
    fake = GroupReplyKeitaro(
        httpx.Response(200, json=[{**GROUP, "id": value} for value in range(1, count + 1)])
    )
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == status
    assert len(fake.posts) == (3 if status == 201 else 0)


@pytest.mark.parametrize("resource", ["domains", "traffic_sources"])
def test_inactive_stateful_default_still_prevents_create(resource: str) -> None:
    fake = FakeCreateKeitaro()
    fake.inactive_reference = f"/admin_api/v1/{resource}"
    with TestClient(fake_app(fake)) as client:
        response = create_request(client)
    assert response.status_code == 503
    assert fake.posts == []
