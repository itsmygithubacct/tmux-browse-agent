"""Conductor rule engine: parsing, matching, dispatching, decision log."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import conductor as agent_conductor  # noqa: E402
from lib import config as cfg  # noqa: E402


class _IsolatedConductor:
    """Point conductor paths at a tempdir and clear in-memory state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", root),
            mock.patch.object(cfg, "AGENT_CONDUCTOR_FILE", root / "conductor.json"),
            mock.patch.object(cfg, "AGENT_CONDUCTOR_LOG", root / "conductor.jsonl"),
            mock.patch.object(cfg, "AGENT_NOTIFICATIONS_FILE",
                              root / "notif.jsonl"),
            mock.patch.object(cfg, "AGENT_HOOKS_FILE", root / "hooks.json"),
        ]
        for p in self._patches:
            p.start()
        agent_conductor.reset_state_for_tests()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class ParseValidateTests(_IsolatedConductor, unittest.TestCase):

    def test_empty_on_missing_file(self):
        self.assertEqual(agent_conductor.load_rules(), [])

    def test_drops_malformed_rules_silently(self):
        cfg.AGENT_CONDUCTOR_FILE.write_text(json.dumps({
            "rules": [
                {"id": "good", "do": [{"action": "log"}]},
                {"do": [{"action": "log"}]},                # missing id
                {"id": "bad", "do": []},                    # empty do
                {"id": "bad2", "do": [{"action": "unknown"}]},
            ]
        }))
        rules = agent_conductor.load_rules()
        self.assertEqual([r["id"] for r in rules], ["good"])

    def test_validate_raises_on_missing_id(self):
        with self.assertRaises(ValueError):
            agent_conductor.validate_raw({"rules": [{"do": [{"action": "log"}]}]})

    def test_validate_raises_on_duplicate_ids(self):
        with self.assertRaises(ValueError):
            agent_conductor.validate_raw({"rules": [
                {"id": "x", "do": [{"action": "log"}]},
                {"id": "x", "do": [{"action": "log"}]},
            ]})

    def test_validate_raises_on_run_agent_without_target(self):
        with self.assertRaises(ValueError):
            agent_conductor.validate_raw({"rules": [
                {"id": "r", "do": [{"action": "run_agent"}]},
            ]})

    def test_save_and_reload_round_trips(self):
        agent_conductor.save_rules({"rules": [
            {"id": "r", "do": [{"action": "log"}]},
        ]})
        self.assertEqual(
            [r["id"] for r in agent_conductor.load_rules()], ["r"])


class MatchingTests(_IsolatedConductor, unittest.TestCase):

    def test_event_filter(self):
        agent_conductor.save_rules({"rules": [
            {"id": "only-failed",
             "when": {"event": "run_failed"},
             "do": [{"action": "log"}]},
        ]})
        fired = agent_conductor.record_event("run_completed", "opus")
        self.assertEqual(fired, [])
        fired = agent_conductor.record_event("run_failed", "opus")
        self.assertTrue(fired)

    def test_wildcard_agent_matches_all(self):
        agent_conductor.save_rules({"rules": [
            {"id": "any-fail",
             "when": {"event": "run_failed", "agent": "*"},
             "do": [{"action": "log"}]},
        ]})
        self.assertTrue(agent_conductor.record_event("run_failed", "opus"))
        self.assertTrue(agent_conductor.record_event("run_failed", "gpt"))

    def test_specific_agent_excludes_others(self):
        agent_conductor.save_rules({"rules": [
            {"id": "only-opus",
             "when": {"event": "run_failed", "agent": "opus"},
             "do": [{"action": "log"}]},
        ]})
        self.assertTrue(agent_conductor.record_event("run_failed", "opus"))
        # Different agent, but runaway guard might kick in if same rule id.
        # Reset to isolate.
        agent_conductor.reset_state_for_tests()
        self.assertEqual(
            agent_conductor.record_event("run_failed", "gpt"), [])

    def test_count_at_least_requires_threshold(self):
        agent_conductor.save_rules({"rules": [
            {"id": "three-strikes",
             "when": {"event": "run_failed", "agent": "opus",
                      "within_last": "1h", "count_at_least": 3},
             "do": [{"action": "log"}]},
        ]})
        # First two fail silently (below threshold).
        self.assertEqual(
            agent_conductor.record_event("run_failed", "opus"), [])
        self.assertEqual(
            agent_conductor.record_event("run_failed", "opus"), [])
        # Third fires.
        agent_conductor.reset_state_for_tests()  # clear inflight guard
        # Re-record the prior two so the window counter sees them.
        now = int(time.time())
        agent_conductor._events[("__all__", "opus")] = [now - 10, now - 5]
        fired = agent_conductor.record_event("run_failed", "opus")
        self.assertTrue(fired)

    def test_window_eviction(self):
        # Events older than the window don't count toward count_at_least.
        agent_conductor.save_rules({"rules": [
            {"id": "hour-window",
             "when": {"event": "run_failed", "agent": "opus",
                      "within_last": "1h", "count_at_least": 3},
             "do": [{"action": "log"}]},
        ]})
        now = int(time.time())
        # Three events two hours ago shouldn't count.
        agent_conductor._events[("__all__", "opus")] = [now - 7200, now - 7100, now - 7000]
        self.assertEqual(
            agent_conductor.record_event("run_failed", "opus"), [])


