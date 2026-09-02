"""SessionStore contract tests for the in-memory implementation."""

import pytest

from dayu_agent.exceptions import SessionError, SessionNotFoundError
from dayu_agent.memory import InMemorySessionStore, MessageRole


@pytest.mark.asyncio
async def test_create_append_list_and_clear_session() -> None:
    """The default store must preserve message order and session identity."""

    store = InMemorySessionStore()
    session = await store.create_session(metadata={"source": "test"})
    await store.append_message(session.id, role=MessageRole.USER, content="first")
    await store.append_message(session.id, role=MessageRole.ASSISTANT, content="second")

    messages = await store.list_messages(session.id)
    assert [message.content for message in messages] == ["first", "second"]
    await store.clear_session(session.id)
    assert await store.list_messages(session.id) == ()
    assert (await store.get_session(session.id)).id == session.id


@pytest.mark.asyncio
async def test_unknown_session_fails_closed() -> None:
    """Unknown session identifiers must never be created implicitly by reads."""

    store = InMemorySessionStore()
    with pytest.raises(SessionNotFoundError):
        await store.get_session("missing")
    with pytest.raises(SessionNotFoundError):
        await store.append_message("missing", role=MessageRole.USER, content="no")
    with pytest.raises(SessionNotFoundError):
        await store.list_messages("missing")
    with pytest.raises(SessionNotFoundError):
        await store.clear_session("missing")


@pytest.mark.asyncio
async def test_duplicate_session_id_is_rejected() -> None:
    """Explicit identifiers cannot silently overwrite an existing conversation."""

    store = InMemorySessionStore()
    await store.create_session(session_id="stable")
    with pytest.raises(SessionError):
        await store.create_session(session_id="stable")
