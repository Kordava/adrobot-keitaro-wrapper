"""Minimal strict models for Keitaro Create preflight and readback.

Extra upstream data stays in the backend and is never returned by API handlers.
"""

from pydantic import BaseModel, ConfigDict, Field

from adrobot.keitaro.read_models import JS_SAFE_INTEGER


class CreatedEntity(BaseModel):
    """An upstream POST reply, not proof that the whole operation succeeded."""

    model_config = ConfigDict(extra="allow", strict=True)

    id: int = Field(gt=0, le=JS_SAFE_INTEGER)


class CampaignReadback(BaseModel):
    """All campaign properties required to assert verified success."""

    model_config = ConfigDict(extra="allow", strict=True)

    id: int = Field(gt=0, le=JS_SAFE_INTEGER)
    name: str = Field(min_length=1)
    alias: str = Field(min_length=1)
    type: str | None = None
    state: str
    domain_id: int = Field(gt=0, le=JS_SAFE_INTEGER)
    group_id: int = Field(gt=0, le=JS_SAFE_INTEGER)
    traffic_source_id: int = Field(gt=0, le=JS_SAFE_INTEGER)


class ActiveOffer(BaseModel):
    """Private validation record of one existing global offer."""

    model_config = ConfigDict(extra="allow", strict=True)

    id: int = Field(gt=0, le=JS_SAFE_INTEGER)
    name: str = Field(min_length=1)
    state: str


class ReferenceItem(BaseModel):
    """Private validation record of a selectable existing default."""

    model_config = ConfigDict(extra="allow", strict=True)

    id: int = Field(gt=0, le=JS_SAFE_INTEGER)
    name: str = Field(min_length=1)
    state: str
