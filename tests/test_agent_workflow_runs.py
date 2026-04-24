"""Workflow execution history and per-workflow state."""

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

from agent import workflow_runs as wr  # noqa: E402


class _TmpDirMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        d = Path(self._tmpdir.name)
        self._p_runs = mock.patch.object(wr, "RUNS_FILE", d / "runs.jsonl")
        self._p_state = mock.patch.object(wr, "STATE_FILE", d / "state.json")
        self._p_runs.start()
        self._p_state.start()

    def tearDown(self):
        self._p_runs.stop()
        self._p_state.stop()
        self._tmpdir.cleanup()


class RecordResultTests(_TmpDirMixin, unittest.TestCase):

    def test_record_ok_resets_failures(self):
        wr.record_result("gpt", 0, status="error", run_id="r1", interval_seconds=60, error="boom")
        wr.record_result("gpt", 0, status="ok", run_id="r2", interval_seconds=60)
        ws = wr.get_workflow_state("gpt", 0)
        self.assertEqual(ws["consecutive_failures"], 0)
        self.assertEqual(ws["last_status"], "ok")
        self.assertEqual(ws["last_run_id"], "r2")

    def test_record_error_increments_failures(self):
        wr.record_result("gpt", 0, status="error", run_id="r1", interval_seconds=60, error="a")
        wr.record_result("gpt", 0, status="error", run_id="r2", interval_seconds=60, error="b")
        ws = wr.get_workflow_state("gpt", 0)
        self.assertEqual(ws["consecutive_failures"], 2)

    def test_appends_to_run_log(self):
        wr.record_result("gpt", 0, status="ok", run_id="r1", interval_seconds=60)
        wr.record_result("opus", 1, status="error", run_id="r2", interval_seconds=300, error="fail")
        runs = wr.read_runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["agent"], "gpt")
        self.assertEqual(runs[1]["agent"], "opus")


class IsDueTests(_TmpDirMixin, unittest.TestCase):

    def test_due_when_no_prior_run(self):
        self.assertTrue(wr.is_due("gpt", 0, 60))

    def test_not_due_after_recent_run(self):
        wr.record_result("gpt", 0, status="ok", run_id="r1", interval_seconds=60)
        self.assertFalse(wr.is_due("gpt", 0, 60))


class ReadRunsTests(_TmpDirMixin, unittest.TestCase):

    def test_empty_when_no_file(self):
        self.assertEqual(wr.read_runs(), [])

    def test_limit(self):
        for i in range(10):
            wr.record_result("gpt", 0, status="ok", run_id=f"r{i}", interval_seconds=60)
        runs = wr.read_runs(limit=3)
        self.assertEqual(len(runs), 3)


class GetAllStateTests(_TmpDirMixin, unittest.TestCase):

    def test_empty_initially(self):
        self.assertEqual(wr.get_all_state(), {})

    def test_returns_all_keys(self):
        wr.record_result("gpt", 0, status="ok", run_id="r1", interval_seconds=60)
        wr.record_result("opus", 1, status="ok", run_id="r2", interval_seconds=60)
        state = wr.get_all_state()
        self.assertIn("gpt:0", state)
        self.assertIn("opus:1", state)


if __name__ == "__main__":
    unittest.main()
