"""Token budget enforcement for agent runs and workflows.

Three tiers:
- Per-run: max tokens for a single ``run_agent()`` call.
- Per-agent daily: max tokens per agent per UTC day.
- Global daily: max tokens across all agents per UTC day.

Each check returns ``{action, reason, used, limit, pct}`` where action
is one of ACTION_OK / ACTION_WARN / ACTION_STOP.
"""

from __future__ import annotations

import calendar
import time
from typing import Any

from . import costs as agent_costs, store as agent_store
from lib import dashboard_config


ACTION_OK = "ok"
ACTION_WARN = "warn"      # 80-99% of limit
ACTION_STOP = "stop"      # 100%+ of limit

_SEVERITY = {ACTION_OK: 0, ACTION_WARN: 1, ACTION_STOP: 2}


def _today_start() -> int:
    """Epoch seconds for start of today (UTC)."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return calendar.timegm(time.strptime(today, "%Y-%m-%d"))


def _result(action: str, used: int, limit: int) -> dict[str, Any]:
    pct = (used / limit) * 100 if limit else 0
    reason = ""
    if action == ACTION_WARN:
        reason = f"budget {pct:.0f}% used ({used}/{limit})"
    elif action == ACTION_STOP:
        reason = f"budget exceeded ({used}/{limit})"
    return {"action": action, "reason": reason, "used": used,
            "limit": limit, "pct": round(pct, 1)}


def _action_for(used: int, limit: int) -> str:
    if used >= limit:
        return ACTION_STOP
    if (used / limit) * 100 >= 80:
        return ACTION_WARN
    return ACTION_OK


def check_run_budget(agent_name: str, cumulative_usage: dict[str, int],
                     token_budget: int) -> dict[str, Any]:
    """Check per-run token limit. Called after each provider response."""
    if token_budget <= 0:
        return {"action": ACTION_OK}
    used = int(cumulative_usage.get("total_tokens") or 0)
    if not used:
        used = (int(cumulative_usage.get("prompt_tokens") or 0)
                + int(cumulative_usage.get("completion_tokens") or 0))
    return _result(_action_for(used, token_budget), used, token_budget)


def check_daily_budget(agent_name: str) -> dict[str, Any]:
    """Check per-agent daily token limit."""
    try:
        agent = agent_store.get_agent(agent_name)
    except Exception:
        return {"action": ACTION_OK}
    limit = int(agent.get("daily_token_budget") or 0)
    if limit <= 0:
        return {"action": ACTION_OK}
    since = _today_start()
    totals = agent_costs.per_agent_totals(since=since)
    used = totals.get(agent_name, {}).get("total_tokens", 0)
    return _result(_action_for(used, limit), used, limit)


def check_global_daily_budget() -> dict[str, Any]:
    """Check cross-agent global daily token limit."""
    cfg = dashboard_config.load()
    limit = int(cfg.get("global_daily_token_budget") or 0)
    if limit <= 0:
        return {"action": ACTION_OK}
    since = _today_start()
    totals = agent_costs.per_agent_totals(since=since)
    used = sum(t.get("total_tokens", 0) for t in totals.values())
    return _result(_action_for(used, limit), used, limit)


def get_budget_status(agent_name: str) -> dict[str, Any]:
    """Return combined budget status for display."""
    daily = check_daily_budget(agent_name)
    global_d = check_global_daily_budget()
    worst = max([daily, global_d],
                key=lambda r: _SEVERITY.get(r.get("action", ACTION_OK), 0))
    return {
        "daily": daily,
        "global_daily": global_d,
        "worst_action": worst["action"],
    }
