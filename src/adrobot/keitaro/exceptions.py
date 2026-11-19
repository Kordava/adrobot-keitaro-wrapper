"""Safe, typed upstream errors; no response bodies or credentials in user messages."""


class KeitaroError(Exception):
    """Base class for expected integration failures."""

    code = "keitaro_error"
    status_code = 502


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
