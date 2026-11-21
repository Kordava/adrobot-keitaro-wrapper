"""Strict internal DTOs for stateless editor Preview.

The IDs are logical draft item IDs, not Keitaro binding IDs. The B1 editor
Fetch maps each ID to global offer_id *within its stream*, rejecting concurrent
duplicate offers, and never treats these DTOs as evidence authorizing Push.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adrobot.keitaro.read_models import JS_SAFE_INTEGER, ReadonlyOffer

MAX_DRAFT_ITEMS = 2048

DraftId = Annotated[int, Field(strict=True, gt=0, le=JS_SAFE_INTEGER)]
RawShare = Annotated[int, Field(strict=True, ge=0, le=JS_SAFE_INTEGER)]
PinShare = Annotated[int, Field(strict=True, ge=1, le=100)]
DraftMode = Literal["SNAPSHOT", "RECALCULATED"]
ShareUnit = Literal["kt_raw", "percentage_points"]


class BaseOffer(BaseModel):
    """One active base binding and its untouched Keitaro raw share."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: DraftId
    raw_share: RawShare


class PreviewInput(BaseModel):
    """Complete, untrusted *calculation* input; no field authorizes writes.

    The two Pin maps are FULL state, not patches. JSON Pin-map keys are
    canonical decimal IDs; parsing them to integers is the sole permitted
    coercion because JSON object keys are strings by definition.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_revision: str = Field(min_length=1, max_length=256)
    base_pin_revision: str = Field(min_length=1, max_length=256)
    base_offers: list[BaseOffer] = Field(max_length=MAX_DRAFT_ITEMS)
    base_known_ids: list[DraftId] = Field(max_length=MAX_DRAFT_ITEMS)
    draft_known_ids: list[DraftId] = Field(max_length=MAX_DRAFT_ITEMS)
    base_pinned_shares: dict[DraftId, PinShare] = Field(max_length=MAX_DRAFT_ITEMS)
    active_ids: list[DraftId] = Field(max_length=MAX_DRAFT_ITEMS)
    pinned_shares: dict[DraftId, PinShare] = Field(max_length=MAX_DRAFT_ITEMS)
    mode: DraftMode

    @field_validator("base_pinned_shares", "pinned_shares", mode="before")
    @classmethod
    def parse_pin_keys(cls, value: object) -> dict[int, object]:
        if not isinstance(value, dict):
            raise ValueError("Pin settings must be a complete object")
        parsed: dict[int, object] = {}
        for key, share in value.items():
            if type(key) is int:
                item_id = key
            elif (
                type(key) is str
                and key.isascii()
                and key.isdecimal()
                and 1 <= len(key) <= 16
                and not key.startswith("0")
            ):
                item_id = int(key)
            else:
                raise ValueError("Pin keys must be canonical positive integer IDs")
            if not 1 <= item_id <= JS_SAFE_INTEGER:
                raise ValueError("Pin keys must be positive JS-safe integer IDs")
            if item_id in parsed:
                raise ValueError("Repeated logical Pin ID")
            parsed[item_id] = share
        return parsed

    @model_validator(mode="after")
    def check_relationships(self) -> Self:
        base_ids = [item.id for item in self.base_offers]
        for name, ids in (
            ("base_offers", base_ids),
            ("base_known_ids", self.base_known_ids),
            ("draft_known_ids", self.draft_known_ids),
            ("active_ids", self.active_ids),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} contains duplicate logical IDs")

        base_known = set(self.base_known_ids)
        draft_known = set(self.draft_known_ids)
        active = set(self.active_ids)
        if not set(base_ids) <= base_known:
            raise ValueError("Base active IDs must be known in the base snapshot")
        if self.draft_known_ids[: len(self.base_known_ids)] != self.base_known_ids:
            raise ValueError("Base known IDs must be the prefix of draft known IDs")
        if not active <= draft_known:
            raise ValueError("Active IDs must be included in draft known IDs")
        if not self.base_pinned_shares.keys() <= base_known:
            raise ValueError("Base Pin keys must belong to base known IDs")
        if not self.pinned_shares.keys() <= draft_known:
            raise ValueError("Pin keys must belong to draft known IDs")
        if any(
            item_id not in base_known and item_id not in active for item_id in self.pinned_shares
        ):
            raise ValueError("Removed staged-only IDs cannot retain a Pin")
        return self


class PreviewRow(BaseModel):
    """Active share or a visible Removed / 0% entry in known-item order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: DraftId
    status: Literal["Active", "Removed"]
    share: RawShare
    unit: ShareUnit
    pinned_share: PinShare | None


class PreviewResult(BaseModel):
    """Canonical calculation result, independent of path or HTTP transport."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_revision: str
    base_pin_revision: str
    mode: DraftMode
    dirty: bool
    active_ids: list[DraftId]
    pinned_shares: dict[DraftId, PinShare]
    share_unit: ShareUnit
    rows: list[PreviewRow]


class PreviewResponse(PreviewResult):
    """HTTP projection with validated route identifiers for correlation."""

    campaign_id: DraftId
    stream_id: DraftId


class KnownOfferRow(BaseModel):
    """One persisted row in display order; never a draft/remote binding patch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: DraftId
    offer_id: DraftId
    binding_id: DraftId | None
    last_binding_id: DraftId | None
    name: str = Field(min_length=1)
    status: Literal["Active", "Removed"]
    kt_state: Literal["active", "disabled"] | None
    last_seen_kt_state: Literal["active", "disabled"]
    display_share: RawShare
    display_share_unit: ShareUnit
    last_seen_raw_share: RawShare
    pinned_share: PinShare | None
    first_seen_at: str
    last_seen_at: str
    removed_at: str | None


class EditorStream(BaseModel):
    """Sanitized stream with its own independent nine-field Preview base."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: DraftId
    name: str
    position: int
    state: str
    schema_name: str = Field(alias="schema")
    kind: Literal["offers", "non_offer"]
    offers: list[ReadonlyOffer]
    editor: PreviewInput | None
    known_offers: list[KnownOfferRow]


class EditorSnapshot(BaseModel):
    """Successful archive-synchronized Fetch; never authority for a Keitaro Push."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: DraftId
    mapping_status: Literal["archive_synced"] = "archive_synced"
    local_archive_synced: Literal[True] = True
    keitaro_write_enabled: Literal[False] = False
    streams: list[EditorStream]
