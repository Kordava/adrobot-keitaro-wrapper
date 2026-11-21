"""Atomic, fail-closed synchronization of a complete Keitaro campaign archive."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry

from adrobot.db.models import KnownOfferBinding
from adrobot.db.sqlite_urls import runtime_sqlite_url
from adrobot.editor.schemas import (
    BaseOffer,
    EditorSnapshot,
    EditorStream,
    KnownOfferRow,
    PreviewInput,
)
from adrobot.editor.service import EditorProblem, RemoteCampaign
from adrobot.keitaro.read_models import CampaignStream, ReadonlyOffer

EXPECTED_REVISION = "a001_known_offer_bindings"
MAX_PREVIEW_BYTES = 131072


@dataclass(frozen=True)
class History:
    """Expected archive row after a successful Fetch, not yet attached to ORM."""

    campaign_id: int
    stream_id: int
    offer_id: int
    last_binding_id: int | None
    offer_name: str
    pinned_share: int | None
    last_seen_share: int
    last_seen_kt_state: str
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None


def create_archive_engine(database_url: str) -> Engine:
    """Build a lazy, never-create SQLite engine using physical BEGIN IMMEDIATE."""
    engine = create_engine(runtime_sqlite_url(database_url), connect_args={"timeout": 1.0})

    @event.listens_for(engine, "connect")
    def sqlite_connect(connection: sqlite3.Connection, _record: ConnectionPoolEntry) -> None:
        connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def sqlite_begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def _utc_string(value: datetime | None) -> str | None:
    return None if value is None else value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(prefix: str, payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pin_revision(campaign_id: int, stream_id: int, history: list[History]) -> str:
    """Identity-independent canonical SHA of all known offer IDs and nullable Pin."""
    pairs = [[r.offer_id, r.pinned_share] for r in sorted(history, key=lambda r: r.offer_id)]
    return _fingerprint(
        "pin-v1",
        {"version": 1, "campaign_id": campaign_id, "stream_id": stream_id, "pins": pairs},
    )


def _remote_revision(campaign_id: int, stream: CampaignStream) -> str:
    bindings = [[item.offer_id, item.id, item.state, item.share] for item in stream.offers]
    return _fingerprint(
        "v1",
        {
            "version": 1,
            "campaign_id": campaign_id,
            "stream_id": stream.id,
            "position": stream.position,
            "schema": stream.schema_name,
            "bindings": bindings,
        },
    )


def _state(row: KnownOfferBinding) -> History:
    return History(
        campaign_id=row.campaign_id,
        stream_id=row.stream_id,
        offer_id=row.offer_id,
        last_binding_id=row.last_binding_id,
        offer_name=row.offer_name,
        pinned_share=row.pinned_share,
        last_seen_share=row.last_seen_share,
        last_seen_kt_state=row.last_seen_kt_state,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        removed_at=row.removed_at,
    )


def _propose(
    remote: RemoteCampaign, existing: list[History], now: datetime
) -> dict[tuple[int, int], list[History]]:
    """Compute every stream's after-image WITHOUT ORM mutation or autoflush."""
    old = {(row.stream_id, row.offer_id): row for row in existing}
    current = {stream.id: stream for stream in remote.streams if stream.schema_name == "landings"}
    for stream_id in {row.stream_id for row in existing}:
        if stream_id not in current:
            remote_stream = next((s for s in remote.streams if s.id == stream_id), None)
            if remote_stream is None:
                raise EditorProblem("editor_known_stream_missing", "A known stream is missing")
            raise EditorProblem(
                "editor_known_stream_type_changed", "A known stream has changed type"
            )

    result: dict[tuple[int, int], list[History]] = {}
    for stream in remote.streams:
        if stream.schema_name != "landings":
            continue
        live_ids = {offer.offer_id for offer in stream.offers}
        updated: list[History] = []
        for binding in stream.offers:
            prev = old.get((stream.id, binding.offer_id))
            if prev is None:
                row = History(
                    campaign_id=remote.campaign_id,
                    stream_id=stream.id,
                    offer_id=binding.offer_id,
                    last_binding_id=binding.id,
                    offer_name=remote.names[binding.offer_id],
                    pinned_share=None,
                    last_seen_share=binding.share,
                    last_seen_kt_state=binding.state,
                    first_seen_at=now,
                    last_seen_at=now,
                    removed_at=None,
                )
            else:
                row = replace(
                    prev,
                    last_binding_id=binding.id,
                    offer_name=remote.names[binding.offer_id],
                    last_seen_share=binding.share,
                    last_seen_kt_state=binding.state,
                    last_seen_at=now,
                    removed_at=None,
                )
            updated.append(row)
        for prev in existing:
            if prev.stream_id == stream.id and prev.offer_id not in live_ids:
                removed_at = prev.removed_at if prev.removed_at is not None else now
                updated.append(replace(prev, removed_at=removed_at))
        result[(remote.campaign_id, stream.id)] = updated
    return result


