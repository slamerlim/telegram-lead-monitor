"""Commercial AI adjudication plane (separate from independent AI validation).

Append-only commercial_ai_reviews + AI_CONFIRMED lead status.
Does NOT modify validation_consensus / ai_validation_attempts / human_labels.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_commercial_ai_reviews"
down_revision = "0008_commercial_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_status")
    op.execute(
        """
        ALTER TABLE leads
        ADD CONSTRAINT ck_leads_status
        CHECK (status IN (
          'NEW','REVIEWED','AI_CONFIRMED','CONTACTED','RESPONDED',
          'QUALIFIED','REJECTED','WON','LOST'
        ))
        NOT VALID
        """
    )
    op.execute("ALTER TABLE leads VALIDATE CONSTRAINT ck_leads_status")

    op.create_table(
        "commercial_ai_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("row_kind", sa.String(16), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="NO ACTION"),
            nullable=False,
        ),
        sa.Column("opportunity_key", sa.String(96), nullable=True),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("slot_id", sa.String(64), nullable=True),
        sa.Column("mode", sa.String(8), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("model_family", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("label", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("lead_type", sa.String(64), nullable=True),
        sa.Column("matches_objectives", sa.Boolean(), nullable=True),
        sa.Column("hard_veto", sa.Boolean(), nullable=True),
        sa.Column("uncertain", sa.Boolean(), nullable=True),
        sa.Column("buyer_action", sa.String(32), nullable=True),
        sa.Column("buyer_role", sa.String(32), nullable=True),
        sa.Column("contactability", sa.String(16), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("rationale_short", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("n_confirm", sa.Integer(), nullable=True),
        sa.Column("n_reject", sa.Integer(), nullable=True),
        sa.Column("n_uncertain", sa.Integer(), nullable=True),
        sa.Column("rank_score", sa.Float(), nullable=True),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("draft_valid", sa.Boolean(), nullable=True),
        sa.Column("draft_reject_reason", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_commercial_ai_reviews_idem"),
        sa.CheckConstraint(
            "row_kind IN ('OPINION','DECISION','DRAFT')",
            name="ck_cai_row_kind",
        ),
    )
    op.create_index("ix_cai_message_id", "commercial_ai_reviews", ["message_id"])
    op.create_index("ix_cai_lead_created", "commercial_ai_reviews", ["lead_id", "created_at"])
    op.create_index("ix_cai_run_id", "commercial_ai_reviews", ["run_id"])
    op.execute(
        """
        CREATE INDEX ix_cai_decision
        ON commercial_ai_reviews (row_kind, decision, created_at)
        WHERE row_kind = 'DECISION'
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION commercial_ai_reviews_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'commercial_ai_reviews is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_commercial_ai_reviews_immutable
        BEFORE UPDATE OR DELETE ON commercial_ai_reviews
        FOR EACH ROW EXECUTE FUNCTION commercial_ai_reviews_immutable()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM leads WHERE status = 'AI_CONFIRMED' LIMIT 1
          ) THEN
            RAISE EXCEPTION 'refuse downgrade while AI_CONFIRMED leads exist';
          END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_commercial_ai_reviews_immutable ON commercial_ai_reviews")
    op.execute("DROP FUNCTION IF EXISTS commercial_ai_reviews_immutable()")
    op.drop_table("commercial_ai_reviews")
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_status")
    op.execute(
        """
        ALTER TABLE leads
        ADD CONSTRAINT ck_leads_status
        CHECK (status IN (
          'NEW','REVIEWED','CONTACTED','RESPONDED','QUALIFIED','REJECTED','WON','LOST'
        ))
        NOT VALID
        """
    )
    op.execute("ALTER TABLE leads VALIDATE CONSTRAINT ck_leads_status")
