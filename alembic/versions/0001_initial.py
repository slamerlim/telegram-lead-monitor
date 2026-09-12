from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "communities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_ref", sa.String(255), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(500)),
        sa.Column("username", sa.String(255)),
        sa.Column("url", sa.String(1000)),
        sa.Column("kind", sa.String(32)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True)),
        sa.Column("last_message_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_ref"),
        sa.UniqueConstraint("telegram_chat_id"),
    )
    op.create_index("ix_communities_telegram_ref", "communities", ["telegram_ref"])
    op.create_table(
        "authors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(255)),
        sa.Column("name", sa.String(500)),
        sa.Column("bio", sa.Text()),
        sa.Column("is_bot", sa.Boolean()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_authors_telegram_id", "authors", ["telegram_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("community_id", sa.Integer(), sa.ForeignKey("communities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("authors.id", ondelete="SET NULL")),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.Integer(), nullable=False),
        sa.Column("message_url", sa.String(1000)),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("community_id", "telegram_message_id", name="uq_message_community_message"),
    )
    op.create_index("ix_messages_community_id", "messages", ["community_id"])
    op.create_index("ix_messages_telegram_chat_id", "messages", ["telegram_chat_id"])
    op.create_index("ix_messages_telegram_message_id", "messages", ["telegram_message_id"])
    op.create_index("ix_messages_date", "messages", ["message_date"])
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="LOW"),
        sa.Column("matched_keywords", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("matched_categories", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reasons", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("semantic_score", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_leads_score", "leads", ["score"])
    op.create_index("ix_leads_created", "leads", ["created_at"])
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("community_id", sa.Integer(), sa.ForeignKey("communities.id", ondelete="SET NULL")),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("messages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_published", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("scan_runs")
    op.drop_index("ix_leads_created", table_name="leads")
    op.drop_index("ix_leads_score", table_name="leads")
    op.drop_table("leads")
    op.drop_index("ix_messages_date", table_name="messages")
    op.drop_index("ix_messages_telegram_message_id", table_name="messages")
    op.drop_index("ix_messages_telegram_chat_id", table_name="messages")
    op.drop_index("ix_messages_community_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_authors_telegram_id", table_name="authors")
    op.drop_table("authors")
    op.drop_index("ix_communities_telegram_ref", table_name="communities")
    op.drop_table("communities")
