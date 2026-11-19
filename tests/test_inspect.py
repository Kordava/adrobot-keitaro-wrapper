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
