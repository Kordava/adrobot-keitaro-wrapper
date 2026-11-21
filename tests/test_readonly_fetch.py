"""Full Fetch boundary: transport, schema, projection, precision and safe diagnostics."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app

SAFE = 9007199254740991
SECRET = "PRIVATE-SENTINEL"


def payload() -> list[dict[str, Any]]:
    """Use unequal raw shares and a repeated global offer, never live data."""
    return [
        {
            "id": 52,
            "campaign_id": 501,
            "name": "<img src=x onerror=alert(1)>",
            "position": 1,
            "schema": "landings",
            "state": "active",
            "filters": [{"stream_id": 52, "payload": SECRET}],
            "offers": [
                {"id": 81, "stream_id": 52, "offer_id": 90, "state": "disabled", "share": 35},
                {"id": 82, "stream_id": 52, "offer_id": 90, "state": "active", "share": 165},
            ],
            "action_payload": SECRET,
        },
        {
            "id": 51,
            "campaign_id": 501,
            "name": "Redirect",
            "position": 1,
            "schema": "redirect",
            "state": "active",
            "filters": [],
            "offers": [],
        },
    ]


def fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    path: str = "501",
    *,
    route: str = "remote-snapshot",
) -> httpx.Response:
    app = create_app(
        Settings(keitaro_url="https://tracker.example", keitaro_api_key=SECRET, _env_file=None),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        return client.get(f"/api/campaigns/{path}/{route}")


def events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]


def test_projection_order_raw_values_and_correlated_success_logs(capsys) -> None:
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload())

    response = fetch(handler)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"campaign_id", "mapping_status", "read_only", "streams"}
    assert body["mapping_status"] == "remote_only" and body["read_only"] is True
    assert [s["id"] for s in body["streams"]] == [51, 52]
    assert [s["kind"] for s in body["streams"]] == ["non_offer", "offers"]
    assert body["streams"][1]["offers"] == [
        {"binding_id": 81, "offer_id": 90, "state": "disabled", "share": 35},
        {"binding_id": 82, "offer_id": 90, "state": "active", "share": 165},
    ]
    assert set(body["streams"][1]) == {
        "id",
        "name",
        "position",
        "schema",
        "state",
        "kind",
        "offers",
    }
    assert len(seen) == 1
    assert seen[0].method == "GET" and seen[0].url.path == "/admin_api/v1/campaigns/501/streams"
    assert seen[0].headers["Api-Key"] == SECRET and not seen[0].url.query
    logs = events(capsys)
    assert SECRET not in json.dumps(logs) + response.text
    operation = [row for row in logs if row["action"] == "readonly_fetch"]
    assert [row["stage"] for row in operation] == ["start", "complete"]
    assert all(
        row["request_id"] == response.headers["x-request-id"] for row in logs if row["request_id"]
    )
    assert operation[1]["stream_ids"] == [51, 52]
    assert [operation[1][k] for k in ["stream_count", "offer_stream_count", "binding_count"]] == [
        2,
        1,
        2,
    ]
    assert operation[1]["duration_ms"] >= 0


@pytest.mark.parametrize(
    "status,expected,code",
    [
        (100, 502, "keitaro_contract_error"),
        (201, 502, "keitaro_contract_error"),
        (204, 502, "keitaro_contract_error"),
        (206, 502, "keitaro_contract_error"),
        (299, 502, "keitaro_contract_error"),
        (300, 503, "keitaro_unavailable"),
        (302, 503, "keitaro_unavailable"),
        (304, 503, "keitaro_unavailable"),
        (307, 503, "keitaro_unavailable"),
        (401, 502, "keitaro_unauthorized"),
        (403, 502, "keitaro_unauthorized"),
        (404, 404, "keitaro_not_found"),
        (422, 502, "keitaro_upstream_validation_error"),
        (429, 503, "keitaro_rate_limited"),
        (400, 503, "keitaro_unavailable"),
        (500, 503, "keitaro_unavailable"),
        (503, 503, "keitaro_unavailable"),
    ],
)
def test_http_errors_never_decode_as_success(status, expected, code, capsys) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json=payload(), headers={"Location": f"https://{SECRET}.invalid"}
        )

    response = fetch(handler)
    assert response.status_code == expected
    assert response.json()["error"]["code"] == code
    assert len(calls) == 1
    assert response.headers["cache-control"] == "no-store"
    logs = events(capsys)
    assert SECRET not in response.text + json.dumps(logs)
    operation = [row for row in logs if row["action"] == "readonly_fetch"]
    assert [row["stage"] for row in operation] == ["start", "failed"]
    failure = operation[1]
    assert (
        failure["request_id"]
        == response.json()["error"]["request_id"]
        == response.headers["x-request-id"]
    )
    assert failure["campaign_id"] == 501 and failure["upstream_status"] == status
    assert failure["failure_kind"] == "http" and failure["failure_stage"] == "upstream_read"
    assert failure["error_code"] == code and "stream_id" not in failure


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Range": "items 0-1/3"},
        {"Link": '<https://private.invalid/next>; rel="next"'},
        {"Link": '<https://private.invalid/next>; rel="prev next"'},
    ],
)
def test_partial_markers_fail_closed(headers, capsys) -> None:
    response = fetch(lambda _: httpx.Response(200, json=[], headers=headers))
    assert response.status_code == 502
    failure = [e for e in events(capsys) if e["stage"] == "failed"][0]
    assert failure["failure_kind"] == "completeness"


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b'[{"name":"\xff"}]',
        b"[NaN]",
        b"[Infinity]",
        b"[-Infinity]",
        b'[{"unused":NaN}]',
        b'{"streams":[]}',
    ],
)
def test_bad_json_and_shape_are_not_empty_success(content) -> None:
    response = fetch(lambda _: httpx.Response(200, content=content))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "keitaro_contract_error"


@pytest.mark.parametrize(
    "exception,kind", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")]
)
def test_transport_diagnostics_do_not_leak(exception, kind, capsys) -> None:
    def handler(request):
        raise exception(SECRET, request=request)

    response = fetch(handler)
    assert response.status_code == 503
    logs = events(capsys)
    failure = [e for e in logs if e["stage"] == "failed"][0]
    assert failure["failure_kind"] == kind and failure["failure_stage"] == "upstream_read"
    assert "upstream_status" not in failure
    assert SECRET not in response.text + json.dumps(logs)


@pytest.mark.parametrize(
    "schema,with_offers,status,kind",
    [
        ("landings", True, 200, "offers"),
        ("landings", False, 200, "offers"),
        ("redirect", True, 502, None),
        ("redirect", False, 200, "non_offer"),
        ("unknown", True, 502, None),
        ("unknown", False, 200, "non_offer"),
    ],
)
def test_schema_and_bindings_are_validated_together(schema, with_offers, status, kind) -> None:
    data = payload()[:1]
    data[0]["schema"] = schema
    if not with_offers:
        data[0]["offers"] = []
    response = fetch(lambda _: httpx.Response(200, json=data))
    assert response.status_code == status
    if status == 200:
        assert response.json()["streams"][0]["kind"] == kind
        assert response.json()["streams"][0]["schema"] == schema


@pytest.mark.parametrize("filter_id", [None, False, "52", 0, -1, 51, SAFE + 1])
def test_filter_parent_reference_is_strict(filter_id) -> None:
    data = payload()
    data[0]["filters"] = [{"stream_id": filter_id}]
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", SAFE + 1),
        ("id", "52"),
        ("campaign_id", True),
        ("position", SAFE + 1),
        ("position", -SAFE - 1),
        ("position", False),
        ("name", ""),
        ("state", None),
        ("schema", ""),
        ("filters", None),
        ("offers", {}),
    ],
)
def test_corrupt_record_among_valid_records_fails_whole_fetch(field, value, capsys) -> None:
    data = payload()
    data[0][field] = value
    response = fetch(lambda _: httpx.Response(200, json=data))
    assert response.status_code == 502 and "streams" not in response.json()
    failed = [e for e in events(capsys) if e["stage"] == "failed"][0]
    assert failed["failure_kind"] == "schema" and failed["failure_stage"] == "validate_streams"
    if field == "id":
        assert "stream_id" not in failed
    else:
        assert failed["stream_id"] == 52


@pytest.mark.parametrize(
    "field", ["id", "campaign_id", "name", "position", "state", "schema", "filters", "offers"]
)
def test_missing_critical_stream_fields(field) -> None:
    data = payload()
    del data[0][field]
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", SAFE + 1),
        ("stream_id", SAFE + 1),
        ("offer_id", SAFE + 1),
        ("share", SAFE + 1),
        ("share", -1),
        ("share", True),
        ("share", 1.5),
        ("offer_id", False),
        ("stream_id", 51),
        ("state", ""),
    ],
)
def test_invalid_nested_binding_fails_whole_fetch(field, value) -> None:
    data = payload()
    data[0]["offers"][1][field] = value
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502


@pytest.mark.parametrize("field", ["id", "stream_id", "offer_id", "state", "share"])
def test_missing_binding_fields(field) -> None:
    data = payload()
    del data[0]["offers"][1][field]
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502


def test_safe_integer_extremes_and_optional_filters() -> None:
    data = payload()[:1]
    data[0].update(id=SAFE, campaign_id=SAFE, position=-SAFE, filters=[{"payload": SECRET}])
    data[0]["offers"] = [
        {"id": SAFE, "stream_id": SAFE, "offer_id": SAFE, "state": "active", "share": SAFE}
    ]
    response = fetch(lambda _: httpx.Response(200, json=data), str(SAFE))
    assert response.status_code == 200
    stream = response.json()["streams"][0]
    assert stream["id"] == SAFE and stream["position"] == -SAFE
    assert stream["offers"][0] == {
        "binding_id": SAFE,
        "offer_id": SAFE,
        "state": "active",
        "share": SAFE,
    }
    data[0]["position"] = SAFE
    data[0]["offers"][0]["share"] = 0
    assert fetch(lambda _: httpx.Response(200, json=data), str(SAFE)).status_code == 200


def test_valid_empty_array_and_no_next_link() -> None:
    response = fetch(
        lambda _: httpx.Response(
            200, json=[], headers={"Link": '<https://private.invalid>; rel="prev"'}
        )
    )
    assert response.json()["streams"] == [] and response.status_code == 200


@pytest.mark.parametrize("campaign", ["0", "-1", str(SAFE + 1), "not-an-id"])
def test_invalid_local_input_never_calls_upstream(campaign) -> None:
    def unexpected(_):
        pytest.fail("Invalid local input reached upstream")

    response = fetch(unexpected, campaign)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_binding_duplicates_across_streams_fail() -> None:
    data = payload()
    data[1].update(schema="landings", offers=[{**data[0]["offers"][0], "stream_id": 51}])
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502


def test_lifespan_reuses_and_closes_transport_and_missing_config_is_diagnostic(capsys) -> None:
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.calls = 0
            self.closed = False

        async def handle_async_request(self, request):
            self.calls += 1
            return httpx.Response(200, json=[])

        async def aclose(self):
            self.closed = True

    transport = Transport()
    app = create_app(
        Settings(keitaro_url="https://tracker.example", keitaro_api_key=SECRET, _env_file=None),
        keitaro_transport=transport,
    )
    with TestClient(app) as client:
        upstream = app.state.keitaro
        assert client.get("/api/campaigns/501/remote-snapshot").status_code == 200
        assert client.get("/api/campaigns/501/remote-snapshot").status_code == 200
        assert app.state.keitaro is upstream
        assert transport.calls == 2 and not transport.closed
        assert "cache-control" not in client.get("/health").headers
    assert transport.closed
    events(capsys)
    app = create_app(Settings(keitaro_url=None, keitaro_api_key=SECRET, _env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/remote-snapshot")
    assert response.status_code == 503
    logs = events(capsys)
    failure = [e for e in logs if e["stage"] == "failed"][0]
    assert failure["failure_kind"] == "configuration" and failure["failure_stage"] == "config"
    assert "stream_id" not in failure and "upstream_status" not in failure
    assert SECRET not in json.dumps(logs) + response.text


@pytest.mark.parametrize("campaign", [True, 0, -1, SAFE + 1, "501"])
def test_adapter_rejects_invalid_campaign_without_transport(campaign) -> None:
    import asyncio

    from adrobot.keitaro.client import KeitaroClient

    async def run():
        def unexpected(_):
            pytest.fail("Invalid adapter input reached upstream")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as transport:
            with pytest.raises(ValueError, match="positive safe integer"):
                await KeitaroClient(transport).get_campaign_streams(campaign)

    asyncio.run(run())


def test_internal_model_name_does_not_replace_required_upstream_schema() -> None:
    data = payload()
    data[0]["schema_name"] = data[0].pop("schema")
    assert fetch(lambda _: httpx.Response(200, json=data)).status_code == 502
    data[0]["schema"] = "landings"
    data[0]["schema_name"] = "redirect"
    response = fetch(lambda _: httpx.Response(200, json=data))
    assert response.status_code == 200
    assert response.json()["streams"][1]["schema"] == "landings"
    assert response.json()["streams"][1]["kind"] == "offers"


@pytest.mark.parametrize("route", ["remote-snapshot"])
def test_diagnostic_preserves_strict_remote_mapping(route: str) -> None:
    response = fetch(lambda _: httpx.Response(200, json=payload()), route=route)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert response.json()["mapping_status"] == "remote_only"
    assert response.json()["read_only"] is True
    assert response.json()["streams"][1]["offers"] == [
        {"binding_id": 81, "offer_id": 90, "state": "disabled", "share": 35},
        {"binding_id": 82, "offer_id": 90, "state": "active", "share": 165},
    ]
    assert SECRET not in response.text


@pytest.mark.parametrize("route", ["remote-snapshot"])
@pytest.mark.parametrize(
    "upstream",
    [
        lambda: httpx.Response(200, json={"streams": []}),
        lambda: httpx.Response(200, json=payload(), headers={"Content-Range": "items 0-1/3"}),
        lambda: httpx.Response(200, text="not JSON"),
        lambda: httpx.Response(401, text=SECRET),
    ],
)
def test_diagnostic_fail_closed_for_bad_or_partial_remote(
    route: str, upstream: Callable[[], httpx.Response]
) -> None:
    response = fetch(lambda _: upstream(), route=route)
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert "streams" not in response.json()
    assert SECRET not in response.text
