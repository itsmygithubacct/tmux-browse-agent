"""Per-run cost tracking."""

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

from agent import costs as ac  # noqa: E402


class _TmpMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            ac, "COSTS_FILE", Path(self._tmpdir.name) / "costs.jsonl",
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


class RecordTests(_TmpMixin, unittest.TestCase):

    def test_record_creates_file(self):
        ac.record(run_id="r1", agent="gpt", model="m",
                  usage={"prompt_tokens": 100, "completion_tokens": 50})
        self.assertTrue(ac.COSTS_FILE.exists())

    def test_record_skips_empty_usage(self):
        ac.record(run_id="r1", agent="gpt", model="m", usage={})
        self.assertFalse(ac.COSTS_FILE.exists())

    def test_computes_total(self):
        ac.record(run_id="r1", agent="gpt", model="m",
                  usage={"prompt_tokens": 100, "completion_tokens": 50})
        rows = ac.query()
        self.assertEqual(rows[0]["total_tokens"], 150)

    def test_uses_provider_total(self):
        ac.record(run_id="r1", agent="gpt", model="m",
                  usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 200})
        rows = ac.query()
        self.assertEqual(rows[0]["total_tokens"], 200)


class QueryTests(_TmpMixin, unittest.TestCase):

    def test_empty_when_no_file(self):
        self.assertEqual(ac.query(), [])

    def test_filter_by_agent(self):
        ac.record(run_id="r1", agent="gpt", model="m", usage={"total_tokens": 10})
        ac.record(run_id="r2", agent="opus", model="m", usage={"total_tokens": 20})
        rows = ac.query(agent="opus")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "opus")


class TotalsTests(_TmpMixin, unittest.TestCase):

    def test_per_agent_totals(self):
        ac.record(run_id="r1", agent="gpt", model="m",
                  usage={"prompt_tokens": 100, "completion_tokens": 50})
        ac.record(run_id="r2", agent="gpt", model="m",
                  usage={"prompt_tokens": 200, "completion_tokens": 100})
        ac.record(run_id="r3", agent="opus", model="m",
                  usage={"prompt_tokens": 50, "completion_tokens": 25})
        totals = ac.per_agent_totals()
        self.assertEqual(totals["gpt"]["prompt_tokens"], 300)
        self.assertEqual(totals["gpt"]["runs"], 2)
        self.assertEqual(totals["opus"]["runs"], 1)

    def test_daily_totals(self):
        ac.record(run_id="r1", agent="gpt", model="m",
                  usage={"prompt_tokens": 100, "completion_tokens": 50})
        today = time.strftime("%Y-%m-%d", time.gmtime())
        totals = ac.daily_totals()
        self.assertIn(today, totals)
        self.assertEqual(totals[today]["runs"], 1)


class SandboxFieldTests(unittest.TestCase):
    """Verify agent_store handles the sandbox field."""

    def test_normalize_adds_sandbox_default(self):
        from agent import store as agent_store
        out = agent_store._normalize_agent_meta("test", {})
        self.assertEqual(out["sandbox"], "host")

    def test_normalize_preserves_valid_sandbox(self):
        from agent import store as agent_store
        out = agent_store._normalize_agent_meta("test", {"sandbox": "worktree"})
        self.assertEqual(out["sandbox"], "worktree")
        out = agent_store._normalize_agent_meta("test", {"sandbox": "docker"})
        self.assertEqual(out["sandbox"], "docker")

    def test_normalize_rejects_invalid_sandbox(self):
        from agent import store as agent_store
        out = agent_store._normalize_agent_meta("test", {"sandbox": "podman"})
        self.assertEqual(out["sandbox"], "host")


if __name__ == "__main__":
    unittest.main()
