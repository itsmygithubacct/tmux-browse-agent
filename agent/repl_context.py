"""Per-REPL context: exec target, observed panes, mode, tick.

A small JSON blob per agent describing how `tb agent repl` should behave
on its next turn. tmuxai-style concepts adapted to tmux-browse's
single-tool (`tb_command`) loop:

- ``exec_target`` — default tmux target the agent sends its actions to.
  In Docker sandbox mode this is forced to ``sandbox:``; otherwise it's
  user-picked.
- ``observed_panes`` — read-only sessions whose state the agent sees at
  turn start. The content doesn't trigger tool calls; it decorates the
  system prompt so the model has context.
- ``mode`` — ``observe`` / ``act`` / ``watch``. MVP only threads the
  value through; the watch-mode auto-turn loop is a follow-up.
- ``tick_sec`` — watch-mode poll interval (advisory; watcher not yet
  wired to use it).

Persistence: ``~/.tmux-browse/agent-contexts/<agent>.json``.
"""

from __future__ import annotations

import json
from typing import Any

from lib import config

VALID_MODES = {"observe", "act", "watch"}
MAX_OBSERVED_PANES = 8
DEFAULT_TICK_SEC = 10
MIN_TICK_SEC = 5


def _path(agent_name: str):
    return config.AGENT_CONTEXT_DIR / f"{agent_name}.json"


def _default() -> dict[str, Any]:
    return {
        "exec_target": "",
        "observed_panes": [],
        "mode": "observe",
        "tick_sec": DEFAULT_TICK_SEC,
    }


def load(agent_name: str) -> dict[str, Any]:
    """Return the context for an agent, filling defaults."""
    path = _path(agent_name)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data
        except (OSError, ValueError):
            raw = {}
    out = _default()
    if isinstance(raw.get("exec_target"), str):
        out["exec_target"] = raw["exec_target"].strip()
    panes = raw.get("observed_panes")
    if isinstance(panes, list):
        out["observed_panes"] = [
            p.strip() for p in panes[:MAX_OBSERVED_PANES]
            if isinstance(p, str) and p.strip()
        ]
    mode = raw.get("mode")
    if isinstance(mode, str) and mode in VALID_MODES:
        out["mode"] = mode
    try:
        tick = int(raw.get("tick_sec") or DEFAULT_TICK_SEC)
        out["tick_sec"] = max(MIN_TICK_SEC, tick)
    except (TypeError, ValueError):
        out["tick_sec"] = DEFAULT_TICK_SEC
    return out


def save(agent_name: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Persist a context after normalising it."""
    config.ensure_dirs()
    config.AGENT_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    normalised = _default()
    if isinstance(ctx.get("exec_target"), str):
        normalised["exec_target"] = ctx["exec_target"].strip()
    panes = ctx.get("observed_panes")
    if isinstance(panes, list):
        seen = set()
        out_panes: list[str] = []
        for p in panes:
            if not isinstance(p, str):
                continue
            t = p.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out_panes.append(t)
            if len(out_panes) >= MAX_OBSERVED_PANES:
                break
        normalised["observed_panes"] = out_panes
    mode = ctx.get("mode")
    if isinstance(mode, str) and mode in VALID_MODES:
        normalised["mode"] = mode
    try:
        tick = int(ctx.get("tick_sec") or DEFAULT_TICK_SEC)
        normalised["tick_sec"] = max(MIN_TICK_SEC, tick)
    except (TypeError, ValueError):
        pass
    _path(agent_name).write_text(
        json.dumps(normalised, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return normalised


def add_observed(agent_name: str, target: str) -> dict[str, Any]:
    ctx = load(agent_name)
    target = (target or "").strip()
    if not target:
        return ctx
    panes = ctx["observed_panes"]
    if target in panes:
        return ctx
    if len(panes) >= MAX_OBSERVED_PANES:
        raise ValueError(
            f"observed-pane limit of {MAX_OBSERVED_PANES} reached")
    panes.append(target)
    return save(agent_name, ctx)


def remove_observed(agent_name: str, target: str) -> dict[str, Any]:
    ctx = load(agent_name)
    target = (target or "").strip()
    ctx["observed_panes"] = [p for p in ctx["observed_panes"] if p != target]
    return save(agent_name, ctx)


def set_exec_target(agent_name: str, target: str) -> dict[str, Any]:
    ctx = load(agent_name)
    ctx["exec_target"] = (target or "").strip()
    return save(agent_name, ctx)


def set_mode(agent_name: str, mode: str) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    ctx = load(agent_name)
    ctx["mode"] = mode
    return save(agent_name, ctx)


def set_tick(agent_name: str, seconds: int) -> dict[str, Any]:
    ctx = load(agent_name)
    ctx["tick_sec"] = max(MIN_TICK_SEC, int(seconds))
    return save(agent_name, ctx)


def render_block(ctx: dict[str, Any]) -> str:
    """Render a system-prompt suffix describing the REPL context. Empty
    string when the context is the default (so runs without /exec or
    /watch don't carry extra tokens)."""
    lines: list[str] = []
    if ctx.get("exec_target"):
        lines.append(f"Default exec target: {ctx['exec_target']}")
    panes = ctx.get("observed_panes") or []
    if panes:
        lines.append("Observed panes (read-only context): "
                     + ", ".join(panes))
    mode = ctx.get("mode") or "observe"
    if mode != "observe":
        lines.append(f"Mode: {mode}")
    if not lines:
        return ""
    return "\n---\n\nREPL context:\n" + "\n".join(f"- {l}" for l in lines) + "\n"
