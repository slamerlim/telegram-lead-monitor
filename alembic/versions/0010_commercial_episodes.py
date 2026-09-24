"""Commercial episode discovery plane (shadow experiment; no CRM promotion).

Adds reply linkage column, discovery/episode tables, concurrent indexes,
and nullable episode fields on commercial_ai_reviews.

CREATE INDEX CONCURRENTLY runs outside the Alembic transaction via autocommit_block.
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_commercial_episodes"
down_revision = "0009_commercial_ai_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")

    op.add_column(
        "messages",
        sa.Column("reply_to_telegram_message_id", sa.Integer(), nullable=True),
    )

    op.create_table(
        "commercial_discovery_candidates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "seed_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="NO ACTION"),
            nullable=False,
        ),
        sa.Column("community_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("discovery_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("trigger_type", sa.String(64), nullable=False),
        sa.Column("trigger_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("scorer_tier", sa.String(16), nullable=False),
        sa.Column("signals_json", sa.Text(), nullable=False),
        sa.Column("topic_fingerprint", sa.String(64), nullable=False),
        sa.Column("recall_rank", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("episode_id", sa.BigInteger(), nullable=True),
        sa.Column("context_version", sa.String(16), nullable=True),
        sa.Column("context_hash", sa.String(64), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("seed_message_id", "discovery_version", name="uq_cdc_msg_ver"),
    )
    op.create_index("ix_cdc_status_created", "commercial_discovery_candidates", ["status", "created_at"])
    op.create_index("ix_cdc_seed", "commercial_discovery_candidates", ["seed_message_id"])

    op.create_table(
        "commercial_episodes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("episode_key", sa.String(96), nullable=False),
        sa.Column(
            "seed_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="NO ACTION"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("community_id", sa.Integer(), nullable=False),
        sa.Column("topic_fingerprint", sa.String(64), nullable=False),
        sa.Column("episode_status", sa.String(24), nullable=False, server_default="BUILT"),
        sa.Column("context_version", sa.String(16), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("members_json", sa.Text(), nullable=False),
        sa.Column("n_messages", sa.SmallInteger(), nullable=False),
        sa.Column("n_excluded_blind", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("episode_key", "context_hash", name="uq_ce_key_hash"),
        sa.CheckConstraint("n_messages >= 1 AND n_messages <= 20", name="ck_ce_n_messages"),
    )
    op.create_index("ix_ce_seed", "commercial_episodes", ["seed_message_id"])
    op.create_index("ix_ce_author_fp", "commercial_episodes", ["author_id", "topic_fingerprint", "created_at"])

    # Episodes may update status/updated_at; block DELETE only (reviews remain append-only).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION commercial_episodes_no_delete() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'commercial_episodes deletes are forbidden';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_commercial_episodes_no_delete
        BEFORE DELETE ON commercial_episodes
        FOR EACH ROW EXECUTE FUNCTION commercial_episodes_no_delete()
        """
    )

    op.add_column("commercial_ai_reviews", sa.Column("episode_id", sa.BigInteger(), nullable=True))
    op.add_column("commercial_ai_reviews", sa.Column("context_version", sa.String(16), nullable=True))
    op.add_column("commercial_ai_reviews", sa.Column("context_hash", sa.String(64), nullable=True))
    op.add_column("commercial_ai_reviews", sa.Column("experiment_arm", sa.String(16), nullable=True))
    op.add_column("commercial_ai_reviews", sa.Column("evidence_message_ids_json", sa.Text(), nullable=True))
    op.add_column("commercial_ai_reviews", sa.Column("evidence_valid", sa.Boolean(), nullable=True))
    op.add_column(
        "commercial_ai_reviews", sa.Column("evidence_reject_reason", sa.String(64), nullable=True)
    )
    op.create_index("ix_cai_episode_arm", "commercial_ai_reviews", ["episode_id", "experiment_arm"])

    # Concurrent indexes MUST run outside the migration transaction.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_author_date
            ON messages (author_id, message_date)
            WHERE author_id IS NOT NULL
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_reply_to
            ON messages (community_id, reply_to_telegram_message_id)
            WHERE reply_to_telegram_message_id IS NOT NULL
            """
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Refuse if episode-linked reviews exist (append-only reviews cannot be cleaned).
    conn = op.get_bind()
    n = conn.execute(
        sa.text("SELECT COUNT(*) FROM commercial_ai_reviews WHERE episode_id IS NOT NULL")
    ).scalar()
    if int(n or 0) > 0:
        raise RuntimeError("refuse downgrade: commercial_ai_reviews has episode_id rows")

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_messages_reply_to")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_messages_author_date")

    op.drop_index("ix_cai_episode_arm", table_name="commercial_ai_reviews")
    op.drop_column("commercial_ai_reviews", "evidence_reject_reason")
    op.drop_column("commercial_ai_reviews", "evidence_valid")
    op.drop_column("commercial_ai_reviews", "evidence_message_ids_json")
    op.drop_column("commercial_ai_reviews", "experiment_arm")
    op.drop_column("commercial_ai_reviews", "context_hash")
    op.drop_column("commercial_ai_reviews", "context_version")
    op.drop_column("commercial_ai_reviews", "episode_id")

    op.execute("DROP TRIGGER IF EXISTS trg_commercial_episodes_no_delete ON commercial_episodes")
    op.execute("DROP FUNCTION IF EXISTS commercial_episodes_no_delete()")
    op.drop_table("commercial_episodes")
    op.drop_table("commercial_discovery_candidates")
    op.drop_column("messages", "reply_to_telegram_message_id")
