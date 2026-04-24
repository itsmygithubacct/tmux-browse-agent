"""Persistent per-agent execution logs."""

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

from agent import logs as agent_logs  # noqa: E402
from agent.runs import LOG_SCHEMA_VERSION  # noqa: E402


class _TmpMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._log_dir = Path(self._tmpdir.name)
        self._patch = mock.patch("agent.logs.config.AGENT_LOG_DIR", self._log_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


class AppendEntryTests(_TmpMixin, unittest.TestCase):

    def test_creates_log_file(self):
        agent_logs.append_entry("gpt", {"status": "ok"})
        path = self._log_dir / "gpt.jsonl"
        self.assertTrue(path.exists())

    def test_entry_has_ts(self):
        agent_logs.append_entry("gpt", {"status": "ok"})
        entries = agent_logs.read_entries("gpt")
        self.assertIn("ts", entries[0])
        self.assertIsInstance(entries[0]["ts"], int)

    def test_entry_has_schema_version(self):
        agent_logs.append_entry("gpt", {"status": "ok"})
        entries = agent_logs.read_entries("gpt")
        self.assertEqual(entries[0]["schema_version"], LOG_SCHEMA_VERSION)

    def test_preserves_existing_schema_version(self):
        agent_logs.append_entry("gpt", {"status": "ok", "schema_version": 99})
        entries = agent_logs.read_entries("gpt")
        self.assertEqual(entries[0]["schema_version"], 99)

    def test_multiple_entries(self):
        agent_logs.append_entry("gpt", {"status": "ok", "prompt": "a"})
        agent_logs.append_entry("gpt", {"status": "error", "prompt": "b"})
        entries = agent_logs.read_entries("gpt")
        self.assertEqual(len(entries), 2)


class ReadEntriesTests(_TmpMixin, unittest.TestCase):

    def test_empty_for_unknown_agent(self):
        self.assertEqual(agent_logs.read_entries("nonexistent"), [])

    def test_respects_limit(self):
        for i in range(10):
            agent_logs.append_entry("gpt", {"n": i})
        entries = agent_logs.read_entries("gpt", limit=3)
        self.assertEqual(len(entries), 3)
        # Should be the last 3
        self.assertEqual(entries[0]["n"], 7)


class GetLatestEntryTests(_TmpMixin, unittest.TestCase):

    def test_none_for_unknown_agent(self):
        self.assertIsNone(agent_logs.get_latest_entry("nonexistent"))

    def test_returns_last_entry(self):
        agent_logs.append_entry("gpt", {"status": "ok", "prompt": "a"})
        agent_logs.append_entry("gpt", {"status": "error", "prompt": "b"})
        latest = agent_logs.get_latest_entry("gpt")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["prompt"], "b")
        self.assertEqual(latest["status"], "error")

    def test_handles_empty_file(self):
        path = self._log_dir / "empty.jsonl"
        path.write_text("")
        self.assertIsNone(agent_logs.get_latest_entry("empty"))

    def test_skips_malformed_lines(self):
        path = self._log_dir / "messy.jsonl"
        path.write_text('not json\n{"status": "ok", "prompt": "good"}\n')
        latest = agent_logs.get_latest_entry("messy")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["prompt"], "good")


class RenderTextTests(_TmpMixin, unittest.TestCase):

    def test_renders_empty_message(self):
        text = agent_logs.render_text("nonexistent")
        self.assertIn("no agent log entries", text)

    def test_renders_entries(self):
        agent_logs.append_entry("gpt", {"status": "ok", "prompt": "check", "message": "done"})
        text = agent_logs.render_text("gpt")
        self.assertIn("agent=gpt", text)
        self.assertIn("prompt: check", text)
        self.assertIn("message: done", text)


if __name__ == "__main__":
    unittest.main()
