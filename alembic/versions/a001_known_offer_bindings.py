"""Create the TASK-03 known offer binding archive.

Revision ID: a001_known_offer_bindings
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a001_known_offer_bindings"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAFE = 9007199254740991


def upgrade() -> None:
    op.create_table(
        "known_offer_bindings",
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("stream_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.BigInteger(), nullable=False),
        sa.Column("last_binding_id", sa.BigInteger(), nullable=True),
        sa.Column("offer_name", sa.String(length=512), nullable=False),
        sa.Column("pinned_share", sa.Integer(), nullable=True),
        sa.Column("last_seen_share", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_kt_state", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("campaign_id", "stream_id", "offer_id"),
        sa.CheckConstraint(f"campaign_id BETWEEN 1 AND {SAFE}", name="campaign_safe"),
        sa.CheckConstraint(f"stream_id BETWEEN 1 AND {SAFE}", name="stream_safe"),
        sa.CheckConstraint(f"offer_id BETWEEN 1 AND {SAFE}", name="offer_safe"),
        sa.CheckConstraint(
            f"last_binding_id IS NULL OR last_binding_id BETWEEN 1 AND {SAFE}",
            name="binding_safe",
        ),
        sa.CheckConstraint(
            "pinned_share IS NULL OR pinned_share BETWEEN 1 AND 100", name="pin_valid"
        ),
        sa.CheckConstraint(f"last_seen_share BETWEEN 0 AND {SAFE}", name="raw_share_valid"),
        sa.CheckConstraint("length(trim(offer_name)) > 0", name="name_nonempty"),
        sa.CheckConstraint("last_seen_kt_state IN ('active', 'disabled')", name="kt_state_valid"),
    )
    op.create_index(
        "ix_known_offer_bindings_campaign_stream",
        "known_offer_bindings",
        ["campaign_id", "stream_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_known_offer_bindings_campaign_stream", table_name="known_offer_bindings")
    op.drop_table("known_offer_bindings")
