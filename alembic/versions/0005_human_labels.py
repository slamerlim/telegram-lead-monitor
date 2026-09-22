"""Add human_labels table for independent commercial ground truth."""

from alembic import op
import sqlalchemy as sa

revision = "0005_human_labels"
down_revision = "0004_message_scores"
branch_labels = None
depends_on = None

FP_CLASSES = (
    "MARKETING_BROADCAST",
    "JOB_VACANCY",
    "SUPPORT_REQUEST",
    "SERVICE_AD",
    "JOB_SEEKER",
    "NEWS_DIGEST",
    "OFF_DOMAIN",
    "DUPLICATE",
)


def upgrade() -> None:
    op.create_table(
        "human_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("community_id", sa.Integer(), sa.ForeignKey("communities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_key", sa.String(length=96), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("fp_class", sa.String(length=64), nullable=True),
        sa.Column("commercially_actionable", sa.Boolean(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("labeled_by", sa.String(length=64), nullable=False, server_default="human"),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", name="uq_human_labels_message_id"),
    )
    op.create_index("ix_human_labels_label", "human_labels", ["label"])
    op.create_index("ix_human_labels_fp_class", "human_labels", ["fp_class"])
    op.create_index("ix_human_labels_labeled_at", "human_labels", ["labeled_at"])
    op.create_index("ix_human_labels_message_id", "human_labels", ["message_id"])
    op.create_index("ix_human_labels_community_id", "human_labels", ["community_id"])
    op.create_index("ix_human_labels_opportunity_key", "human_labels", ["opportunity_key"])


def downgrade() -> None:
    op.drop_index("ix_human_labels_opportunity_key", table_name="human_labels")
    op.drop_index("ix_human_labels_community_id", table_name="human_labels")
    op.drop_index("ix_human_labels_message_id", table_name="human_labels")
    op.drop_index("ix_human_labels_labeled_at", table_name="human_labels")
    op.drop_index("ix_human_labels_fp_class", table_name="human_labels")
    op.drop_index("ix_human_labels_label", table_name="human_labels")
    op.drop_table("human_labels")
