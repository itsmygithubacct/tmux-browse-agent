"""File-backed run index with filtered queries."""

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

from agent import run_index as idx  # noqa: E402


class _TmpDirMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            idx, "INDEX_FILE", Path(self._tmpdir.name) / "index.jsonl",
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


def _sample(agent="gpt", status="run_completed", prompt="check panes",
            message="done", error=None, steps=3, offset=0, tools=None):
    now = int(time.time()) - offset
    return dict(
        run_id=f"r-{agent}-{now}",
        agent=agent,
        status=status,
        started_ts=now - 5,
        finished_ts=now,
        steps=steps,
        prompt=prompt,
        message=message,
        error=error,
        origin="cli",
        model="m",
        transcript=[
            {"step": 1, "action": {"type": "tool", "tool": "tb_command", "args": t}}
            for t in (tools or [["ls"]])
        ],
    )


class AppendTests(_TmpDirMixin, unittest.TestCase):

    def test_append_creates_file(self):
        idx.append(**_sample())
        self.assertTrue(idx.INDEX_FILE.exists())

    def test_append_fields(self):
        idx.append(**_sample(prompt="hello world" * 20))
        rows = idx.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "gpt")
        self.assertEqual(rows[0]["status"], "run_completed")
        self.assertIn("hello world", rows[0]["prompt_preview"])
        self.assertLessEqual(len(rows[0]["prompt_preview"]), 123)

    def test_tools_extracted(self):
        idx.append(**_sample(tools=[["ls"], ["show"], ["ls"]]))
        rows = idx.query()
        self.assertEqual(rows[0]["tools_used"], ["ls", "show"])


class QueryTests(_TmpDirMixin, unittest.TestCase):

    def test_query_empty(self):
        self.assertEqual(idx.query(), [])

    def test_filter_by_agent(self):
        idx.append(**_sample(agent="gpt"))
        idx.append(**_sample(agent="opus"))
        rows = idx.query(agent="opus")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "opus")

    def test_filter_by_status(self):
        idx.append(**_sample(status="run_completed"))
        idx.append(**_sample(status="run_failed", message="", error="boom"))
        rows = idx.query(status="run_failed")
        self.assertEqual(len(rows), 1)

    def test_filter_by_text(self):
        idx.append(**_sample(prompt="check panes"))
        idx.append(**_sample(prompt="list sessions"))
        rows = idx.query(text="panes")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "gpt")

    def test_filter_by_tool(self):
        idx.append(**_sample(tools=[["ls"]]))
        idx.append(**_sample(tools=[["exec"]]))
        rows = idx.query(tool="exec")
        self.assertEqual(len(rows), 1)

    def test_filter_by_time_range(self):
        now = int(time.time())
        idx.append(**_sample(offset=0))
        idx.append(**_sample(offset=3600))
        rows = idx.query(since=now - 60)
        self.assertEqual(len(rows), 1)

    def test_limit(self):
        for i in range(10):
            idx.append(**_sample())
        rows = idx.query(limit=3)
        self.assertEqual(len(rows), 3)

    def test_newest_first(self):
        idx.append(**_sample(agent="old", offset=100))
        idx.append(**_sample(agent="new", offset=0))
        rows = idx.query()
        self.assertEqual(rows[0]["agent"], "new")

    def test_combined_filters(self):
        idx.append(**_sample(agent="gpt", status="run_completed"))
        idx.append(**_sample(agent="gpt", status="run_failed", message="", error="x"))
        idx.append(**_sample(agent="opus", status="run_failed", message="", error="y"))
        rows = idx.query(agent="gpt", status="run_failed")
        self.assertEqual(len(rows), 1)


class GetRunTests(_TmpDirMixin, unittest.TestCase):

    def test_get_existing(self):
        s = _sample()
        idx.append(**s)
        row = idx.get_run(s["run_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["run_id"], s["run_id"])

    def test_get_nonexistent(self):
        self.assertIsNone(idx.get_run("no-such-id"))


class ExtractToolsTests(unittest.TestCase):

    def test_extracts_unique_verbs(self):
        transcript = [
            {"step": 1, "action": {"type": "tool", "tool": "tb_command", "args": ["ls", "--json"]}},
            {"step": 2, "action": {"type": "tool", "tool": "tb_command", "args": ["show", "work"]}},
            {"step": 3, "action": {"type": "tool", "tool": "tb_command", "args": ["ls"]}},
            {"step": 4, "action": {"type": "final", "message": "done"}},
        ]
        self.assertEqual(idx._extract_tools(transcript), ["ls", "show"])

    def test_empty_transcript(self):
        self.assertEqual(idx._extract_tools([]), [])


if __name__ == "__main__":
    unittest.main()
