from alembic import op
import sqlalchemy as sa

revision = "0002_lead_intent_and_operational_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("communities", sa.Column("resolve_status", sa.String(length=32), nullable=False, server_default="pending"))
    op.add_column("communities", sa.Column("last_error", sa.Text(), nullable=True))

    op.add_column("leads", sa.Column("lead_type", sa.String(length=64), nullable=False, server_default="NOISE"))
    op.add_column("leads", sa.Column("buyer_type", sa.String(length=32), nullable=False, server_default="UNKNOWN"))
    op.add_column("leads", sa.Column("status", sa.String(length=32), nullable=False, server_default="NEW"))
    op.add_column("leads", sa.Column("intent_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("leads", sa.Column("technical_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("leads", sa.Column("commercial_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("leads", sa.Column("promotion_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("leads", sa.Column("contact_usernames", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("leads", sa.Column("contact_urls", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("leads", sa.Column("budget_amount", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("budget_currency", sa.String(length=16), nullable=True))
    op.add_column("leads", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))

    op.create_index("ix_leads_lead_type", "leads", ["lead_type"])
    op.create_index("ix_leads_buyer_type", "leads", ["buyer_type"])
    op.create_index("ix_leads_status", "leads", ["status"])


def downgrade() -> None:
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_buyer_type", table_name="leads")
    op.drop_index("ix_leads_lead_type", table_name="leads")

    op.drop_column("leads", "updated_at")
    op.drop_column("leads", "budget_currency")
    op.drop_column("leads", "budget_amount")
    op.drop_column("leads", "contact_urls")
    op.drop_column("leads", "contact_usernames")
    op.drop_column("leads", "promotion_score")
    op.drop_column("leads", "commercial_score")
    op.drop_column("leads", "technical_score")
    op.drop_column("leads", "intent_score")
    op.drop_column("leads", "status")
    op.drop_column("leads", "buyer_type")
    op.drop_column("leads", "lead_type")

    op.drop_column("communities", "last_error")
    op.drop_column("communities", "resolve_status")
