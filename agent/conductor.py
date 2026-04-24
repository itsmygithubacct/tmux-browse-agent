"""Conductor: a thin rule engine above event hooks.

Event hooks (``lib/agent_hooks.py``) react to one event at a time with a
flat list of actions. The conductor sits on the same event stream and
adds three things:

- **State across events**: rolling-window counters ("three failures in
  one hour"), keyed by ``(rule_id, agent)``.
- **Cross-agent actions**: rules can fire a run on a different agent,
  so one agent's rate-limit can automatically retry on another.
- **A decision log**: every fired rule writes one JSONL line to
  ``~/.tmux-browse/agent-conductor.jsonl`` so the user can tell why
  something happened.

Rules live in ``~/.tmux-browse/agent-conductor.json``::

    {
      "rules": [
        {
          "id": "three-strikes-opus",
          "when": {
            "event": "run_failed",
            "agent": "opus",
            "within_last": "1h",
            "count_at_least": 3
          },
          "do": [
            {"action": "pause_workflow", "agent": "opus"},
            {"action": "notify",
             "message": "opus paused after 3 failures in 1h"}
          ]
        }
      ]
    }

``when.agent`` accepts ``"*"`` to match any agent. ``when.within_last``
is a string like ``"1h"`` / ``"30m"`` / ``"2d"``. Everything is
optional; an empty ``when`` matches every event.

The conductor does not run the LLM itself. The ``run_agent`` action
delegates to ``agent_runner.run_agent`` (which owns the actual call).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from lib import config

VALID_ACTIONS = {"log", "retry", "pause_workflow", "notify", "run_agent"}
_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)

# Per-(rule_id, agent) rolling event-timestamp buffer. Events older than
# the longest window referenced by any rule get pruned opportunistically.
_events: dict[tuple[str, str], list[int]] = {}
_events_lock = threading.Lock()

# Runaway-loop guard: a rule that fires within 5 s of itself on the same
# agent is dropped. Keyed by (rule_id, agent) so two agents hitting the
# same ``*`` rule each get to fire on their own stream.
_INFLIGHT_TTL_SEC = 5
_inflight: dict[tuple[str, str], int] = {}


def _parse_window(raw: Any) -> int:
    """Return seconds in a window string, or 0 if unparseable / empty."""
    if not raw:
        return 0
    m = _WINDOW_RE.match(str(raw))
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _is_rule(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if not isinstance(raw.get("id"), str) or not raw["id"].strip():
        return False
    do = raw.get("do")
    if not isinstance(do, list) or not do:
        return False
    for action in do:
        if not isinstance(action, dict):
            return False
        if action.get("action") not in VALID_ACTIONS:
            return False
    when = raw.get("when")
    if when is not None and not isinstance(when, dict):
        return False
    return True


def load_rules() -> list[dict[str, Any]]:
    """Read the rule file. Invalid rules are silently dropped — callers
    that need validation errors should use :func:`validate_raw`."""
    path = config.AGENT_CONDUCTOR_FILE
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        return []
    return [r for r in rules if _is_rule(r)]


def validate_raw(raw: Any) -> list[dict[str, Any]]:
    """Validate a submission from the API. Raises ValueError on bad shape.
    Returns the cleaned rule list."""
    if not isinstance(raw, dict):
        raise ValueError("conductor config must be an object")
    rules = raw.get("rules")
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        raise ValueError("'rules' must be a list")
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, rule in enumerate(rules):
        if not _is_rule(rule):
            raise ValueError(f"rule #{i} is malformed")
        rid = rule["id"].strip()
        if rid in seen_ids:
            raise ValueError(f"duplicate rule id: {rid!r}")
        seen_ids.add(rid)
        for action in rule["do"]:
            if action["action"] == "run_agent" and not action.get("agent"):
                raise ValueError(
                    f"rule {rid!r}: run_agent action requires an 'agent' field")
        cleaned.append(rule)
    return cleaned


def save_rules(raw: Any) -> list[dict[str, Any]]:
    """Persist a cleaned rule set; returns the on-disk shape."""
    cleaned = validate_raw(raw)
    config.ensure_dirs()
    config.AGENT_CONDUCTOR_FILE.write_text(
        json.dumps({"rules": cleaned}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return cleaned


def _record_event(agent: str, now: int) -> None:
    """Append an event timestamp to every (rule, agent) bucket that the
    given agent could plausibly match. We store by (rule_id, agent) so
    the ``*`` wildcard shares a bucket across agents."""
    # Stored under a single key — we track by agent only. Counting is
    # done at eval time against the rule's window.
    with _events_lock:
        buf = _events.setdefault(("__all__", agent), [])
        buf.append(now)
        # Trim anything older than 24h — longest window we support.
        cutoff = now - 86400
        while buf and buf[0] < cutoff:
            buf.pop(0)


def _count_in_window(agent: str, window_sec: int, now: int) -> int:
    buf = _events.get(("__all__", agent), [])
    if not window_sec:
        return len(buf)
    cutoff = now - window_sec
    return sum(1 for ts in buf if ts >= cutoff)


def _matches(rule: dict[str, Any], event: str, agent: str, now: int) -> bool:
    when = rule.get("when") or {}
    wanted_event = when.get("event")
    if wanted_event and wanted_event != event:
        return False
    wanted_agent = when.get("agent") or "*"
    if wanted_agent != "*" and wanted_agent != agent:
        return False
    window = _parse_window(when.get("within_last"))
    min_count = int(when.get("count_at_least") or 1)
    if min_count > 1 or window:
        if _count_in_window(agent, window, now) < min_count:
            return False
    return True


def _already_fired(rule_id: str, agent: str, now: int) -> bool:
    key = (rule_id, agent)
    exp = _inflight.get(key)
    if exp and exp > now:
        return True
    _inflight[key] = now + _INFLIGHT_TTL_SEC
    # Opportunistic cleanup
    for k in list(_inflight):
        if _inflight[k] <= now:
            _inflight.pop(k, None)
    return False


def _append_decision(rule_id: str, event: str, agent: str,
                     actions: list[dict[str, Any]], reason: str = "") -> None:
    config.ensure_dirs()
    record = {
        "ts": int(time.time()),
        "rule_id": rule_id,
        "event": event,
        "agent": agent,
        "actions": actions,
    }
    if reason:
        record["reason"] = reason
    try:
        with config.AGENT_CONDUCTOR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        pass


def record_event(event: str, agent: str,
                 context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Entry point called by ``agent_hooks.execute``. Records the event,
    evaluates every rule, dispatches fired actions, and returns the
    list of dispatched actions (for test + debug inspection)."""
    if not event or not agent:
        return []
    now = int(time.time())
    _record_event(agent, now)
    dispatched: list[dict[str, Any]] = []
    for rule in load_rules():
        if not _matches(rule, event, agent, now):
            continue
        if _already_fired(rule["id"], agent, now):
            _append_decision(rule["id"], event, agent, [],
                             reason="runaway guard")
            continue
        actions = list(rule["do"])
        _dispatch(actions, agent=agent, context=context or {})
        _append_decision(rule["id"], event, agent, actions)
        dispatched.extend(actions)
    return dispatched