def _editor_stream(
    remote: RemoteCampaign,
    stream: CampaignStream,
    history: list[History],
) -> EditorStream:
    """Return a safe display projection plus exactly nine Stage A input fields."""
    bindings = [
        ReadonlyOffer(
            binding_id=item.id, offer_id=item.offer_id, state=item.state, share=item.share
        )
        for item in stream.offers
    ]
    if stream.schema_name != "landings":
        return EditorStream(
            id=stream.id,
            name=stream.name,
            position=stream.position,
            state=stream.state,
            schema_name=stream.schema_name,
            kind="non_offer",
            offers=bindings,
            editor=None,
            known_offers=[],
        )

    live = {item.offer_id: item for item in stream.offers}
    rows = {row.offer_id: row for row in history}
    active_ids = [item.offer_id for item in stream.offers]
    removed = sorted(
        (row for row in history if row.offer_id not in live),
        key=lambda row: (row.first_seen_at, row.offer_id),
    )
    all_ids = active_ids + [row.offer_id for row in removed]
    pins = {row.offer_id: row.pinned_share for row in history if row.pinned_share is not None}
    editor = PreviewInput(
        base_revision=_remote_revision(remote.campaign_id, stream),
        base_pin_revision=pin_revision(remote.campaign_id, stream.id, history),
        base_offers=[BaseOffer(id=b.offer_id, raw_share=b.share) for b in stream.offers],
        base_known_ids=all_ids,
        draft_known_ids=all_ids,
        base_pinned_shares=pins,
        active_ids=active_ids,
        pinned_shares=pins,
        mode="SNAPSHOT",
    )
    known_rows: list[KnownOfferRow] = []
    for offer_id in all_ids:
        row = rows[offer_id]
        binding = live.get(offer_id)
        active = binding is not None
        known_rows.append(
            KnownOfferRow(
                id=offer_id,
                offer_id=offer_id,
                binding_id=binding.id if binding is not None else None,
                last_binding_id=row.last_binding_id,
                name=row.offer_name,
                status="Active" if active else "Removed",
                kt_state=binding.state if binding is not None else None,
                last_seen_kt_state=row.last_seen_kt_state,
                display_share=binding.share if binding is not None else 0,
                display_share_unit="kt_raw" if active else "percentage_points",
                last_seen_raw_share=row.last_seen_share,
                pinned_share=row.pinned_share,
                first_seen_at=_utc_string(row.first_seen_at),
                last_seen_at=_utc_string(row.last_seen_at),
                removed_at=_utc_string(row.removed_at),
            )
        )
    return EditorStream(
        id=stream.id,
        name=stream.name,
        position=stream.position,
        state=stream.state,
        schema_name=stream.schema_name,
        kind="offers",
        offers=bindings,
        editor=editor,
        known_offers=known_rows,
    )


def _snapshot(
    remote: RemoteCampaign, by_stream: dict[tuple[int, int], list[History]]
) -> EditorSnapshot:
    streams = [
        _editor_stream(remote, stream, by_stream.get((remote.campaign_id, stream.id), []))
        for stream in sorted(remote.streams, key=lambda s: (s.position, s.id))
    ]
    return EditorSnapshot(campaign_id=remote.campaign_id, streams=streams)


def _check_capacity(snapshot: EditorSnapshot) -> None:
    """Bound every stream to the existing nine-field Preview contract."""
    for stream in snapshot.streams:
        if stream.editor is None:
            continue
        # Pydantic already validates MAX_DRAFT_ITEMS and structural constraints.
        # Its compact JSON has the same length as JSON.stringify for this ASCII-only DTO.
        body = json.dumps(
            stream.editor.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        if len(body) > MAX_PREVIEW_BYTES:
            raise EditorProblem(
                "editor_snapshot_too_large", "The complete archive exceeds Preview limits"
            )


def _validate_migration(session: Session) -> None:
    """Fail rather than treating an uninitialized or stale DB as an empty archive."""
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if revision != EXPECTED_REVISION:
        raise EditorProblem(
            "local_archive_schema_missing", "Local archive migration is missing", 503
        )


def _synchronize(session: Session, remote: RemoteCampaign) -> EditorSnapshot:
    """Inside a physical transaction: SELECT -> full preflight -> DML -> result."""
    _validate_migration(session)
    previous = list(
        session.execute(
            select(KnownOfferBinding).where(KnownOfferBinding.campaign_id == remote.campaign_id)
        ).scalars()
    )
    old = [_state(row) for row in previous]
    now = datetime.now(UTC).replace(tzinfo=None)
    after = _propose(remote, old, now)
    try:
        snapshot = _snapshot(remote, after)
        _check_capacity(snapshot)
    except (ValidationError, ValueError) as exc:
        raise EditorProblem(
            "editor_snapshot_too_large", "The complete archive exceeds Preview limits"
        ) from exc

    present = {(r.stream_id, r.offer_id): r for r in previous}
    for all_rows in after.values():
        for row in all_rows:
            prev = present.get((row.stream_id, row.offer_id))
            if prev is None:
                session.add(KnownOfferBinding(**row.__dict__))
            else:
                for name in (
                    "last_binding_id",
                    "offer_name",
                    "last_seen_share",
                    "last_seen_kt_state",
                    "last_seen_at",
                    "removed_at",
                ):
                    setattr(prev, name, getattr(row, name))
                # pinned_share and first_seen_at are immutable across Fetch.
    return snapshot


def sync_campaign(engine: Engine, remote: RemoteCampaign) -> EditorSnapshot:
    """One SQL worker owns Session/BEGIN/commit/rollback/close until return."""
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            with session.begin():
                # Session.begin() alone does not BEGIN SQLite in legacy mode.
                # session.connection() forces the registered physical BEGIN IMMEDIATE.
                session.connection()
                return _synchronize(session, remote)
    except OperationalError as exc:
        message = str(exc.orig).lower()
        if "locked" in message or "busy" in message:
            raise EditorProblem("local_archive_busy", "Local archive is busy", 503) from exc
        if "no such table" in message or "no such column" in message:
            raise EditorProblem(
                "local_archive_schema_missing", "Local archive migration is missing", 503
            ) from exc
        raise EditorProblem(
            "local_archive_unavailable", "Local archive is unavailable", 503
        ) from exc
    except (SQLAlchemyError, sqlite3.DatabaseError) as exc:
        raise EditorProblem(
            "local_archive_unavailable", "Local archive is unavailable", 503
        ) from exc
