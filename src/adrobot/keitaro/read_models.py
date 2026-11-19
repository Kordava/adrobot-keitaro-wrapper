"""Validated, read-only representations of the observed Keitaro stream JSON.

These models intentionally accept additional upstream fields. Only the explicit
public projection from :func:`build_readonly_snapshot` reaches the browser;
unknown fields and upstream secrets are not serialized into application APIs.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adrobot.keitaro.exceptions import KeitaroContractError


class OfferBinding(BaseModel):
    """One Keitaro stream-offer binding, distinct from the global offer."""

    model_config = ConfigDict(extra="allow", strict=True)

    id: int = Field(gt=0)
    stream_id: int = Field(gt=0)
    offer_id: int = Field(gt=0)
    state: str = Field(min_length=1)
    share: int = Field(ge=0)


class CampaignStream(BaseModel):
    """Critical fields required for a complete, safely classified stream read."""

    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)

    id: int = Field(gt=0)
    campaign_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    position: int
    state: str = Field(min_length=1)
    schema_name: str = Field(alias="schema", min_length=1)
    filters: list[dict[str, Any]]
    offers: list[OfferBinding]


class ReadonlyOffer(BaseModel):
    """Sanitized binding projection; `share` is Keitaro's raw integer value."""

    binding_id: int
    offer_id: int
    state: str
    share: int


class ReadonlyStream(BaseModel):
    """One stream in a read-only remote snapshot; not a draft or local archive."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    position: int
    state: str
    schema_name: str = Field(alias="schema")
    kind: Literal["offers", "non_offer"]
    offers: list[ReadonlyOffer]


class ReadonlySnapshot(BaseModel):
    """Temporary TASK-01 API response; TASK-03 will add local archive and Pin."""

    campaign_id: int
    mapping_status: Literal["remote_only"] = "remote_only"
    read_only: Literal[True] = True
    streams: list[ReadonlyStream]


def parse_streams(campaign_id: int, data: object) -> list[CampaignStream]:
    """Validate a *complete* upstream array before exposing any part of it.

    Unexpected or missing required fields, duplicate stream/binding identifiers,
    or cross-campaign references are integration errors, never empty results.
    No unique constraint is assumed for the global `offer_id` (OPEN-03).
    """
    if not isinstance(data, list):
        raise KeitaroContractError("Keitaro stream list has unexpected shape")

    streams: list[CampaignStream] = []
    stream_ids: set[int] = set()
    binding_ids: set[int] = set()
    for raw in data:
        try:
            stream = CampaignStream.model_validate(raw)
        except ValidationError as exc:
            raise KeitaroContractError("Keitaro stream record is incomplete") from exc
        if stream.campaign_id != campaign_id or stream.id in stream_ids:
            raise KeitaroContractError("Keitaro returned inconsistent stream identifiers")
        stream_ids.add(stream.id)
        for offer in stream.offers:
            if offer.stream_id != stream.id or offer.id in binding_ids:
                raise KeitaroContractError("Keitaro returned inconsistent binding identifiers")
            binding_ids.add(offer.id)
        streams.append(stream)
    return streams


def build_readonly_snapshot(campaign_id: int, streams: list[CampaignStream]) -> ReadonlySnapshot:
    """Expose a deliberately narrow, non-mutating, archive-free API projection."""
    return ReadonlySnapshot(
        campaign_id=campaign_id,
        streams=[
            ReadonlyStream(
                id=stream.id,
                name=stream.name,
                position=stream.position,
                state=stream.state,
                schema_name=stream.schema_name,
                kind="offers" if stream.schema_name == "landings" or stream.offers else "non_offer",
                offers=[
                    ReadonlyOffer(
                        binding_id=offer.id,
                        offer_id=offer.offer_id,
                        state=offer.state,
                        share=offer.share,
                    )
                    for offer in stream.offers
                ],
            )
            for stream in sorted(streams, key=lambda item: (item.position, item.id))
        ],
    )
