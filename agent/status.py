"""Derive live status for each configured agent.

Status is inferred from the most recent log entry, active conversation
sessions, and workflow configuration.  No polling or long-lived state is
needed — each call reads the latest data and derives a snapshot.
"""

from __future__ import annotations

import time
from typing import Any

from . import logs as agent_logs, store as agent_store, workflows as agent_workflows
from .runs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RATE_LIMITED,
    STATUS_STARTED,
)


# How recently a run_started entry must be to count as "running".
RUNNING_THRESHOLD_SECONDS = 300


def _mode_and_phase(origin: str) -> tuple[str, str]:
    """Extract a mode name + phase from a run's ``origin`` tag.

    Returns (mode, phase) where either may be empty. Keeps the schema
    string-based so future modes don't need parser updates here.
    """
    if not origin:
        return "", ""
    if origin.startswith("cycle"):
        phase = origin.split("-", 1)[1] if "-" in origin else ""
        return "cycle", phase
    if origin in ("work", "drive"):
        return origin, ""
    return "", ""


class AgentStatus:
    RUNNING = "running"
    IDLE = "idle"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    WORKFLOW_PAUSED = "workflow_paused"


def _workflow_paused(agent_name: str) -> bool:
    """Return True if the agent has workflows defined but all disabled."""
    try:
        wf = agent_workflows.load()
    except Exception:
        return False
    agent_cfg = (wf.get("agents") or {}).get(agent_name)
    if not agent_cfg:
        return False
    workflows = agent_cfg.get("workflows") or []
    has_any = any(
        w.get("name") or w.get("prompt")
        for w in workflows
        if isinstance(w, dict)
    )
    if not has_any:
        return False
    return not agent_cfg.get("enabled", False)


def get_status(agent_name: str) -> dict[str, Any]:
    """Return the derived status for a single agent.

    Returns a dict with:
        status:    one of AgentStatus constants
        reason:    short human-readable explanation
        last_ts:   epoch timestamp of last activity (0 if none)
        last_status: raw status string from the log entry (if any)
    """
    name = (agent_name or "").strip().lower()
    entry = agent_logs.get_latest_entry(name)
    now = int(time.time())

    if entry is None:
        if _workflow_paused(name):
            return {
                "status": AgentStatus.WORKFLOW_PAUSED,
                "reason": "workflows defined but disabled",
                "last_ts": 0,
                "last_status": None,
                "mode": "",
                "mode_phase": "",
            }
        return {
            "status": AgentStatus.IDLE,
            "reason": "no activity recorded",
            "last_ts": 0,
            "last_status": None,
            "mode": "",
            "mode_phase": "",
        }

    entry_ts = int(entry.get("ts") or 0)
    entry_status = str(entry.get("status") or "")
    origin = str(entry.get("origin") or "")
    mode, phase = _mode_and_phase(origin)
    age = now - entry_ts

    def _wrap(base: dict[str, Any]) -> dict[str, Any]:
        base["mode"] = mode
        base["mode_phase"] = phase
        return base

    # Currently running: started recently and no completion/failure yet.
    if entry_status == STATUS_STARTED and age < RUNNING_THRESHOLD_SECONDS:
        return _wrap({
            "status": AgentStatus.RUNNING,
            "reason": _preview_prompt(entry),
            "last_ts": entry_ts,
            "last_status": entry_status,
        })

    # Rate limited.
    if entry_status == STATUS_RATE_LIMITED:
        return _wrap({
            "status": AgentStatus.RATE_LIMITED,
            "reason": str(entry.get("error") or "rate limited"),
            "last_ts": entry_ts,
            "last_status": entry_status,
        })

    # Failed (not rate-limited).
    if entry_status == STATUS_FAILED:
        return _wrap({
            "status": AgentStatus.ERROR,
            "reason": str(entry.get("error") or "run failed"),
            "last_ts": entry_ts,
            "last_status": entry_status,
        })

    # Workflow paused takes precedence over idle.
    if _workflow_paused(name):
        return _wrap({
            "status": AgentStatus.WORKFLOW_PAUSED,
            "reason": "workflows defined but disabled",
            "last_ts": entry_ts,
            "last_status": entry_status,
        })

    # Default: idle (completed or old started entry).
    reason = "idle"
    if entry_status == STATUS_COMPLETED:
        msg = str(entry.get("message") or "").strip()
        reason = f"last run ok" + (f": {msg[:60]}" if msg else "")
    elif entry_status == STATUS_STARTED and age >= RUNNING_THRESHOLD_SECONDS:
        reason = "last run may have stalled"

    return _wrap({
        "status": AgentStatus.IDLE,
        "reason": reason,
        "last_ts": entry_ts,
        "last_status": entry_status,
    })


def get_all_statuses() -> dict[str, dict[str, Any]]:
    """Return statuses keyed by agent name for every configured agent."""
    out: dict[str, dict[str, Any]] = {}
    for row in agent_store.list_agents():
        name = row.get("name", "")
        out[name] = get_status(name)
    return out


def _preview_prompt(entry: dict[str, Any]) -> str:
    prompt = str(entry.get("prompt") or "").strip()
    if len(prompt) > 60:
        return prompt[:57] + "..."
    return prompt or "running"
