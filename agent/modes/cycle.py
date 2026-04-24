"""Cycle mode: one planning-then-execute turn.

Composition over infrastructure: cycle is implemented by calling
:func:`agent_runner.run_agent` twice — once in plan phase, once in
execute phase — with distinct system-prompt suffixes. Both runs are
indexed normally with distinct ``origin`` values, so cost accounting
and run-history search keep working without mode-specific plumbing.

The plan phase is constrained to ``max_steps=1`` so it returns a plan
as its final message rather than starting a tool loop. The execute
phase runs with the plan as its prompt and the full step budget.

No loop, no scheduler, no new conversation store. One invocation = one
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import (
    repl_context as agent_repl_context,
    runner as agent_runner,
    runtime as agent_runtime,
)
from lib import config


PLAN_PROMPT_SUFFIX = """

---

You are in the PLAN phase of a cycle. Read the goal and respond with a
short numbered plan as your FINAL message. Do not call any tools. The
execute phase will run with your plan as its prompt.

Respond with JSON only, one object:
{"type":"final","message":"1. step one\\n2. step two\\n..."}
"""

EXECUTE_PROMPT_PREFIX = """Execute the following plan using tb_command
tool calls. Verify each step before moving to the next.

Plan:
"""


@dataclass
class CycleResult:
    plan_run_id: str
    exec_run_id: str
    plan_message: str
    exec_message: str


def _load_goal(agent_name: str, *, goal_path: str | None = None,
               goal_text: str | None = None) -> str:
    if goal_text and goal_text.strip():
        return goal_text.strip()
    if goal_path:
        return Path(goal_path).expanduser().read_text(encoding="utf-8").strip()
    default = config.STATE_DIR / "agent-cycle" / f"{agent_name}.txt"
    if default.exists():
        return default.read_text(encoding="utf-8").strip()
    return ""


def _plan_prompt(goal: str) -> str:
    if not goal:
        return (
            "Propose a plan consistent with this conversation's prior "
            "context. If there's no prior context, propose a small, "
            "concrete investigation of the current tmux state."
        )
    return f"Goal: {goal}"


def run(agent_cfg: dict[str, Any], *,
        goal_path: str | None = None,
        goal_text: str | None = None,
        steps: int = 20,
        request_timeout: float = 90.0,
        repo_root: Path | None = None) -> CycleResult:
    """Run one cycle: plan phase then execute phase.

    Returns the two run_ids and final messages. Both runs are indexed
    with ``origin="cycle-plan"`` and ``origin="cycle-exec"``.
    """
    name = agent_cfg["name"]
    repo_root = repo_root or config.PROJECT_DIR
    goal = _load_goal(name, goal_path=goal_path, goal_text=goal_text)

    # Plan-phase prompt: we can't modify run_agent's SYSTEM_PROMPT from
    # here, so we stuff the phase instructions into the user prompt. The
    # model still sees them; they land below any repl_context block.
    plan_prompt_full = _plan_prompt(goal) + PLAN_PROMPT_SUFFIX
    ctx = agent_repl_context.load(name)

    plan_result = agent_runner.run_agent(
        agent_cfg,
        plan_prompt_full,
        repo_root=repo_root,
        max_steps=1,
        request_timeout=request_timeout,
        origin="cycle-plan",
        repl_context=ctx,
    )
    plan_message = (plan_result.get("message") or "").strip()

    # Record the plan as an assistant turn so subsequent cycles see it.
    try:
        agent_runtime.record_turn(name, role="assistant",
                                  content=plan_message,
                                  run_id=plan_result.get("run_id"))
    except Exception:
        pass

    # Execute phase.
    exec_prompt = EXECUTE_PROMPT_PREFIX + plan_message
    exec_result = agent_runner.run_agent(
        agent_cfg,
        exec_prompt,
        repo_root=repo_root,
        max_steps=max(1, steps),
        request_timeout=request_timeout,
        origin="cycle-exec",
        repl_context=ctx,
    )

    try:
        agent_runtime.record_turn(name, role="assistant",
                                  content=exec_result.get("message") or "",
                                  run_id=exec_result.get("run_id"))
    except Exception:
        pass

    return CycleResult(
        plan_run_id=plan_result.get("run_id", ""),
        exec_run_id=exec_result.get("run_id", ""),
        plan_message=plan_message,
        exec_message=(exec_result.get("message") or "").strip(),
    )
