"""Conversation-session management for configured agents.

Bridges ``agent_conversations`` (storage) with agent naming conventions
and tmux session names.  Each agent REPL gets a *conversation session*:
a persistent conversation_id stored in a lightweight index so the REPL
can resume where it left off.
"""

from __future__ import annotations

from typing import Any

from . import conversations as agent_conversations


CONVERSATION_PREFIX = "agent-repl-"

# In-memory map: agent_name -> active conversation_id.
# Populated on first access; survives for the process lifetime.
_active: dict[str, str] = {}


def conversation_session_name(agent_name: str) -> str:
    return CONVERSATION_PREFIX + (agent_name or "").strip().lower()


def agent_name_from_session(session_name: str) -> str | None:
    name = (session_name or "").strip()
    if not name.startswith(CONVERSATION_PREFIX):
        return None
    agent_name = name[len(CONVERSATION_PREFIX):].strip().lower()
    return agent_name or None


def get_or_create_conversation(agent_name: str) -> str:
    """Return the active conversation_id for *agent_name*, creating one
    if none exists yet."""
    name = (agent_name or "").strip().lower()
    if name in _active:
        header = agent_conversations.load_header(_active[name])
        if header is not None:
            return _active[name]

    # Check for an existing conversation on disk.
    existing = agent_conversations.list_conversations(agent_name=name)
    if existing:
        # Use the most recently created one.
        best = max(existing, key=lambda h: h.get("created_ts", 0))
        cid = best["conversation_id"]
        _active[name] = cid
        return cid

    cid = agent_conversations.create(name)
    _active[name] = cid
    return cid


def start_new_conversation(agent_name: str) -> str:
    """Force-create a fresh conversation, replacing the active one."""
    name = (agent_name or "").strip().lower()
    cid = agent_conversations.create(name)
    _active[name] = cid
    return cid


def load_context(agent_name: str) -> list[dict[str, str]]:
    """Load the message history for the active conversation, suitable
    for passing as ``conversation_messages`` to ``run_agent``."""
    cid = get_or_create_conversation(agent_name)
    return agent_conversations.load_messages(cid)


def record_turn(agent_name: str, *, role: str, content: str,
                run_id: str | None = None) -> None:
    """Append a turn to the active conversation for *agent_name*."""
    cid = get_or_create_conversation(agent_name)
    agent_conversations.append_turn(cid, role=role, content=content, run_id=run_id)


def fork_conversation(agent_name: str) -> str:
    """Fork the active conversation for *agent_name* into a new one.

    The original conversation is preserved; the new one becomes the
    active conversation.  Returns the new conversation_id.
    """
    name = (agent_name or "").strip().lower()
    source_cid = get_or_create_conversation(name)
    new_cid = agent_conversations.fork(source_cid, agent_name=name)
    _active[name] = new_cid
    return new_cid


def list_sessions(agent_name: str | None = None) -> list[dict[str, Any]]:
    """Return conversation headers, optionally filtered."""
    return agent_conversations.list_conversations(agent_name=agent_name)


def clear_conversation(agent_name: str) -> bool:
    """Delete the active conversation for *agent_name*."""
    name = (agent_name or "").strip().lower()
    cid = _active.pop(name, None)
    if cid:
        return agent_conversations.clear(cid)
    return False
