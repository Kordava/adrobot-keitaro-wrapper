"""Small structured logger with stable fields and safe redaction boundaries."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Serialize diagnostic records without logging headers or bodies."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "action": getattr(record, "action", record.name),
            "stage": getattr(record, "stage", ""),
            "request_id": getattr(record, "request_id", ""),
            "message": record.getMessage(),
        }
        for key in ("status_code", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str) -> None:
    """Configure stdout logging; secrets must never be put in log fields."""
    import sys

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
