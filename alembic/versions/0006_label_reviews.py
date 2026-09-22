"""Add label_review_samples + label_reviews for independent human review.

Does NOT modify/overwrite human_labels provenance. Agent-provisional labels stay
in human_labels; independent reviewers append rows here.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_label_reviews"
down_revision = "0005_human_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "label_review_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sample_batch_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stratum", sa.String(length=64), nullable=False),
        sa.Column("methodology_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sample_batch_id", "message_id", name="uq_label_review_samples_batch_msg"),
    )
    op.create_index("ix_label_review_samples_batch", "label_review_samples", ["sample_batch_id"])
    op.create_index("ix_label_review_samples_stratum", "label_review_samples", ["stratum"])
    op.create_index("ix_label_review_samples_message_id", "label_review_samples", ["message_id"])

    op.create_table(
        "label_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sample_batch_id", sa.String(length=64), nullable=True),
        sa.Column("reviewer_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("fp_class", sa.String(length=64), nullable=True),
        sa.Column("commercially_actionable", sa.Boolean(), nullable=True),
        sa.Column("scorer_shown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prior_label_shown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "reviewer_id", "sample_batch_id", name="uq_label_reviews_msg_reviewer_batch"),
    )
    op.create_index("ix_label_reviews_message_id", "label_reviews", ["message_id"])
    op.create_index("ix_label_reviews_reviewer_id", "label_reviews", ["reviewer_id"])
    op.create_index("ix_label_reviews_label", "label_reviews", ["label"])
    op.create_index("ix_label_reviews_batch", "label_reviews", ["sample_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_label_reviews_batch", table_name="label_reviews")
    op.drop_index("ix_label_reviews_label", table_name="label_reviews")
    op.drop_index("ix_label_reviews_reviewer_id", table_name="label_reviews")
    op.drop_index("ix_label_reviews_message_id", table_name="label_reviews")
    op.drop_table("label_reviews")
    op.drop_index("ix_label_review_samples_message_id", table_name="label_review_samples")
    op.drop_index("ix_label_review_samples_stratum", table_name="label_review_samples")
    op.drop_index("ix_label_review_samples_batch", table_name="label_review_samples")
    op.drop_table("label_review_samples")
