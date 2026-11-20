"""Schema-only output must not expose upstream values or credentials."""

from adrobot.keitaro.inspect import describe_streams, describe_type


def test_types_and_bool_are_distinguished() -> None:
    assert describe_type(True) == "boolean"
    assert describe_type(5) == "number"
    assert describe_type(None) == "null"


def test_schema_diagnostic_does_not_emit_values() -> None:
    streams = [
        {
            "id": 74,
            "name": "PRIVATE STREAM NAME",
            "secret": "TOKEN-DONT-PRINT",
            "offers": [{"offer_id": 11, "share": 33, "private": "PRIVATE-OFFER-VALUE"}],
        },
        {"id": 75, "name": "Empty", "offers": []},
        {"id": 76, "name": "Unspecified"},
    ]
    result = describe_streams(streams)
    rendered = str(result)
    assert result["stream_count"] == 3
    assert result["streams"][0]["offer_fields"] == {
        "offer_id": "number",
        "private": "string",
        "share": "number",
    }
    assert result["streams"][1]["offers_count"] == 0
    assert result["streams"][2]["offers_field_type"] == "absent"
    for secret in ("PRIVATE STREAM NAME", "TOKEN-DONT-PRINT", "PRIVATE-OFFER-VALUE"):
        assert secret not in rendered


def test_inspection_uses_validated_client_and_closes_pool(monkeypatch, capsys) -> None:
    import httpx

    import adrobot.keitaro.inspect as inspector
    from adrobot.config import Settings

    clients = []
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 51,
                    "campaign_id": 501,
                    "name": "PRIVATE-NAME",
                    "position": 1,
                    "state": "active",
                    "schema": "landings",
                    "offers": [],
                    "filters": [],
                }
            ],
        )

    def client_factory(**kwargs):
        assert kwargs["verify"] is True and kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert all(value > 0 for value in kwargs["timeout"].as_dict().values())
        client = original_client(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(inspector.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        inspector,
        "get_settings",
        lambda: Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="SYNTHETIC-KEY",
            _env_file=None,
        ),
    )
    monkeypatch.setattr("sys.argv", ["inspect", "501"])
    assert inspector.main() == 0
    rendered = capsys.readouterr().out
    assert '"stream_count": 1' in rendered
    assert "PRIVATE-NAME" not in rendered and "SYNTHETIC-KEY" not in rendered
    assert len(requests) == len(clients) == 1 and clients[0].is_closed
    assert requests[0].method == "GET" and requests[0].headers["Api-Key"] == "SYNTHETIC-KEY"
