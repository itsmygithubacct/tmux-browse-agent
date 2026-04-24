"""Tool registry + read_file dispatch."""

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

from agent import tool_registry as reg  # noqa: E402


class RegistryTests(unittest.TestCase):

    def test_tb_command_registered(self):
        self.assertIn("tb_command", reg.TOOLS)
        self.assertIsNotNone(reg.TOOLS["tb_command"].run_host)
        self.assertIsNotNone(reg.TOOLS["tb_command"].run_sandbox)

    def test_read_file_registered(self):
        self.assertIn("read_file", reg.TOOLS)
        self.assertIsNotNone(reg.TOOLS["read_file"].run_host)
        self.assertIsNotNone(reg.TOOLS["read_file"].run_sandbox)

    def test_default_tools_when_agent_missing(self):
        self.assertEqual(reg.tool_names_for_agent({}), ["tb_command"])

    def test_unknown_tools_dropped_from_agent_list(self):
        names = reg.tool_names_for_agent({
            "tools": ["tb_command", "read_file", "bogus"]
        })
        self.assertEqual(names, ["tb_command", "read_file"])

    def test_empty_tools_falls_back_to_default(self):
        self.assertEqual(
            reg.tool_names_for_agent({"tools": []}), ["tb_command"])

    def test_prompt_block_mentions_tools(self):
        block = reg.tool_prompt_block(["tb_command", "read_file"])
        self.assertIn("tb_command", block)
        self.assertIn("read_file", block)


class ReadFileHostTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_small_file(self):
        f = self._root / "note.md"
        f.write_text("hello tmux")
        result = reg._read_file_host(
            self._root, {"path": str(f), "max_bytes": 1024}, None)
        self.assertTrue(result.ok)
        self.assertIn("hello tmux", result.stdout)

    def test_respects_max_bytes(self):
        f = self._root / "big.txt"
        f.write_text("x" * 10_000)
        result = reg._read_file_host(
            self._root, {"path": str(f), "max_bytes": 32}, None)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.stdout), 32)
        self.assertIn("truncated", result.stderr)

    def test_missing_path_is_error(self):
        result = reg._read_file_host(self._root, {}, None)
        self.assertFalse(result.ok)
        self.assertIn("path required", result.stderr)

    def test_missing_file_is_error_not_exception(self):
        result = reg._read_file_host(
            self._root, {"path": str(self._root / "ghost")}, None)
        self.assertFalse(result.ok)
        self.assertIn("not found", result.stderr)

    def test_blocks_ssh_path(self):
        ssh_path = Path.home() / ".ssh" / "id_rsa"
        result = reg._read_file_host(
            self._root, {"path": str(ssh_path)}, None)
        self.assertFalse(result.ok)
        self.assertIn("blocked", result.stderr)

    def test_caps_max_bytes_at_hard_limit(self):
        f = self._root / "big.txt"
        f.write_text("x" * (reg.READ_FILE_MAX_BYTES * 2))
        result = reg._read_file_host(
            self._root, {"path": str(f),
                         "max_bytes": reg.READ_FILE_MAX_BYTES * 10},
            None)
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.stdout), reg.READ_FILE_MAX_BYTES)


class ReadFileSandboxTests(unittest.TestCase):

    def _fake_sandbox(self, stdout="content", returncode=0, stderr=""):
        sb = mock.Mock()
        sb.container_name = "tb-test"
        return sb, stdout, returncode, stderr

    def test_rejects_host_absolute_path(self):
        sb, *_ = self._fake_sandbox()
        result = reg._read_file_sandbox(sb, {"path": "/etc/passwd"}, None)
        self.assertFalse(result.ok)
        self.assertIn("only accepts", result.stderr)

    def test_accepts_workspace_path(self):
        sb = mock.Mock()
        sb.container_name = "tb-test"
        with mock.patch("agent.tool_registry.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="bytes",
                                               stderr="")):
            result = reg._read_file_sandbox(
                sb, {"path": "/workspace/note.md"}, None)
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "bytes")

    def test_accepts_opt_tmux_browse_path(self):
        sb = mock.Mock()
        sb.container_name = "tb-test"
        with mock.patch("agent.tool_registry.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout="x",
                                               stderr="")):
            result = reg._read_file_sandbox(
                sb, {"path": "/opt/tmux-browse/tb.py"}, None)
        self.assertTrue(result.ok)

    def test_rejects_missing_path(self):
        sb = mock.Mock()
        result = reg._read_file_sandbox(sb, {}, None)
        self.assertFalse(result.ok)
        self.assertIn("path required", result.stderr)


if __name__ == "__main__":
    unittest.main()
