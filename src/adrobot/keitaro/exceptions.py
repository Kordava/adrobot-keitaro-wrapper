"""Safe, typed upstream errors; no response bodies or credentials in user messages."""

from typing import Literal

FailureKind = Literal[
    "configuration", "http", "timeout", "network", "json", "schema", "completeness"
]
FailureStage = Literal["config", "upstream_read", "decode_json", "validate_streams"]


class KeitaroError(Exception):
    """Base class for expected integration failures."""

    code = "keitaro_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        failure_kind: FailureKind = "http",
        failure_stage: FailureStage = "upstream_read",
        stream_id: int | None = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.failure_stage = failure_stage
        self.stream_id = stream_id
        self.upstream_status = upstream_status


class KeitaroNotConfigured(KeitaroError):
    """The app has no configured upstream credentials."""

    code = "keitaro_not_configured"
    status_code = 503


class KeitaroUnavailable(KeitaroError):
    """The remote tracker did not respond reliably."""

    code = "keitaro_unavailable"
    status_code = 503


class KeitaroUnauthorized(KeitaroError):
    """Configured credentials are missing remote authorization."""

    code = "keitaro_unauthorized"
    status_code = 502


class KeitaroNotFound(KeitaroError):
    """The requested remote campaign does not exist."""

    code = "keitaro_not_found"
    status_code = 404


class KeitaroRateLimited(KeitaroError):
    """The tracker refused the request due to a rate limit."""

    code = "keitaro_rate_limited"
    status_code = 503


class KeitaroContractError(KeitaroError):
    """Upstream returned an unrecognized or incomplete response."""

    code = "keitaro_contract_error"
    status_code = 502


class KeitaroUpstreamValidationError(KeitaroError):
    """Remote validation failed, distinct from invalid local campaign input."""

    code = "keitaro_upstream_validation_error"
    status_code = 502
