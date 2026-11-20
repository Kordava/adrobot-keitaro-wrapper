"""Logging only serializes explicitly approved diagnostic fields."""

import json
import logging

from adrobot.logging_setup import JsonFormatter


def test_formatter_omits_unknown_extras_and_exception_details() -> None:
    record = logging.LogRecord("adrobot", logging.WARNING, "", 0, "safe message", (), None)
    record.request_id = "request-1"
    record.campaign_id = 501
    record.stream_id = 52
    record.error_code = "keitaro_contract_error"
    record.failure_kind = "schema"
    record.failure_stage = "validate_streams"
    record.upstream_status = 200
    record.authorization = "SECRET-HEADER"
    record.payload = {"secret": "SECRET-BODY"}
    try:
        raise ValueError("SECRET-EXCEPTION")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    formatted = JsonFormatter().format(record)
    data = json.loads(formatted)
    assert "SECRET" not in formatted
    assert data["campaign_id"] == 501 and data["stream_id"] == 52
    assert data["failure_kind"] == "schema" and data["failure_stage"] == "validate_streams"
    assert data["error_code"] == "keitaro_contract_error" and data["upstream_status"] == 200
