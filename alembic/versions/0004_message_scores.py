"""Add message_scores table for persisted positives/negatives/features."""

from alembic import op
import sqlalchemy as sa

revision = "0004_message_scores"
down_revision = "0003_opportunity_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("community_id", sa.Integer(), sa.ForeignKey("communities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="LOW"),
        sa.Column("buyer_type", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.Column("lead_type", sa.String(length=64), nullable=False, server_default="NOISE"),
        sa.Column("intent_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("technical_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("commercial_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("promotion_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("semantic_score", sa.Float(), nullable=True),
        sa.Column("matched_keywords", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("matched_categories", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reasons", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("rule_version", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("decision", sa.String(length=16), nullable=False, server_default="NEGATIVE"),
        sa.UniqueConstraint("message_id", name="uq_message_scores_message_id"),
    )
    op.create_index("ix_message_scores_scored_at", "message_scores", ["scored_at"])
    op.create_index("ix_message_scores_decision", "message_scores", ["decision"])
    op.create_index("ix_message_scores_tier", "message_scores", ["tier"])
    op.create_index("ix_message_scores_community_id", "message_scores", ["community_id"])
    op.create_index("ix_message_scores_message_id", "message_scores", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_message_scores_message_id", table_name="message_scores")
    op.drop_index("ix_message_scores_community_id", table_name="message_scores")
    op.drop_index("ix_message_scores_tier", table_name="message_scores")
    op.drop_index("ix_message_scores_decision", table_name="message_scores")
    op.drop_index("ix_message_scores_scored_at", table_name="message_scores")
    op.drop_table("message_scores")
