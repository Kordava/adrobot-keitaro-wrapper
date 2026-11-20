"""Validated process configuration. Secrets never leave the backend."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-owned application settings, without hard-coded credentials."""

    model_config = SettingsConfigDict(
        env_prefix="ADROBOT_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    keitaro_url: str | None = None
    keitaro_api_key: SecretStr | None = None
    database_url: str = "sqlite:///./adrobot.db"
    log_level: str = "INFO"

    @field_validator("keitaro_url")
    @classmethod
    def validate_keitaro_url(cls, value: str | None) -> str | None:
        """Accept a fixed HTTPS origin, not a caller-selected endpoint."""
        if value is None or value.strip() == "":
            return None
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            port = parsed.port
            hostname = parsed.hostname
        except ValueError:
            raise ValueError("Keitaro URL must be a clean HTTPS origin") from None
        if (
            parsed.scheme != "https"
            or not hostname
            or (port is not None and port < 1)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Keitaro URL must be a clean HTTPS origin")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Limit logging configuration to supported, safe level names."""
        level = value.upper()
        if level not in {
            "CRITICAL",
            "FATAL",
            "ERROR",
            "WARNING",
            "WARN",
            "INFO",
            "DEBUG",
            "NOTSET",
        }:
            raise ValueError("Invalid logging level")
        return level

    @property
    def keitaro_configured(self) -> bool:
        """Whether both upstream connection prerequisites are configured."""
        return self.keitaro_url is not None and self.keitaro_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Parse application settings once per process."""
    return Settings()
