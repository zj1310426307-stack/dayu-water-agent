"""Minimal database infrastructure tests that require no PostgreSQL service."""

from dayu_agent.database import Base


def test_metadata_contains_only_phase_zero_session_tables() -> None:
    """The initial schema must stay minimal and avoid business-domain tables."""

    assert set(Base.metadata.tables) == {"agent_sessions", "agent_messages"}
    assert Base.metadata.tables["agent_messages"].c.session_id.foreign_keys
