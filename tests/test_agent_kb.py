"""Per-agent knowledge base: file management and prompt-block rendering."""

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

from agent import kb as agent_kb  # noqa: E402
from lib import config as cfg  # noqa: E402


class _IsolatedKB:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._src_tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", root),
            mock.patch.object(cfg, "AGENT_KB_DIR", root / "kb"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()
        self._src_tmp.cleanup()

    def _write_src(self, name: str, content: bytes | str) -> str:
        p = Path(self._src_tmp.name) / name
        if isinstance(content, str):
            p.write_text(content)
        else:
            p.write_bytes(content)
        return str(p)


class KBTests(_IsolatedKB, unittest.TestCase):

    def test_empty_for_new_agent(self):
        self.assertEqual(agent_kb.list_files("opus"), [])

    def test_add_and_list(self):
        src = self._write_src("notes.md", "# Plan\n")
        info = agent_kb.add_file("opus", src)
        self.assertEqual(info["name"], "notes.md")
        self.assertGreater(info["size"], 0)
        rows = agent_kb.list_files("opus")
        self.assertEqual([r["name"] for r in rows], ["notes.md"])

    def test_remove(self):
        src = self._write_src("notes.md", "x")
        agent_kb.add_file("opus", src)
        self.assertTrue(agent_kb.remove_file("opus", "notes.md"))
        self.assertEqual(agent_kb.list_files("opus"), [])

    def test_remove_missing_is_false(self):
        self.assertFalse(agent_kb.remove_file("opus", "ghost.md"))

    def test_cap_rejects_oversized_file(self):
        src = self._write_src("big.md", "x" * (agent_kb.TOTAL_BYTES_CAP + 1))
        with self.assertRaises(ValueError):
            agent_kb.add_file("opus", src)

    def test_cap_rejects_when_sum_would_exceed(self):
        half = agent_kb.TOTAL_BYTES_CAP // 2 + 1
        a = self._write_src("a.md", "x" * half)
        b = self._write_src("b.md", "x" * half)
        agent_kb.add_file("opus", a)
        with self.assertRaises(ValueError):
            agent_kb.add_file("opus", b)

    def test_render_block_empty_when_no_files(self):
        self.assertEqual(agent_kb.render_block("opus"), "")

    def test_render_block_includes_content(self):
        agent_kb.add_file("opus", self._write_src("notes.md", "hello"))
        block = agent_kb.render_block("opus")
        self.assertIn("## Knowledge base", block)
        self.assertIn("notes.md", block)
        self.assertIn("hello", block)


if __name__ == "__main__":
    unittest.main()
