"""Persistent history of logical offer-to-stream memberships (TASK-03 B1)."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from adrobot.db.base import Base
from adrobot.keitaro.read_models import JS_SAFE_INTEGER


class KnownOfferBinding(Base):
    """One logical offer per campaign/stream, independent of Keitaro binding ID."""

    __tablename__ = "known_offer_bindings"
    __table_args__ = (
        CheckConstraint(f"campaign_id BETWEEN 1 AND {JS_SAFE_INTEGER}", name="campaign_safe"),
        CheckConstraint(f"stream_id BETWEEN 1 AND {JS_SAFE_INTEGER}", name="stream_safe"),
        CheckConstraint(f"offer_id BETWEEN 1 AND {JS_SAFE_INTEGER}", name="offer_safe"),
        CheckConstraint(
            f"last_binding_id IS NULL OR last_binding_id BETWEEN 1 AND {JS_SAFE_INTEGER}",
            name="binding_safe",
        ),
        CheckConstraint("pinned_share IS NULL OR pinned_share BETWEEN 1 AND 100", name="pin_valid"),
        CheckConstraint(f"last_seen_share BETWEEN 0 AND {JS_SAFE_INTEGER}", name="raw_share_valid"),
        CheckConstraint("length(trim(offer_name)) > 0", name="name_nonempty"),
        CheckConstraint("last_seen_kt_state IN ('active', 'disabled')", name="kt_state_valid"),
        Index("ix_known_offer_bindings_campaign_stream", "campaign_id", "stream_id"),
    )

    campaign_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stream_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    offer_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_binding_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    offer_name: Mapped[str] = mapped_column(String(512), nullable=False)
    pinned_share: Mapped[int | None] = mapped_column(nullable=True)
    last_seen_share: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_kt_state: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
