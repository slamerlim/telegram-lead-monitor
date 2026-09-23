"""Commercial ops: lead_events audit trail + lead source/owner/follow-up columns.

Additive only. Does not modify messages, human_labels, or AI validation tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_commercial_ops"
down_revision = "0007_ai_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    op.add_column(
        "leads",
        sa.Column("source", sa.String(24), nullable=False, server_default="scorer"),
    )
    op.add_column("leads", sa.Column("owner", sa.String(64), nullable=True))
    op.add_column(
        "leads",
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
    )

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

    op.execute(
        """
        ALTER TABLE leads
        ADD CONSTRAINT ck_leads_source
        CHECK (source IN ('scorer','agent_provisional','operator_search','promote'))
        NOT VALID
        """
    )
    op.execute("ALTER TABLE leads VALIDATE CONSTRAINT ck_leads_source")

    op.create_index(
        "ix_leads_next_action_at",
        "leads",
        ["next_action_at"],
        postgresql_where=sa.text(
            "status IN ('QUALIFIED','CONTACTED','RESPONDED')"
        ),
    )

    op.create_table(
        "lead_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="NO ACTION"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="NO ACTION"),
            nullable=False,
        ),
        sa.Column("opportunity_key", sa.String(96), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=True),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("outcome_amount", sa.Float(), nullable=True),
        sa.Column("outcome_currency", sa.String(16), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_lead_events_idempotency"),
    )
    op.create_index("ix_lead_events_lead_id", "lead_events", ["lead_id"])
    op.create_index("ix_lead_events_occurred_at", "lead_events", ["occurred_at"])
    op.create_index("ix_lead_events_event_type", "lead_events", ["event_type"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION lead_events_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'lead_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lead_events_immutable
        BEFORE UPDATE OR DELETE ON lead_events
        FOR EACH ROW EXECUTE FUNCTION lead_events_immutable()
        """
    )

    # Backfill STATUS_CHANGE-equivalent audit for existing non-NEW leads.
    op.execute(
        """
        INSERT INTO lead_events (
          lead_id, message_id, opportunity_key, event_type,
          from_status, to_status, actor_id, reason_code, note, occurred_at, created_at
        )
        SELECT
          l.id, l.message_id, l.opportunity_key, 'BACKFILL',
          NULL, l.status, 'migration_0008', 'pre_fsm_backfill',
          'Existing CRM status before commercial ops FSM',
          COALESCE(l.updated_at, l.created_at, now()),
          now()
        FROM leads l
        WHERE UPPER(l.status) <> 'NEW'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_lead_events_immutable ON lead_events")
    op.execute("DROP FUNCTION IF EXISTS lead_events_immutable()")
    op.drop_table("lead_events")
    op.drop_index("ix_leads_next_action_at", table_name="leads")
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_source")
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_status")
    op.drop_column("leads", "next_action_at")
    op.drop_column("leads", "owner")
    op.drop_column("leads", "source")
