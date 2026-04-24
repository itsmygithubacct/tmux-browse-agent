"""Work mode: file-backed task queue + run-agent loop."""

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

from agent import budgets as agent_budgets  # noqa: E402
from lib import config as cfg  # noqa: E402
from agent.modes import work as work_mode  # noqa: E402


AGENT = {"name": "opus", "model": "claude-opus-4-7"}


def _ok_run(steps: int = 1):
    return {"message": "ok", "run_id": "r", "steps": steps,
            "transcript": [], "usage": {}}


class _IsolatedWork:
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
        work_mode._stop_flags.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _tasks_file(self, lines):
        p = cfg.STATE_DIR / "tasks.txt"
        p.write_text("\n".join(lines) + "\n")
        return p


class FileSourceTests(_IsolatedWork, unittest.TestCase):

    def test_plaintext_lines_become_tasks(self):
        src = work_mode.FileSource(
            path=self._tasks_file(["one", "two", "three"]),
            agent_name="opus")
        tasks = list(src.iter_pending())
        self.assertEqual([t.prompt for t in tasks], ["one", "two", "three"])

    def test_comments_and_blank_lines_skipped(self):
        src = work_mode.FileSource(
            path=self._tasks_file(["", "# a comment", "real", ""]),
            agent_name="opus")
        self.assertEqual([t.prompt for t in src.iter_pending()], ["real"])

    def test_jsonl_lines_respect_id(self):
        line = '{"id":"custom-1","prompt":"jsony task","meta":{"priority":"high"}}'
        src = work_mode.FileSource(
            path=self._tasks_file([line]), agent_name="opus")
        tasks = list(src.iter_pending())
        self.assertEqual(tasks[0].id, "custom-1")
        self.assertEqual(tasks[0].meta["priority"], "high")

    def test_resumable_via_done_file(self):
        src = work_mode.FileSource(
            path=self._tasks_file(["a", "b"]), agent_name="opus")
        tasks = list(src.iter_pending())
        # Mark first done and reconstruct source — should skip it.
        src.mark_done(tasks[0], "ok")
        src2 = work_mode.FileSource(path=src.path, agent_name="opus")
        remaining = [t.prompt for t in src2.iter_pending()]
        self.assertEqual(remaining, ["b"])


class RunLoopTests(_IsolatedWork, unittest.TestCase):

    def _patch_budget(self, action="ok", reason=""):
        return mock.patch(
            "agent.modes.work.agent_budgets.check_daily_budget",
            return_value={"action": action, "reason": reason})

    def test_empty_source_returns_empty(self):
        with mock.patch("agent.modes.work.agent_runner.run_agent") as ra, \
             self._patch_budget():
            path = self._tasks_file([])
            result = work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.total_tasks, 0)
        ra.assert_not_called()

    def test_two_tasks_run_sequentially(self):
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        return_value=_ok_run()) as ra, \
             self._patch_budget():
            path = self._tasks_file(["alpha", "beta"])
            result = work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(ra.call_count, 2)
        self.assertEqual(result.completed, 2)
        self.assertEqual(result.status, "done")
        prompts = [c.args[1] for c in ra.call_args_list]
        self.assertEqual(prompts, ["alpha", "beta"])

    def test_stop_on_error_halts(self):
        def side_effect(agent, prompt, **kw):
            if prompt == "bad":
                raise RuntimeError("boom")
            return _ok_run()
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        side_effect=side_effect) as ra, \
             self._patch_budget():
            path = self._tasks_file(["good", "bad", "never"])
            result = work_mode.run(AGENT, tasks_path=str(path),
                                   stop_on_error=True)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(ra.call_count, 2)

    def test_continue_on_error_default(self):
        def side_effect(agent, prompt, **kw):
            if prompt == "bad":
                raise RuntimeError("boom")
            return _ok_run()
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        side_effect=side_effect), \
             self._patch_budget():
            path = self._tasks_file(["good", "bad", "final"])
            result = work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(result.status, "done")
        self.assertEqual(result.completed, 2)
        self.assertEqual(result.failed, 1)

    def test_daily_budget_aborts(self):
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        return_value=_ok_run()) as ra, \
             self._patch_budget(action=agent_budgets.ACTION_STOP,
                                 reason="daily cap reached"):
            path = self._tasks_file(["x", "y"])
            result = work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(result.status, "budget")
        self.assertIn("daily cap", result.last_error)
        ra.assert_not_called()

    def test_step_cap_aborts(self):
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        return_value=_ok_run(steps=50)) as ra, \
             self._patch_budget():
            path = self._tasks_file(["one", "two", "three"])
            result = work_mode.run(AGENT, tasks_path=str(path),
                                   max_total_steps=75)
        self.assertEqual(result.status, "step_cap")
        # First task used 50 steps; second starts and uses 50 → over cap
        # → next iteration's guard stops before task 3.
        self.assertGreaterEqual(result.completed, 1)
        self.assertLess(ra.call_count, 3)

    def test_stop_signal(self):
        # Request stop before any tasks run; loop should exit immediately.
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        return_value=_ok_run()) as ra, \
             self._patch_budget():
            path = self._tasks_file(["one", "two"])
            work_mode.request_stop(AGENT["name"])
            # request_stop is coalesced with _stop_flag_for which *clears*
            # the flag on loop entry, so set it again after flag creation.
            # Real-world usage uses /api/agent-work/stop which sets the
            # flag while the loop is running. Simulate that with a
            # side-effect that stops after the first iteration.
            def side_effect(agent, prompt, **kw):
                work_mode._stop_flags[AGENT["name"]].set()
                return _ok_run()
            ra.side_effect = side_effect
            result = work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.completed, 1)

    def test_origin_is_work(self):
        with mock.patch("agent.modes.work.agent_runner.run_agent",
                        return_value=_ok_run()) as ra, \
             self._patch_budget():
            path = self._tasks_file(["t"])
            work_mode.run(AGENT, tasks_path=str(path))
        self.assertEqual(ra.call_args.kwargs["origin"], "work")


if __name__ == "__main__":
    unittest.main()
