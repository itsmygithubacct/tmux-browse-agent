"""Background workflow scheduler.

Runs as a daemon thread owned by the dashboard server.  On each tick
it loads workflow config, checks which workflows are due, and executes
them via ``run_agent``.  Results are recorded in the workflow-runs
history and per-workflow state.

The scheduler only runs if it holds the scheduler lock (see
``agent_scheduler_lock``).  This prevents duplicate execution when
multiple dashboard processes are running.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from . import (
    budgets as agent_budgets,
    hooks as agent_hooks,
    runner as agent_runner,
    scheduler_lock as agent_scheduler_lock,
    store as agent_store,
    workflow_runs as agent_workflow_runs,
    workflows as agent_workflows,
)
from .runs import new_run_id


TICK_INTERVAL = 10  # seconds between scheduler wake-ups


class Scheduler:
    """Background workflow executor."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        """Attempt to acquire the lock and start the scheduler thread.

        Returns True if the scheduler started, False if another process
        holds the lock.
        """
        if not agent_scheduler_lock.acquire():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="workflow-scheduler",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Signal the scheduler to stop and release the lock."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            self._thread = None
        agent_scheduler_lock.release()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            self._stop.wait(TICK_INTERVAL)

    def _tick(self) -> None:
        if not agent_scheduler_lock.is_owned():
            return

        try:
            wf_config = agent_workflows.load()
        except Exception:
            return

        agents_cfg = wf_config.get("agents") or {}
        for agent_name, spec in agents_cfg.items():
            if self._stop.is_set():
                return
            if not spec.get("enabled"):
                continue
            # Check daily budgets before running any workflows
            daily = agent_budgets.check_daily_budget(agent_name)
            if daily["action"] == agent_budgets.ACTION_STOP:
                agent_hooks.execute(
                    "workflow_skipped", agent_name,
                    error="daily budget exceeded")
                continue
            global_d = agent_budgets.check_global_daily_budget()
            if global_d["action"] == agent_budgets.ACTION_STOP:
                agent_hooks.execute(
                    "workflow_skipped", agent_name,
                    error="global daily budget exceeded")
                continue
            workflows = spec.get("workflows") or []
            for idx, wf in enumerate(workflows):
                if self._stop.is_set():
                    return
                prompt = (wf.get("prompt") or "").strip()
                if not prompt:
                    continue
                interval = int(wf.get("interval_seconds", 300))
                if not agent_workflow_runs.is_due(agent_name, idx, interval):
                    continue
                self._run_workflow(agent_name, idx, prompt, interval)

    def _run_workflow(self, agent_name: str, workflow_idx: int,
                      prompt: str, interval: int) -> None:
        run_id = new_run_id()
        try:
            agent = agent_store.get_agent(agent_name)
        except Exception as e:
            agent_workflow_runs.record_result(
                agent_name, workflow_idx,
                status="error", run_id=run_id,
                interval_seconds=interval,
                error=f"agent not configured: {e}",
            )
            return
        sandbox_spec = None
        if agent.get("sandbox") == "docker":
            sandbox_spec = {
                "mode": "docker",
                "workspace": str(self._repo_root),
            }
        try:
            agent_runner.run_agent(
                agent, prompt,
                repo_root=self._repo_root,
                max_steps=20,
                request_timeout=90.0,
                origin="scheduler",
                run_id=run_id,
                sandbox_spec=sandbox_spec,
            )
            agent_workflow_runs.record_result(
                agent_name, workflow_idx,
                status="ok", run_id=run_id,
                interval_seconds=interval,
            )
        except Exception as e:
            agent_workflow_runs.record_result(
                agent_name, workflow_idx,
                status="error", run_id=run_id,
                interval_seconds=interval,
                error=str(e),
            )
            agent_hooks.execute(
                "run_failed", agent_name,
                run_id=run_id, prompt=prompt, error=str(e))
