"""Transactional publication of scan, discovery and commercial review work."""

from alembic import op
import sqlalchemy as sa

revision = "0011_dispatch_outbox"
down_revision = "0010_commercial_episodes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dispatch_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(255), nullable=False, unique=True),
        sa.Column("stream", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(128), nullable=True),
    )
    op.create_index("ix_outbox_due", "dispatch_outbox", ["published_at", "available_at", "id"])


def downgrade():
    raise RuntimeError("Outbox contains durable work; use a forward migration instead of deleting it")
