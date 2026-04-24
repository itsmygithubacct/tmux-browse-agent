"""Event hook system tests."""

import json
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

from agent import hooks as ah  # noqa: E402


class _TmpMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        d = Path(self._tmpdir.name)
        self._p_hooks = mock.patch.object(ah, "HOOKS_FILE", d / "hooks.json")
        self._p_notif = mock.patch.object(ah, "NOTIFICATIONS_FILE", d / "notifications.jsonl")
        self._p_hooks.start()
        self._p_notif.start()

    def tearDown(self):
        self._p_hooks.stop()
        self._p_notif.stop()
        self._tmpdir.cleanup()


class LoadTests(_TmpMixin, unittest.TestCase):

    def test_returns_defaults_when_no_file(self):
        hooks = ah.load()
        self.assertIn("run_completed", hooks)
        self.assertEqual(hooks["run_completed"], ["log"])

    def test_returns_saved_config(self):
        ah.save({"run_failed": ["log", "notify"]})
        hooks = ah.load()
        self.assertEqual(hooks["run_failed"], ["log", "notify"])


class SaveTests(_TmpMixin, unittest.TestCase):

    def test_validates_events(self):
        saved = ah.save({"run_failed": ["log", "notify"], "bogus_event": ["log"]})
        self.assertNotIn("bogus_event", saved)
        self.assertIn("run_failed", saved)

    def test_strips_invalid_actions(self):
        saved = ah.save({"run_failed": ["log", "explode", "notify"]})
        self.assertEqual(saved["run_failed"], ["log", "notify"])

    def test_saves_per_agent_overrides(self):
        saved = ah.save({
            "run_failed": ["log"],
            "agents": {"gpt": {"run_failed": ["log", "retry"]}},
        })
        self.assertEqual(saved["agents"]["gpt"]["run_failed"], ["log", "retry"])


class GetActionsTests(_TmpMixin, unittest.TestCase):

    def test_returns_global_default(self):
        actions = ah.get_actions("run_completed", "gpt")
        self.assertEqual(actions, ["log"])

    def test_returns_per_agent_override(self):
        ah.save({
            "run_failed": ["log"],
            "agents": {"gpt": {"run_failed": ["log", "notify"]}},
        })
        actions = ah.get_actions("run_failed", "gpt")
        self.assertEqual(actions, ["log", "notify"])

    def test_non_overridden_agent_gets_global(self):
        ah.save({
            "run_failed": ["log", "retry"],
            "agents": {"gpt": {"run_completed": ["log"]}},
        })
        actions = ah.get_actions("run_failed", "gpt")
        self.assertEqual(actions, ["log", "retry"])


class ExecuteTests(_TmpMixin, unittest.TestCase):

    def test_notify_appends_to_file(self):
        ah.save({"run_failed": ["log", "notify"]})
        taken = ah.execute("run_failed", "gpt", run_id="r1", error="boom")
        self.assertIn("notify", taken)
        notes = ah.read_notifications()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["event"], "run_failed")
        self.assertEqual(notes[0]["agent"], "gpt")
        self.assertEqual(notes[0]["error"], "boom")

    def test_pause_workflow_disables_agent(self):
        ah.save({"budget_exceeded": ["log", "pause_workflow"]})
        wf = {"agents": {"gpt": {"enabled": True, "workflows": []}}}
        with mock.patch("agent.hooks.agent_workflows.load", return_value=wf), \
             mock.patch("agent.hooks.agent_workflows.save") as save_wf:
            taken = ah.execute("budget_exceeded", "gpt")
        self.assertIn("pause_workflow", taken)
        save_wf.assert_called_once()
        self.assertFalse(wf["agents"]["gpt"]["enabled"])

    def test_retry_signals_action(self):
        ah.save({"run_rate_limited": ["log", "retry"]})
        taken = ah.execute("run_rate_limited", "gpt")
        self.assertIn("retry", taken)

    def test_log_is_always_in_taken(self):
        taken = ah.execute("run_completed", "gpt")
        self.assertIn("log", taken)


class ReadNotificationsTests(_TmpMixin, unittest.TestCase):

    def test_empty_when_no_file(self):
        self.assertEqual(ah.read_notifications(), [])

    def test_respects_limit(self):
        ah.save({"run_failed": ["notify"]})
        for i in range(10):
            ah.execute("run_failed", "gpt", run_id=f"r{i}", error=f"err{i}")
        notes = ah.read_notifications(limit=3)
        self.assertEqual(len(notes), 3)

    def test_error_truncated(self):
        ah.save({"run_failed": ["notify"]})
        ah.execute("run_failed", "gpt", error="x" * 500)
        notes = ah.read_notifications()
        self.assertLessEqual(len(notes[0]["error"]), 200)


if __name__ == "__main__":
    unittest.main()
