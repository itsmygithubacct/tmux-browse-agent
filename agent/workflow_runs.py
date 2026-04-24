"""Workflow execution history and per-workflow runtime state.

Two files:
- ``agent-workflow-runs.jsonl`` — append-only log of each workflow
  execution (agent, workflow index, status, run_id, timestamps).
- ``agent-workflow-state.json`` — compact per-workflow state dict
  (last_run_ts, next_run_ts, last_status, consecutive_failures).

The state file is rewritten atomically on each update.  It is the
scheduler's view of "what to run next" and the dashboard's view of
"what happened last."
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from lib import config
from lib.errors import StateError


RUNS_FILE = config.STATE_DIR / "agent-workflow-runs.jsonl"
STATE_FILE = config.STATE_DIR / "agent-workflow-state.json"


# ---------------------------------------------------------------------------
# Run log (append-only)
# ---------------------------------------------------------------------------

def append_run(record: dict[str, Any]) -> None:
    config.ensure_dirs()
    row = dict(record)
    row.setdefault("ts", int(time.time()))
    try:
        with RUNS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot write {RUNS_FILE}: {e.strerror or e}")


def read_runs(*, limit: int = 100) -> list[dict[str, Any]]:
    if not RUNS_FILE.exists():
        return []
    try:
        lines = RUNS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise StateError(f"cannot read {RUNS_FILE}: {e.strerror or e}")
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Per-workflow state (rewritten atomically)
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(data: dict[str, Any]) -> None:
    config.ensure_dirs()
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as e:
        raise StateError(f"cannot write {STATE_FILE}: {e.strerror or e}")


def _key(agent_name: str, workflow_idx: int) -> str:
    return f"{agent_name}:{workflow_idx}"


def get_workflow_state(agent_name: str, workflow_idx: int) -> dict[str, Any]:
    """Return runtime state for one workflow slot."""
    state = _load_state()
    return state.get(_key(agent_name, workflow_idx), {})


def get_all_state() -> dict[str, Any]:
    """Return the full state dict (for the dashboard API)."""
    return _load_state()


def record_result(agent_name: str, workflow_idx: int, *,
                  status: str, run_id: str | None = None,
                  interval_seconds: int = 300,
                  error: str | None = None) -> None:
    """Update per-workflow state after an execution attempt."""
    now = int(time.time())
    state = _load_state()
    k = _key(agent_name, workflow_idx)
    prev = state.get(k, {})

    failures = int(prev.get("consecutive_failures", 0))
    if status == "ok":
        failures = 0
    else:
        failures += 1

    state[k] = {
        "last_run_ts": now,
        "next_run_ts": now + interval_seconds,
        "last_status": status,
        "last_run_id": run_id,
        "last_error": error,
        "consecutive_failures": failures,
    }
    _save_state(state)

    append_run({
        "agent": agent_name,
        "workflow_idx": workflow_idx,
        "status": status,
        "run_id": run_id,
        "error": error,
    })


def is_due(agent_name: str, workflow_idx: int, interval_seconds: int) -> bool:
    """Return True if enough time has passed since the last run."""
    state = _load_state()
    k = _key(agent_name, workflow_idx)
    ws = state.get(k, {})
    last = int(ws.get("last_run_ts", 0))
    return int(time.time()) - last >= interval_seconds
