"""K4: status integration — runtime prefix awareness + per-session detect."""

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

from agent import cli_detect, runtime  # noqa: E402


class AgentNameFromSessionTests(unittest.TestCase):

    def test_repl_prefix_unchanged(self):
        self.assertEqual(runtime.agent_name_from_session("agent-repl-sonnet"), "sonnet")

    def test_cli_prefix_resolves_to_registry_name(self):
        # binary == name for claude
        self.assertEqual(
            runtime.agent_name_from_session("agent-cli-claude-deadbeef"),
            "claude",
        )

    def test_cli_prefix_unknown_binary_returns_binary(self):
        # If the binary isn't in the registry, the binary itself is a
        # reasonable fallback so the dashboard can still attribute the pane.
        self.assertEqual(
            runtime.agent_name_from_session("agent-cli-myshim-aabbccdd"),
            "myshim",
        )

    def test_unrelated_session_returns_none(self):
        self.assertIsNone(runtime.agent_name_from_session("random-session"))

    def test_invalid_cli_uid_returns_none(self):
        # uid must be exactly 8 hex chars; otherwise unknown.
        self.assertIsNone(runtime.agent_name_from_session("agent-cli-claude-foo"))


class DetectForSessionTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        self._patch_root = mock.patch.object(cli_detect, "HOOKS_ROOT", self._dir)
        self._patch_root.start()

    def tearDown(self):
        self._patch_root.stop()
        self._tmp.cleanup()

    def _write_hook_status(self, instance_id: str, status: str) -> None:
        d = self._dir / instance_id
        d.mkdir(parents=True)
        (d / "status").write_text(status)

    def test_hook_file_wins(self):
        self._write_hook_status("deadbeef", "running")
        # Pane content says idle, but hook file says running — hook wins.
        result = cli_detect.detect_for_session(
            "agent-cli-claude-deadbeef",
            capture=lambda _s: "ready\n>",
        )
        self.assertEqual(result, "running")

    def test_falls_back_to_pane_when_no_hook_file(self):
        result = cli_detect.detect_for_session(
            "agent-cli-codex-aabbccdd",
            capture=lambda _s: "thinking about your request",
        )
        self.assertEqual(result, "running")

    def test_unknown_session_returns_idle(self):
        self.assertEqual(
            cli_detect.detect_for_session("not-a-cli-session"),
            "idle",
        )

    def test_unparseable_hook_status_falls_through_to_pane(self):
        self._write_hook_status("deadbeef", "garbage")
        result = cli_detect.detect_for_session(
            "agent-cli-codex-deadbeef",
            capture=lambda _s: "thinking",
        )
        self.assertEqual(result, "running")

    def test_capture_failure_returns_idle(self):
        def boom(_s):
            raise RuntimeError("tmux unreachable")
        result = cli_detect.detect_for_session(
            "agent-cli-codex-aabbccdd",
            capture=boom,
        )
        self.assertEqual(result, "idle")


if __name__ == "__main__":
    unittest.main()