class RunawayGuardTests(_IsolatedConductor, unittest.TestCase):

    def test_same_rule_doesnt_refire_immediately(self):
        agent_conductor.save_rules({"rules": [
            {"id": "r", "when": {"event": "run_failed"},
             "do": [{"action": "log"}]},
        ]})
        first = agent_conductor.record_event("run_failed", "opus")
        second = agent_conductor.record_event("run_failed", "opus")
        self.assertTrue(first)
        self.assertEqual(second, [])  # guard swallows it


class DispatchTests(_IsolatedConductor, unittest.TestCase):

    def test_run_agent_dispatches_in_background_thread(self):
        agent_conductor.save_rules({"rules": [
            {"id": "failover",
             "when": {"event": "run_rate_limited", "agent": "sonnet"},
             "do": [{"action": "run_agent", "agent": "opus",
                     "prompt_from": "$.original_prompt"}]},
        ]})
        with mock.patch("agent.runner.run_agent") as run_agent, \
             mock.patch("agent.store.get_agent", return_value={
                 "name": "opus", "model": "m"}):
            agent_conductor.record_event(
                "run_rate_limited", "sonnet",
                context={"prompt": "do the thing"})
            # Spawned thread — give it a moment
            import time as _t; _t.sleep(0.05)
        # Called at least once with the substituted prompt.
        self.assertTrue(run_agent.called)
        call = run_agent.call_args
        self.assertEqual(call.kwargs.get("origin"), "conductor")

    def test_pause_workflow_action_calls_hook_helper(self):
        agent_conductor.save_rules({"rules": [
            {"id": "pause-on-fail",
             "when": {"event": "run_failed"},
             "do": [{"action": "pause_workflow", "agent": "opus"}]},
        ]})
        with mock.patch("agent.hooks._pause_agent_workflow") as pause:
            agent_conductor.record_event("run_failed", "opus")
        pause.assert_called_once_with("opus")


class DecisionLogTests(_IsolatedConductor, unittest.TestCase):

    def test_fired_rule_writes_one_line(self):
        agent_conductor.save_rules({"rules": [
            {"id": "r", "when": {"event": "run_failed"},
             "do": [{"action": "log"}]},
        ]})
        agent_conductor.record_event("run_failed", "opus")
        decisions = agent_conductor.read_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["rule_id"], "r")
        self.assertEqual(decisions[0]["agent"], "opus")

    def test_read_decisions_agent_filter(self):
        agent_conductor.save_rules({"rules": [
            {"id": "any", "when": {"event": "run_failed"},
             "do": [{"action": "log"}]},
        ]})
        agent_conductor.record_event("run_failed", "opus")
        agent_conductor.reset_state_for_tests()
        agent_conductor.record_event("run_failed", "gpt")
        opus_only = agent_conductor.read_decisions(agent="opus")
        self.assertEqual(len(opus_only), 1)
        self.assertEqual(opus_only[0]["agent"], "opus")


if __name__ == "__main__":
    unittest.main()
