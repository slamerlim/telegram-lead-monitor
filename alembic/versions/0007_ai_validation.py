"""Extend label_reviews for AI validators + attempts + consensus tables.

Additive only. Does not modify/delete existing Phase D or human rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_ai_validation"
down_revision = "0006_label_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    # --- label_reviews provenance columns (nullable for legacy rows) ---
    cols = [
        ("validator_kind", sa.String(16), True),
        ("validation_mode", sa.String(8), True),
        ("provider", sa.String(32), True),
        ("model", sa.String(128), True),
        ("model_family", sa.String(64), True),
        ("prompt_version", sa.String(32), True),
        ("config_version", sa.String(64), True),
        ("validation_run_id", sa.String(64), True),
        ("request_id", sa.String(128), True),
        ("latency_ms", sa.Integer(), True),
        ("confidence", sa.Float(), True),
        ("lead_type", sa.String(64), True),
        ("evidence_json", sa.Text(), True),
        ("rationale_short", sa.Text(), True),
        ("structured_result", sa.Text(), True),
        ("input_sha256", sa.String(64), True),
        ("peer_outputs_shown", sa.Boolean(), False),
        ("source_attempt_id", sa.BigInteger(), True),
        ("attestation_sig", sa.String(128), True),
    ]
    for name, coltype, nullable in cols:
        if name == "peer_outputs_shown":
            op.add_column(
                "label_reviews",
                sa.Column(name, coltype, nullable=False, server_default=sa.false()),
            )
        else:
            op.add_column("label_reviews", sa.Column(name, coltype, nullable=nullable))

    op.create_index("ix_lr_kind_batch", "label_reviews", ["validator_kind", "sample_batch_id"])
    op.create_index("ix_lr_run", "label_reviews", ["validation_run_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lr_ai_mode_per_msg_batch
        ON label_reviews (message_id, sample_batch_id, validation_mode)
        WHERE validator_kind = 'ai'
        """
    )

    op.execute(
        """
        ALTER TABLE label_reviews
        ADD CONSTRAINT ck_lr_validator_kind
        CHECK (validator_kind IS NULL OR validator_kind IN ('human', 'ai'))
        NOT VALID
        """
    )
    op.execute("ALTER TABLE label_reviews VALIDATE CONSTRAINT ck_lr_validator_kind")

    op.execute(
        """
        ALTER TABLE label_reviews
        ADD CONSTRAINT ck_lr_ai_label
        CHECK (
          validator_kind IS DISTINCT FROM 'ai'
          OR label IN ('AI_TRUE', 'AI_FALSE', 'AI_UNCERTAIN', 'AI_INSUFFICIENT_EVIDENCE')
        )
        NOT VALID
        """
    )
    op.execute("ALTER TABLE label_reviews VALIDATE CONSTRAINT ck_lr_ai_label")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION label_reviews_ai_guard() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.validator_kind = 'ai' THEN
              RAISE EXCEPTION 'ai label_reviews rows are immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.validator_kind = 'ai' THEN
            RAISE EXCEPTION 'ai label_reviews rows are immutable';
          END IF;
          IF NEW.validator_kind = 'ai' AND (OLD.validator_kind IS DISTINCT FROM 'ai') THEN
            RAISE EXCEPTION 'cannot promote legacy/human row to ai';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_label_reviews_ai_guard
        BEFORE UPDATE OR DELETE ON label_reviews
        FOR EACH ROW EXECUTE FUNCTION label_reviews_ai_guard()
        """
    )

    op.create_table(
        "ai_validation_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("validation_run_id", sa.String(64), nullable=False),
        sa.Column("sample_batch_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("validation_mode", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_family", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=True),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_sha256", sa.String(64), nullable=True),
        sa.Column("response_sha256", sa.String(64), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "validation_run_id",
            "message_id",
            "reviewer_id",
            "attempt_no",
            name="uq_ai_val_attempt",
        ),
    )
    op.create_index("ix_ai_val_attempts_status", "ai_validation_attempts", ["status"])
    op.create_index("ix_ai_val_attempts_message", "ai_validation_attempts", ["message_id"])
    op.create_index("ix_ai_val_attempts_run", "ai_validation_attempts", ["validation_run_id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION ai_validation_attempts_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'ai_validation_attempts is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_validation_attempts_immutable
        BEFORE UPDATE OR DELETE ON ai_validation_attempts
        FOR EACH ROW EXECUTE FUNCTION ai_validation_attempts_immutable()
        """
    )

    op.create_table(
        "validation_consensus",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sample_batch_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("validation_run_id", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("rationale_code", sa.String(64), nullable=False),
        sa.Column("n_blind_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_true", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_false", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_uncertain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_insufficient", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_providers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_families", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("modes_present", sa.String(8), nullable=True),
        sa.Column("adjudicator_label", sa.String(32), nullable=True),
        sa.Column("lead_type", sa.String(64), nullable=True),
        sa.Column("input_review_ids", sa.Text(), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("gate_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "sample_batch_id",
            "message_id",
            "policy_version",
            "input_digest",
            name="uq_validation_consensus_digest",
        ),
    )
    op.create_index("ix_validation_consensus_state", "validation_consensus", ["state"])
    op.create_index(
        "ix_validation_consensus_batch_msg",
        "validation_consensus",
        ["sample_batch_id", "message_id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION validation_consensus_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'validation_consensus is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validation_consensus_immutable
        BEFORE UPDATE OR DELETE ON validation_consensus
        FOR EACH ROW EXECUTE FUNCTION validation_consensus_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_validation_consensus_immutable ON validation_consensus")
    op.execute("DROP FUNCTION IF EXISTS validation_consensus_immutable()")
    op.drop_table("validation_consensus")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_validation_attempts_immutable ON ai_validation_attempts")
    op.execute("DROP FUNCTION IF EXISTS ai_validation_attempts_immutable()")
    op.drop_table("ai_validation_attempts")
    op.execute("DROP TRIGGER IF EXISTS trg_label_reviews_ai_guard ON label_reviews")
    op.execute("DROP FUNCTION IF EXISTS label_reviews_ai_guard()")
    op.execute("DROP INDEX IF EXISTS uq_lr_ai_mode_per_msg_batch")
    op.drop_index("ix_lr_run", table_name="label_reviews")
    op.drop_index("ix_lr_kind_batch", table_name="label_reviews")
    op.execute("ALTER TABLE label_reviews DROP CONSTRAINT IF EXISTS ck_lr_ai_label")
    op.execute("ALTER TABLE label_reviews DROP CONSTRAINT IF EXISTS ck_lr_validator_kind")
    for name in (
        "attestation_sig",
        "source_attempt_id",
        "peer_outputs_shown",
        "input_sha256",
        "structured_result",
        "rationale_short",
        "evidence_json",
        "lead_type",
        "confidence",
        "latency_ms",
        "request_id",
        "validation_run_id",
        "config_version",
        "prompt_version",
        "model_family",
        "model",
        "provider",
        "validation_mode",
        "validator_kind",
    ):
        op.drop_column("label_reviews", name)
