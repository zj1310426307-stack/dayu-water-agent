"""Add production runtime, idempotency, and durable stream state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade Phase-00 tables and add durable run/event state."""

    op.add_column("agent_sessions", sa.Column("user_id", sa.String(length=255), nullable=True))
    op.add_column(
        "agent_sessions",
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("worker_instance_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "usage", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "result", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency_key"),
    )
    op.create_index("ix_agent_runs_request_id", "agent_runs", ["request_id"], unique=False)
    op.create_index(
        "ix_agent_runs_session_created",
        "agent_runs",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_agent_runs_one_active_per_session",
        "agent_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.add_column("agent_messages", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.add_column("agent_messages", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column(
        "agent_messages",
        sa.Column("committed", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (
                PARTITION BY session_id ORDER BY created_at, id
            ) AS position
            FROM agent_messages
        )
        UPDATE agent_messages AS message
        SET sequence = ordered.position
        FROM ordered
        WHERE message.id = ordered.id
        """
    )
    op.alter_column("agent_messages", "sequence", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "fk_agent_messages_run_id_agent_runs",
        "agent_messages",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_agent_messages_session_sequence",
        "agent_messages",
        ["session_id", "sequence"],
    )

    op.create_table(
        "agent_stream_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_stream_events_run_sequence"),
    )
    op.create_index(
        "ix_agent_stream_events_run_created",
        "agent_stream_events",
        ["run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Restore the exact Phase-00 session and message schema."""

    op.drop_index("ix_agent_stream_events_run_created", table_name="agent_stream_events")
    op.drop_table("agent_stream_events")
    op.drop_constraint(
        "uq_agent_messages_session_sequence", "agent_messages", type_="unique"
    )
    op.drop_constraint(
        "fk_agent_messages_run_id_agent_runs", "agent_messages", type_="foreignkey"
    )
    op.drop_column("agent_messages", "committed")
    op.drop_column("agent_messages", "sequence")
    op.drop_column("agent_messages", "run_id")
    op.drop_index("uq_agent_runs_one_active_per_session", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_request_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_column("agent_sessions", "version")
    op.drop_column("agent_sessions", "status")
    op.drop_column("agent_sessions", "user_id")
