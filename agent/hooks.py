"""Event hook system for agent lifecycle events.

Hooks react to agent events (run_completed, run_failed, etc.) with
configurable actions (log, retry, pause_workflow, notify). Config is
stored at ``~/.tmux-browse/agent-hooks.json`` with optional per-agent
overrides.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import workflows as agent_workflows
from lib import config


HOOKS_FILE = config.AGENT_HOOKS_FILE
NOTIFICATIONS_FILE = config.AGENT_NOTIFICATIONS_FILE

VALID_EVENTS = {
    "run_completed", "run_failed", "run_rate_limited",
    "budget_exceeded", "workflow_skipped",
}
VALID_ACTIONS = {"log", "retry", "pause_workflow", "notify"}

DEFAULT_HOOKS: dict[str, list[str]] = {
    "run_completed": ["log"],
    "run_failed": ["log"],
    "run_rate_limited": ["log"],
    "budget_exceeded": ["log", "pause_workflow"],
    "workflow_skipped": ["log"],
}


def load() -> dict[str, Any]:
    """Load hook config."""
    if not HOOKS_FILE.exists():
        return dict(DEFAULT_HOOKS)
    try:
        raw = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else dict(DEFAULT_HOOKS)
    except (OSError, ValueError):
        return dict(DEFAULT_HOOKS)


def save(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist hook config."""
    config.ensure_dirs()
    out: dict[str, Any] = {}
    for event in VALID_EVENTS:
        actions = raw.get(event)
        if isinstance(actions, list):
            out[event] = [a for a in actions if a in VALID_ACTIONS]
        else:
            out[event] = DEFAULT_HOOKS.get(event, ["log"])
    agents = raw.get("agents")
    if isinstance(agents, dict):
        out["agents"] = {}
        for name, spec in agents.items():
            if not isinstance(spec, dict):
                continue
            agent_hooks: dict[str, list[str]] = {}
            for event in VALID_EVENTS:
                actions = spec.get(event)
                if isinstance(actions, list):
                    agent_hooks[event] = [a for a in actions if a in VALID_ACTIONS]
            if agent_hooks:
                out["agents"][name] = agent_hooks
    HOOKS_FILE.write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def get_actions(event_type: str, agent_name: str) -> list[str]:
    """Return actions for an event, with per-agent overrides."""
    hooks = load()
    agent_overrides = (hooks.get("agents") or {}).get(agent_name, {})
    if event_type in agent_overrides:
        return agent_overrides[event_type]
    return hooks.get(event_type, DEFAULT_HOOKS.get(event_type, ["log"]))


def execute(event_type: str, agent_name: str, *,
            run_id: str = "", prompt: str = "",
            error: str = "") -> list[str]:
    """Evaluate and execute hook actions. Returns list of actions taken."""
    actions = get_actions(event_type, agent_name)
    taken: list[str] = []
    for action in actions:
        if action == "log":
            taken.append("log")
        elif action == "notify":
            _append_notification(event_type, agent_name, run_id, error)
            taken.append("notify")
        elif action == "pause_workflow":
            _pause_agent_workflow(agent_name)
            taken.append("pause_workflow")
        elif action == "retry":
            taken.append("retry")
    # Also fan the event into the conductor's rule engine so cross-event
    # policy (rolling-window counters, cross-agent routing) can react.
    try:
        from . import conductor
        agent_conductor.record_event(
            event_type, agent_name,
            context={"run_id": run_id, "prompt": prompt, "error": error})
    except Exception:
        pass
    return taken


def _append_notification(event_type: str, agent_name: str,
                         run_id: str, error: str) -> None:
    config.ensure_dirs()
    record = {
        "ts": int(time.time()),
        "event": event_type,
        "agent": agent_name,
        "run_id": run_id,
        "error": error[:200] if error else "",
    }
    try:
        with NOTIFICATIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        pass


def _pause_agent_workflow(agent_name: str) -> None:
    """Set the agent's workflow enabled=false."""
    try:
        wf = agent_workflows.load()
        agent_cfg = (wf.get("agents") or {}).get(agent_name)
        if agent_cfg and agent_cfg.get("enabled"):
            agent_cfg["enabled"] = False
            agent_workflows.save(wf)
    except Exception:
        pass


def read_notifications(*, limit: int = 50) -> list[dict[str, Any]]:
    """Read recent notifications."""
    if not NOTIFICATIONS_FILE.exists():
        return []
    try:
        lines = NOTIFICATIONS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            continue
    return rows