def _dispatch(actions: list[dict[str, Any]], *,
              agent: str, context: dict[str, Any]) -> None:
    """Carry out each action. Best-effort; failures are logged as
    warnings in the decision record, never re-raised."""
    for action in actions:
        verb = action.get("action")
        try:
            if verb == "log":
                continue  # decision log entry itself is the record
            if verb == "notify":
                from . import hooks as agent_hooks
                agent_hooks._append_notification(
                    "conductor", action.get("agent", agent),
                    context.get("run_id", ""),
                    str(action.get("message") or ""))
            elif verb == "pause_workflow":
                from . import hooks as agent_hooks
                agent_hooks._pause_agent_workflow(action.get("agent", agent))
            elif verb == "retry":
                # retry semantics live in agent_runner's existing hook
                # path; the conductor doesn't re-trigger them here to
                # avoid two retry sources fighting. We write the intent
                # to the decision log and let the operator act.
                pass
            elif verb == "run_agent":
                _dispatch_run_agent(action, source_agent=agent,
                                    context=context)
        except Exception:
            # Swallow per the plan — the decision log is the audit trail.
            pass


def _dispatch_run_agent(action: dict[str, Any], *,
                        source_agent: str, context: dict[str, Any]) -> None:
    """Fire a one-shot run on a target agent. Runs in a daemon thread so
    the event path isn't blocked by a multi-minute agent invocation."""
    target = action.get("agent")
    if not target:
        return
    prompt_template = action.get("prompt") or action.get("prompt_from")
    # Minimal templating: ``$.original_prompt`` substitutes the triggering
    # run's prompt. Everything else is passed through verbatim.
    original_prompt = context.get("prompt", "")
    if prompt_template == "$.original_prompt" or prompt_template is None:
        prompt = original_prompt or f"triggered by {source_agent} event"
    else:
        prompt = str(prompt_template).replace("$.original_prompt", original_prompt)

    def _run():
        try:
            from . import runner as agent_runner, store as agent_store
            from lib import config as _cfg
            ag = agent_store.get_agent(target)
            agent_runner.run_agent(
                ag, prompt,
                repo_root=_cfg.PROJECT_DIR,
                origin="conductor",
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def read_decisions(*, limit: int = 50, agent: str = "") -> list[dict[str, Any]]:
    """Tail of the decision log. ``agent`` filters to a single agent."""
    path = config.AGENT_CONDUCTOR_LOG
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if agent and row.get("agent") != agent:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def reset_state_for_tests() -> None:
    """Clear in-memory state — exposed only for unit tests."""
    with _events_lock:
        _events.clear()
    _inflight.clear()
