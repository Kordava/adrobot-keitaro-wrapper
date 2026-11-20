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

    # Optional, deliberately disabled write functionality. This does not affect
    # health or existing read-only Fetch when local discovery is incomplete.
    create_domain_id: int | None = None
    create_group_id: int | None = None
    create_traffic_source_id: int | None = None
    create_offer_action_type: str | None = None
    create_mapping_verified: bool = False
    create_writes_enabled: bool = False

    @field_validator("create_domain_id", "create_group_id", "create_traffic_source_id")
    @classmethod
    def validate_create_id(cls, value: int | None) -> int | None:
        from adrobot.keitaro.read_models import JS_SAFE_INTEGER

        if value is not None and (type(value) is not int or not 1 <= value <= JS_SAFE_INTEGER):
            raise ValueError("Create default must be a positive safe integer")
        return value

    @field_validator("create_offer_action_type")
    @classmethod
    def validate_offer_action(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not value.isascii() or not value.replace("_", "").isalnum() or len(value) > 40:
            raise ValueError("Invalid Create offer action type")
        return value

    @property
    def create_defaults_configured(self) -> bool:
        return all(
            item is not None
            for item in (
                self.create_domain_id,
                self.create_group_id,
                self.create_traffic_source_id,
                self.create_offer_action_type,
            )
        )

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
