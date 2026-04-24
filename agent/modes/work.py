"""Work mode: autonomous task-queue runner.

Pick a task from a source, run it via :func:`agent_runner.run_agent`,
loop until the source is empty or an abort condition fires.

The mode owns three concerns only:

1. Loop control (current task + counters)
2. The task-source abstraction (``TaskSource``) — MVP ships
   :class:`FileSource`; workflow and hook sources are follow-ups.
3. Abort predicates — daily budget, step caps, operator stop,
   optional stop-on-error.

Every task becomes a run with ``origin="work"`` in the existing run
index. Completed task ids append to
``~/.tmux-browse/agent-work/<agent>/<source_name>.done`` so a
re-invocation against the same source resumes cleanly.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .. import (
    budgets as agent_budgets,
    repl_context as agent_repl_context,
    runner as agent_runner,
)
from lib import config


@dataclass
class Task:
    id: str
    prompt: str
    meta: dict[str, Any] = field(default_factory=dict)


class TaskSource(Protocol):
    name: str
    def iter_pending(self) -> Iterable[Task]: ...
    def mark_done(self, task: Task, status: str) -> None: ...


@dataclass
class WorkResult:
    status: str  # "done" (queue emptied), "empty", "stopped", "budget", "step_cap", "error"
    total_tasks: int
    completed: int
    failed: int
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.status in ("done", "empty"),
            "status": self.status,
            "total_tasks": self.total_tasks,
            "completed": self.completed,
            "failed": self.failed,
            "last_error": self.last_error,
        }


# --- FileSource: plaintext / JSONL file, resumable via .done sibling ---

class FileSource:
    """Read tasks from a path. One task per line.

    Plaintext lines become ``Task(id=md5(line), prompt=line)``. JSON
    lines with an ``{"id","prompt"}`` shape are honoured as-is.

    The ``.done`` sibling file is append-only and contains task ids
    that completed successfully; resuming skips those.
    """

    def __init__(self, *, path: Path, agent_name: str):
        self.path = Path(path).expanduser()
        self.name = self.path.name
        self._agent = agent_name
        self._done_path = (config.STATE_DIR / "agent-work" / agent_name
                           / (self.name + ".done"))
        self._done_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_done(self) -> set[str]:
        if not self._done_path.exists():
            return set()
        try:
            return {line.strip() for line in
                    self._done_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()}
        except OSError:
            return set()

    def iter_pending(self) -> Iterable[Task]:
        if not self.path.exists():
            return
        done = self._load_done()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            task = _parse_task_line(stripped)
            if task.id in done:
                continue
            yield task

    def mark_done(self, task: Task, status: str) -> None:
        # Only acked tasks get replayed-skipped on resume. Failures
        # stay in the queue unless --stop-on-error halts the loop.
        if status == "ok":
            try:
                with self._done_path.open("a", encoding="utf-8") as fh:
                    fh.write(task.id + "\n")
            except OSError:
                pass


def _parse_task_line(line: str) -> Task:
    # Try JSON first; plaintext fallback.
    if line.startswith("{"):
        try:
            raw = json.loads(line)
        except ValueError:
            raw = None
        if isinstance(raw, dict) and "prompt" in raw:
            tid = str(raw.get("id") or
                      hashlib.md5(raw["prompt"].encode()).hexdigest()[:12])
            # If there's an explicit nested "meta" dict, use it; otherwise
            # collect sibling fields (everything except id / prompt).
            meta = raw.get("meta")
            if not isinstance(meta, dict):
                meta = {k: v for k, v in raw.items()
                        if k not in ("id", "prompt")}
            return Task(id=tid, prompt=str(raw["prompt"]), meta=meta)
    return Task(
        id=hashlib.md5(line.encode()).hexdigest()[:12],
        prompt=line,
    )


# --- Stop signalling ---

_stop_flags: dict[str, threading.Event] = {}


def request_stop(agent_name: str) -> None:
    """Best-effort stop for any in-flight run loop on this agent."""
    flag = _stop_flags.get(agent_name)
    if flag is not None:
        flag.set()


def _stop_flag_for(agent_name: str) -> threading.Event:
    flag = _stop_flags.get(agent_name)
    if flag is None:
        flag = threading.Event()
        _stop_flags[agent_name] = flag
    else:
        flag.clear()
    return flag


# --- Core loop ---

def run(agent_cfg: dict[str, Any], *,
        tasks_path: str,
        steps_per_task: int = 20,
        max_total_steps: int = 200,
        stop_on_error: bool = False,
        request_timeout: float = 90.0,
        repo_root: Path | None = None,
        source: TaskSource | None = None) -> WorkResult:
    """Run tasks until the source is empty or an abort fires."""
    name = agent_cfg["name"]
    repo_root = repo_root or config.PROJECT_DIR
    src = source or FileSource(path=Path(tasks_path), agent_name=name)
    stop = _stop_flag_for(name)
    ctx = agent_repl_context.load(name)

    total = 0
    completed = 0
    failed = 0
    cumulative_steps = 0
    last_error = ""
    status = "done"

    for task in src.iter_pending():
        total += 1
        if stop.is_set():
            status = "stopped"
            break
        # Daily budget is the primary external guard; budgets can
        # pause a workflow elsewhere too, so belt + braces.
        budget = agent_budgets.check_daily_budget(name)
        if budget["action"] == agent_budgets.ACTION_STOP:
            status = "budget"
            last_error = budget["reason"]
            break
        if cumulative_steps >= max_total_steps:
            status = "step_cap"
            break
        try:
            result = agent_runner.run_agent(
                agent_cfg, task.prompt,
                repo_root=repo_root,
                max_steps=steps_per_task,
                request_timeout=request_timeout,
                origin="work",
                repl_context=ctx,
            )
            cumulative_steps += int(result.get("steps") or 0)
            completed += 1
            src.mark_done(task, "ok")
        except Exception as e:
            failed += 1
            last_error = str(e)
            src.mark_done(task, "error")
            if stop_on_error:
                status = "error"
                break

    # iter_pending could have yielded nothing (resume after full
    # completion, or file empty) — tag that separately from "done".
    if status == "done" and total == 0:
        status = "empty"

    return WorkResult(
        status=status,
        total_tasks=total,
        completed=completed,
        failed=failed,
        last_error=last_error,
    )
