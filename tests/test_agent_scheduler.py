"""Background workflow scheduler."""

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import scheduler as sched  # noqa: E402
from agent.providers import ProviderResult  # noqa: E402


class SchedulerTickTests(unittest.TestCase):
    """Unit-test the scheduler's _tick logic without threads."""

    def _make_scheduler(self):
        s = sched.Scheduler(repo_root=Path("/tmp"))
        return s

    def test_tick_skips_disabled_agents(self):
        s = self._make_scheduler()
        wf = {"agents": {"gpt": {
            "enabled": False,
            "workflows": [{"name": "check", "prompt": "check all", "interval_seconds": 60}],
        }}}
        with mock.patch("agent.scheduler.agent_scheduler_lock.is_owned", return_value=True), \
             mock.patch("agent.scheduler.agent_workflows.load", return_value=wf), \
             mock.patch("agent.scheduler.agent_workflow_runs.is_due") as is_due:
            s._tick()
        is_due.assert_not_called()

    def test_tick_skips_empty_prompts(self):
        s = self._make_scheduler()
        wf = {"agents": {"gpt": {
            "enabled": True,
            "workflows": [{"name": "empty", "prompt": "", "interval_seconds": 60}],
        }}}
        with mock.patch("agent.scheduler.agent_scheduler_lock.is_owned", return_value=True), \
             mock.patch("agent.scheduler.agent_workflows.load", return_value=wf), \
             mock.patch("agent.scheduler.agent_workflow_runs.is_due") as is_due:
            s._tick()
        is_due.assert_not_called()

    def test_tick_runs_due_workflow(self):
        s = self._make_scheduler()
        wf = {"agents": {"gpt": {
            "enabled": True,
            "workflows": [{"name": "check", "prompt": "check all", "interval_seconds": 60}],
        }}}
        with mock.patch("agent.scheduler.agent_scheduler_lock.is_owned", return_value=True), \
             mock.patch("agent.scheduler.agent_workflows.load", return_value=wf), \
             mock.patch("agent.scheduler.agent_workflow_runs.is_due", return_value=True), \
             mock.patch.object(s, "_run_workflow") as run_wf:
            s._tick()
        run_wf.assert_called_once_with("gpt", 0, "check all", 60)

    def test_tick_skips_not_due_workflow(self):
        s = self._make_scheduler()
        wf = {"agents": {"gpt": {
            "enabled": True,
            "workflows": [{"name": "check", "prompt": "check all", "interval_seconds": 60}],
        }}}
        with mock.patch("agent.scheduler.agent_scheduler_lock.is_owned", return_value=True), \
             mock.patch("agent.scheduler.agent_workflows.load", return_value=wf), \
             mock.patch("agent.scheduler.agent_workflow_runs.is_due", return_value=False), \
             mock.patch.object(s, "_run_workflow") as run_wf:
            s._tick()
        run_wf.assert_not_called()

    def test_tick_skips_when_not_lock_owner(self):
        s = self._make_scheduler()
        with mock.patch("agent.scheduler.agent_scheduler_lock.is_owned", return_value=False), \
             mock.patch("agent.scheduler.agent_workflows.load") as load:
            s._tick()
        load.assert_not_called()


class RunWorkflowTests(unittest.TestCase):

    def test_records_ok_result(self):
        s = sched.Scheduler(repo_root=Path("/tmp"))
        agent = {"name": "gpt", "model": "m", "wire_api": "openai-chat", "api_key": "k", "base_url": "http://x"}
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent", return_value={"message": "ok"}), \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result") as rec:
            s._run_workflow("gpt", 0, "check all", 60)
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["status"], "ok")

    def test_records_error_result(self):
        s = sched.Scheduler(repo_root=Path("/tmp"))
        agent = {"name": "gpt", "model": "m", "wire_api": "openai-chat", "api_key": "k", "base_url": "http://x"}
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent", side_effect=Exception("fail")), \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result") as rec:
            s._run_workflow("gpt", 0, "check all", 60)
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["status"], "error")
        self.assertIn("fail", rec.call_args.kwargs["error"])

    def test_records_error_when_agent_not_configured(self):
        s = sched.Scheduler(repo_root=Path("/tmp"))
        with mock.patch("agent.scheduler.agent_store.get_agent", side_effect=Exception("not found")), \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result") as rec:
            s._run_workflow("missing", 0, "check all", 60)
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["status"], "error")
        self.assertIn("not configured", rec.call_args.kwargs["error"])


class DockerSandboxSchedulerTests(unittest.TestCase):
    """Scheduler builds a sandbox spec but never instantiates Sandbox."""

    def _agent(self, sandbox_mode):
        return {
            "name": "opus", "model": "m", "wire_api": "openai-chat",
            "api_key": "k", "base_url": "http://x",
            "sandbox": sandbox_mode,
        }

    def test_docker_agent_passes_docker_spec(self):
        s = sched.Scheduler(repo_root=Path("/repo"))
        agent = self._agent("docker")
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent",
                        return_value={"message": "ok"}) as run, \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result"):
            s._run_workflow("opus", 0, "do work", 60)
        spec = run.call_args.kwargs["sandbox_spec"]
        self.assertEqual(spec, {"mode": "docker", "workspace": "/repo"})

    def test_host_agent_passes_no_spec(self):
        s = sched.Scheduler(repo_root=Path("/repo"))
        agent = self._agent("host")
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent",
                        return_value={"message": "ok"}) as run, \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result"):
            s._run_workflow("opus", 0, "do work", 60)
        self.assertIsNone(run.call_args.kwargs["sandbox_spec"])

    def test_scheduler_does_not_instantiate_sandbox(self):
        s = sched.Scheduler(repo_root=Path("/repo"))
        agent = self._agent("docker")
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent",
                        return_value={"message": "ok"}), \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result"), \
             mock.patch("lib.docker_sandbox.Sandbox") as sandbox_cls:
            s._run_workflow("opus", 0, "do work", 60)
        sandbox_cls.assert_not_called()

    def test_sandbox_creation_failure_records_error_no_fallback(self):
        s = sched.Scheduler(repo_root=Path("/repo"))
        agent = self._agent("docker")
        with mock.patch("agent.scheduler.agent_store.get_agent", return_value=agent), \
             mock.patch("agent.scheduler.agent_runner.run_agent",
                        side_effect=Exception("sandbox creation failed: docker missing")), \
             mock.patch("agent.scheduler.agent_workflow_runs.record_result") as rec, \
             mock.patch("agent.scheduler.agent_hooks.execute"):
            s._run_workflow("opus", 0, "do work", 60)
        self.assertEqual(rec.call_args.kwargs["status"], "error")
        self.assertIn("sandbox creation failed", rec.call_args.kwargs["error"])


if __name__ == "__main__":
    unittest.main()
