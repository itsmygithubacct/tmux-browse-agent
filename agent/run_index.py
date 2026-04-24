"""File-backed index of completed agent runs.

Each completed or failed ``run_agent`` call appends a compact summary
row to ``~/.tmux-browse/agent-run-index.jsonl``.  The index supports
filtered queries by agent, status, time range, free-text search, and
tool name — good enough for hundreds of runs without needing SQLite.

Row schema::

    run_id          str     unique run identifier
    agent           str     agent name
    status          str     run_completed | run_failed | run_rate_limited
    started_ts      int     epoch seconds
    finished_ts     int     epoch seconds
    duration_s      int     finished - started
    steps           int     number of agent steps
    prompt_preview  str     first 120 chars of prompt
    message_preview str     first 120 chars of final message (or error)
    tools_used      list    de-duplicated tb command verbs used
    origin          str     cli | repl | scheduler
    model           str     model name
"""

from __future__ import annotations

import json
import time
from typing import Any

from lib import config
from lib.errors import StateError


INDEX_FILE = config.STATE_DIR / "agent-run-index.jsonl"


def _preview(text: str, limit: int = 120) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def _extract_tools(transcript: list[dict[str, Any]]) -> list[str]:
    """Extract unique tb command verbs from a run transcript."""
    seen: set[str] = set()
    for step in transcript:
        action = step.get("action")
        if not isinstance(action, dict):
            continue
        args = action.get("args")
        if isinstance(args, list) and args:
            verb = str(args[0])
            seen.add(verb)
    return sorted(seen)


def append(*, run_id: str, agent: str, status: str,
           started_ts: int, finished_ts: int | None = None,
           steps: int = 0, prompt: str = "", message: str = "",
           error: str | None = None, origin: str = "",
           model: str = "", transcript: list | None = None) -> None:
    """Append a summary row for a completed/failed run."""
    config.ensure_dirs()
    now = int(time.time())
    fin = finished_ts or now
    row: dict[str, Any] = {
        "run_id": run_id,
        "agent": agent,
        "status": status,
        "started_ts": started_ts,
        "finished_ts": fin,
        "duration_s": max(0, fin - started_ts),
        "steps": steps,
        "prompt_preview": _preview(prompt),
        "message_preview": _preview(message or (error or "")),
        "tools_used": _extract_tools(transcript or []),
        "origin": origin,
        "model": model,
    }
    try:
        with INDEX_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot write {INDEX_FILE}: {e.strerror or e}")


def query(*, agent: str | None = None, status: str | None = None,
          since: int | None = None, until: int | None = None,
          text: str | None = None, tool: str | None = None,
          origin: str | None = None,
          limit: int = 100) -> list[dict[str, Any]]:
    """Query the run index with optional filters.

    Returns rows in reverse chronological order (newest first).
    """
    if not INDEX_FILE.exists():
        return []
    try:
        lines = INDEX_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise StateError(f"cannot read {INDEX_FILE}: {e.strerror or e}")

    text_lower = text.lower() if text else None
    tool_lower = tool.lower() if tool else None

    results: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(results) >= limit:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if agent and row.get("agent") != agent:
            continue
        if status and row.get("status") != status:
            continue
        if origin and row.get("origin") != origin:
            continue
        ts = int(row.get("finished_ts") or row.get("started_ts") or 0)
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        if text_lower:
            haystack = (
                str(row.get("prompt_preview", "")) + " " +
                str(row.get("message_preview", ""))
            ).lower()
            if text_lower not in haystack:
                continue
        if tool_lower:
            tools = row.get("tools_used") or []
            if not any(tool_lower == t.lower() for t in tools):
                continue
        results.append(row)
    return results


def get_run(run_id: str) -> dict[str, Any] | None:
    """Look up a single run by run_id."""
    if not INDEX_FILE.exists():
        return None
    try:
        lines = INDEX_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            return row
    return None
