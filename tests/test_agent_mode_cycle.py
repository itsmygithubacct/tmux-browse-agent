"""Cycle mode: two-phase planner + executor built on run_agent."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from lib import config as cfg  # noqa: E402
from agent.modes import cycle as cycle_mode  # noqa: E402


AGENT = {"name": "opus", "model": "claude-opus-4-7", "wire_api": "anthropic-messages"}


def _make_runs(plan_msg: str, exec_msg: str):
    """Return a side_effect list for two run_agent calls."""
    return [
        {"message": plan_msg, "run_id": "plan-run-1", "steps": 1,
         "transcript": [], "usage": {}},
        {"message": exec_msg, "run_id": "exec-run-1", "steps": 2,
         "transcript": [], "usage": {}},
    ]


class _IsolatedCycle:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", root),
            mock.patch.object(cfg, "AGENT_CONTEXT_DIR", root / "ctx"),
            mock.patch.object(cfg, "AGENT_KB_DIR", root / "kb"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class CycleRunTests(_IsolatedCycle, unittest.TestCase):

    def test_two_runs_with_distinct_origins(self):
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("1. look around", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT, goal_text="add tests")
        self.assertEqual(ra.call_count, 2)
        self.assertEqual(ra.call_args_list[0].kwargs["origin"], "cycle-plan")
        self.assertEqual(ra.call_args_list[1].kwargs["origin"], "cycle-exec")

    def test_plan_phase_gets_max_steps_1(self):
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("1. look", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT, goal_text="x", steps=10)
        plan_call = ra.call_args_list[0]
        self.assertEqual(plan_call.kwargs["max_steps"], 1)
        exec_call = ra.call_args_list[1]
        self.assertEqual(exec_call.kwargs["max_steps"], 10)

    def test_execute_prompt_contains_plan(self):
        plan_msg = "1. inspect tmux\n2. report"
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs(plan_msg, "reported")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT, goal_text="audit")
        exec_prompt = ra.call_args_list[1].args[1]
        self.assertIn("1. inspect tmux", exec_prompt)

    def test_goal_file_is_read(self):
        goal_file = cfg.STATE_DIR / "g.txt"
        goal_file.write_text("migrate the database\n")
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("plan", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT, goal_path=str(goal_file))
        plan_prompt = ra.call_args_list[0].args[1]
        self.assertIn("migrate the database", plan_prompt)

    def test_default_agent_goal_file(self):
        # Default path is ~/.tmux-browse/agent-cycle/<agent>.txt under STATE_DIR.
        default_dir = cfg.STATE_DIR / "agent-cycle"
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "opus.txt").write_text("ship it\n")
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("plan", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT)
        plan_prompt = ra.call_args_list[0].args[1]
        self.assertIn("ship it", plan_prompt)

    def test_no_goal_falls_back_to_propose(self):
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("plan", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT)
        plan_prompt = ra.call_args_list[0].args[1]
        self.assertIn("Propose a plan", plan_prompt)

    def test_plan_failure_aborts_execute(self):
        def raise_plan(*a, **k):
            raise RuntimeError("provider down")

        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=raise_plan) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            with self.assertRaises(RuntimeError):
                cycle_mode.run(AGENT, goal_text="x")
        self.assertEqual(ra.call_count, 1)

    def test_repl_context_passes_through(self):
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("plan", "done")) as ra, \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            cycle_mode.run(AGENT, goal_text="x")
        # Both phases receive the repl_context kwarg (even if empty).
        for call in ra.call_args_list:
            self.assertIn("repl_context", call.kwargs)

    def test_returns_result_with_run_ids(self):
        with mock.patch("agent.modes.cycle.agent_runner.run_agent",
                        side_effect=_make_runs("the plan", "the exec")), \
             mock.patch("agent.modes.cycle.agent_runtime.record_turn"):
            res = cycle_mode.run(AGENT, goal_text="x")
        self.assertEqual(res.plan_run_id, "plan-run-1")
        self.assertEqual(res.exec_run_id, "exec-run-1")
        self.assertEqual(res.plan_message, "the plan")
        self.assertEqual(res.exec_message, "the exec")


if __name__ == "__main__":
    unittest.main()
