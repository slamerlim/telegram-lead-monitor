"""Add leads.opportunity_key and collapse duplicate (text, community) opportunities."""

from alembic import op
import sqlalchemy as sa

from shared.opportunity import opportunity_key as make_opportunity_key

revision = "0003_opportunity_key"
down_revision = "0002_lead_intent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("opportunity_key", sa.String(length=96), nullable=True))
    op.create_index("ix_leads_opportunity_key", "leads", ["opportunity_key"])

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT l.id, m.community_id, m.text
            FROM leads AS l
            JOIN messages AS m ON m.id = l.message_id
            WHERE l.opportunity_key IS NULL
            """
        )
    ).fetchall()
    for lead_id, community_id, text in rows:
        conn.execute(
            sa.text("UPDATE leads SET opportunity_key = :key WHERE id = :id"),
            {"key": make_opportunity_key(int(community_id), text or ""), "id": int(lead_id)},
        )

    # Keep one lead per opportunity_key: prefer higher score, then older id.
    op.execute(
        """
        DELETE FROM leads AS l
        USING leads AS keep
        WHERE l.opportunity_key IS NOT NULL
          AND keep.opportunity_key = l.opportunity_key
          AND l.id <> keep.id
          AND (
                keep.score > l.score
             OR (keep.score = l.score AND keep.id < l.id)
          )
        """
    )

    op.create_unique_constraint("uq_leads_opportunity_key", "leads", ["opportunity_key"])


def downgrade() -> None:
    op.drop_constraint("uq_leads_opportunity_key", "leads", type_="unique")
    op.drop_index("ix_leads_opportunity_key", table_name="leads")
    op.drop_column("leads", "opportunity_key")
