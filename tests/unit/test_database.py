"""Database metadata tests that require no PostgreSQL service."""

from dayu_agent.database import Base


def test_metadata_contains_phase_one_runtime_tables() -> None:
    """The schema includes runtime state without adding business-domain tables."""

    assert set(Base.metadata.tables) == {
        "agent_sessions",
        "agent_messages",
        "agent_runs",
        "agent_stream_events",
    }
    assert Base.metadata.tables["agent_messages"].c.session_id.foreign_keys


def test_runtime_schema_has_database_concurrency_and_ordering_constraints() -> None:
    """Run ownership and durable ordering are enforced below the process layer."""

    runs = Base.metadata.tables["agent_runs"]
    messages = Base.metadata.tables["agent_messages"]
    events = Base.metadata.tables["agent_stream_events"]

    assert "uq_agent_runs_one_active_per_session" in {index.name for index in runs.indexes}
    assert "uq_agent_runs_idempotency_key" in {
        constraint.name for constraint in runs.constraints
    }
    assert "uq_agent_messages_session_sequence" in {
        constraint.name for constraint in messages.constraints
    }
    assert "uq_agent_stream_events_run_sequence" in {
        constraint.name for constraint in events.constraints
    }
