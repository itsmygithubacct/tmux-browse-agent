"""Persistent per-session conversation storage for agent REPLs.

Each conversation is an append-only JSONL file under
``~/.tmux-browse/agent-conversations/<conversation_id>.jsonl``.

A *turn* is one user prompt plus the agent's response (or error).
The conversation file also stores a header record (type=header) on
its first line with metadata like agent name, creation time, and
optional parent conversation id (for future forking).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from lib import config
from lib.errors import StateError


CONVERSATIONS_DIR = config.AGENT_CONVERSATIONS_DIR


def _ensure_dir() -> None:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def new_conversation_id() -> str:
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    return f"{ts:08x}-{suffix}"


def conversation_path(conversation_id: str) -> Path:
    safe = (conversation_id or "").strip() or "unknown"
    return CONVERSATIONS_DIR / f"{safe}.jsonl"


def create(agent_name: str, *, parent_id: str | None = None) -> str:
    """Create a new conversation and write the header record.

    Returns the conversation_id.
    """
    _ensure_dir()
    cid = new_conversation_id()
    header = {
        "type": "header",
        "conversation_id": cid,
        "agent_name": agent_name,
        "parent_id": parent_id,
        "created_ts": int(time.time()),
    }
    path = conversation_path(cid)
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(header, ensure_ascii=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot create conversation {path}: {e.strerror or e}")
    return cid


def append_turn(conversation_id: str, *, role: str, content: str,
                run_id: str | None = None,
                extra: dict[str, Any] | None = None) -> None:
    """Append a single turn (user prompt or assistant reply) to the conversation."""
    _ensure_dir()
    record: dict[str, Any] = {
        "type": "turn",
        "ts": int(time.time()),
        "role": role,
        "content": content,
    }
    if run_id:
        record["run_id"] = run_id
    if extra:
        record.update(extra)
    path = conversation_path(conversation_id)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot append to conversation {path}: {e.strerror or e}")


def load_header(conversation_id: str) -> dict[str, Any] | None:
    """Read the header record from a conversation file."""
    path = conversation_path(conversation_id)
    if not path.exists():
        return None
    try:
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        row = json.loads(first)
        if isinstance(row, dict) and row.get("type") == "header":
            return row
    except (OSError, ValueError):
        pass
    return None


def load_turns(conversation_id: str) -> list[dict[str, Any]]:
    """Read all turn records from a conversation (excludes the header)."""
    path = conversation_path(conversation_id)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise StateError(f"cannot read conversation {path}: {e.strerror or e}")
    turns: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") == "turn":
            turns.append(row)
    return turns


def load_messages(conversation_id: str) -> list[dict[str, str]]:
    """Return turns as a simple ``[{role, content}, ...]`` list suitable
    for passing to ``run_agent`` as conversation context."""
    return [
        {"role": t["role"], "content": t["content"]}
        for t in load_turns(conversation_id)
        if t.get("role") and t.get("content")
    ]


def list_conversations(agent_name: str | None = None) -> list[dict[str, Any]]:
    """Return headers for all conversations, optionally filtered by agent."""
    _ensure_dir()
    results: list[dict[str, Any]] = []
    for path in sorted(CONVERSATIONS_DIR.glob("*.jsonl")):
        try:
            first = path.read_text(encoding="utf-8").split("\n", 1)[0]
            row = json.loads(first)
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("type") != "header":
            continue
        if agent_name and row.get("agent_name") != agent_name:
            continue
        results.append(row)
    return results


def fork(conversation_id: str, *, agent_name: str | None = None) -> str:
    """Create a new conversation by copying all turns from an existing one.

    The new conversation gets a fresh id with ``parent_id`` pointing to
    the source.  If *agent_name* is not provided it is read from the
    source header.  Returns the new conversation_id.
    """
    header = load_header(conversation_id)
    if header is None:
        raise StateError(f"cannot fork: conversation {conversation_id} not found")
    name = agent_name or header.get("agent_name", "unknown")
    turns = load_turns(conversation_id)
    new_cid = create(name, parent_id=conversation_id)
    for turn in turns:
        append_turn(
            new_cid,
            role=turn["role"],
            content=turn["content"],
            run_id=turn.get("run_id"),
        )
    return new_cid


def clear(conversation_id: str) -> bool:
    """Delete a conversation file. Returns True if it existed."""
    path = conversation_path(conversation_id)
    if path.exists():
        path.unlink()
        return True
    return False
