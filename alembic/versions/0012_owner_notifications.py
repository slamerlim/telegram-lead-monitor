"""Durable owner Telegram alerts, separated from prospect outreach and labels."""
from alembic import op
import sqlalchemy as sa

revision = "0012_owner_notifications"
down_revision = "0011_dispatch_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="NO ACTION"), nullable=False, unique=True),
        sa.Column("dedup_key", sa.String(64), nullable=False, unique=True),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("random_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(128)),
        sa.Column("telegram_message_id", sa.Integer()),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_lead_notifications_status", "lead_notifications", ["status"])


def downgrade():
    raise RuntimeError("Refusing to delete owner alert history; restore schema deliberately")
