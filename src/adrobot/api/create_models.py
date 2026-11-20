"""Public Create API types: never include an upstream record or credentials."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from adrobot.campaigns.countries import ISO_ALPHA2
from adrobot.keitaro.read_models import JS_SAFE_INTEGER


class CampaignCreateInput(BaseModel):
    """Three fields shown in the form; normalized before any remote request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120)
    geo: str = Field(min_length=2, max_length=256)
    offer_id: int = Field(gt=0, le=JS_SAFE_INTEGER)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or len(stripped) > 120:
            raise ValueError("Campaign name is required (max 120 characters)")
        return stripped

    @field_validator("geo")
    @classmethod
    def normalize_geo(cls, value: str) -> str:
        parts = [item.strip().upper() for item in value.split(",")]
        if not parts or any(part not in ISO_ALPHA2 for part in parts):
            raise ValueError("GEO must contain valid ISO country codes separated by commas")
        unique = list(dict.fromkeys(parts))
        if len(unique) > 30:
            raise ValueError("At most 30 GEO countries are supported")
        return ",".join(unique)

    @property
    def countries(self) -> list[str]:
        return self.geo.split(",")


class SafeOffer(BaseModel):
    id: int
    name: str


class OfferSearchResult(BaseModel):
    offers: list[SafeOffer]


class ReferenceData(BaseModel):
    available: bool
    message: str
    domain: str | None = None
    group: str | None = None
    traffic_source: str | None = None


class CreateSuccess(BaseModel):
    status: Literal["SUCCESS"] = "SUCCESS"
    campaign_id: int
    editor_url: str


class CreationOutcome(BaseModel):
    status: Literal["PARTIAL", "UNKNOWN"]
    campaign_id: int | None
    stage: str  # Attempted/failed step; NOT proof of completion.
    last_confirmed_stage: Literal[
        "none",
        "campaign_post_accepted",
        "campaign_readback_verified",
        "geo_post_accepted",
        "fallback_post_accepted",
    ]


class CreateErrorPayload(BaseModel):
    code: str
    message: str
    request_id: str


class CreateFailure(BaseModel):
    error: CreateErrorPayload
    creation: CreationOutcome
