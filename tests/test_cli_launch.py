"""CLI agent launch flow: installation check, command assembly, tmux spawn."""

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

from agent import cli_launch, cli_registry  # noqa: E402
from lib import sessions as core_sessions  # noqa: E402


class IsInstalledTests(unittest.TestCase):

    def test_which_path_uses_shutil(self):
        cli = cli_registry.find("claude")
        with mock.patch("agent.cli_launch.shutil.which", return_value="/usr/bin/claude"):
            self.assertTrue(cli_launch.is_installed(cli))
        with mock.patch("agent.cli_launch.shutil.which", return_value=None):
            self.assertFalse(cli_launch.is_installed(cli))


class SessionNameTests(unittest.TestCase):

    def test_round_trip(self):
        cli = cli_registry.find("codex")
        name = cli_launch.session_name_for(cli, "deadbeef")
        self.assertEqual(name, "agent-cli-codex-deadbeef")
        parsed = cli_launch.parse_session_name(name)
        self.assertEqual(parsed, ("codex", "deadbeef"))

    def test_parse_rejects_non_prefix(self):
        self.assertIsNone(cli_launch.parse_session_name("agent-repl-foo"))
        self.assertIsNone(cli_launch.parse_session_name("random"))

    def test_parse_rejects_short_uid(self):
        # uid must be exactly 8 hex chars (matches secrets.token_hex(4))
        self.assertIsNone(cli_launch.parse_session_name("agent-cli-claude-abc"))


class BuildCommandTests(unittest.TestCase):

    def test_includes_instance_id_env(self):
        cli = cli_registry.find("opencode")
        cmd = cli_launch._build_command(cli, None, "abc12345", yolo_enabled=False)
        self.assertIn("TBA_INSTANCE_ID=abc12345", cmd)
        self.assertIn("opencode", cmd)
        self.assertIn("stty susp undef", cmd)

    def test_yolo_flag_appended(self):
        cli = cli_registry.find("codex")
        cmd = cli_launch._build_command(cli, None, "11111111", yolo_enabled=True)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)

    def test_yolo_env_prepended(self):
        cli = cli_registry.find("opencode")
        cmd = cli_launch._build_command(cli, None, "11111111", yolo_enabled=True)
        # OpenCode's yolo is an env var, not a flag.
        self.assertIn("OPENCODE_PERMISSION", cmd)
        self.assertNotIn("--dangerously", cmd)

    def test_yolo_disabled_omits_flag(self):
        cli = cli_registry.find("codex")
        cmd = cli_launch._build_command(cli, None, "11111111", yolo_enabled=False)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)

    def test_instruction_template_rendered(self):
        cli = cli_registry.find("claude")  # has --append-system-prompt {}
        cmd = cli_launch._build_command(
            cli, "be terse", "11111111", yolo_enabled=False,
        )
        self.assertIn("--append-system-prompt", cmd)
        self.assertIn("'be terse'", cmd)  # shell-quoted

    def test_instruction_skipped_when_no_template(self):
        # opencode has no instruction_flag; an instruction passed in is silently dropped.
        cli = cli_registry.find("opencode")
        cmd = cli_launch._build_command(
            cli, "ignored", "11111111", yolo_enabled=False,
        )
        self.assertNotIn("ignored", cmd)


class LaunchTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        # Make HOOKS_ROOT a tempdir so gc_stale_hook_dirs is harmless.
        self._patch_hooks = mock.patch.object(cli_launch, "HOOKS_ROOT", self._dir / "tba-hooks")
        self._patch_hooks.start()
        # Pretend every binary is installed; tests target other branches.
        self._patch_installed = mock.patch.object(cli_launch, "is_installed", return_value=True)
        self._patch_installed.start()
        # Mock the actual tmux call so we don't spawn anything real.
        self._patch_new_session = mock.patch.object(
            core_sessions, "new_session", return_value=(True, "")
        )
        self._mock_new_session = self._patch_new_session.start()

    def tearDown(self):
        self._patch_hooks.stop()
        self._patch_installed.stop()
        self._patch_new_session.stop()
        self._tmp.cleanup()

    def test_unknown_agent(self):
        result = cli_launch.launch("nope")
        self.assertFalse(result["ok"])
        self.assertIn("unknown", result["error"])

    def test_missing_binary_returns_install_required(self):
        with mock.patch.object(cli_launch, "is_installed", return_value=False):
            result = cli_launch.launch("claude")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("install_required"))
        self.assertIn("npm install", result["install_hint"])

    def test_happy_path_spawns_session(self):
        result = cli_launch.launch("claude", cwd=str(self._dir))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["binary"], "claude")
        self.assertEqual(result["name"], "claude")
        self.assertTrue(result["session"].startswith("agent-cli-claude-"))
        self.assertEqual(len(result["instance_id"]), 8)
        # Verify the tmux call: session name + cwd + cmd were passed through.
        self._mock_new_session.assert_called_once()
        args, kwargs = self._mock_new_session.call_args
        self.assertEqual(args[0], result["session"])
        self.assertEqual(kwargs["cwd"], str(self._dir))
        self.assertIn("TBA_INSTANCE_ID=", kwargs["cmd"])

    def test_invalid_cwd_short_circuits(self):
        result = cli_launch.launch("claude", cwd="/no/such/dir/exists/here")
        self.assertFalse(result["ok"])
        self.assertIn("cwd", result["error"])
        self._mock_new_session.assert_not_called()

    def test_tmux_failure_propagates(self):
        self._mock_new_session.return_value = (False, "tmux not running")
        result = cli_launch.launch("claude", cwd=str(self._dir))
        self.assertFalse(result["ok"])
        self.assertIn("tmux", result["error"])


class GcStaleHookDirsTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        self._hooks = self._dir / "tba-hooks"
        self._patch = mock.patch.object(cli_launch, "HOOKS_ROOT", self._hooks)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_drops_dirs_for_dead_sessions(self):
        self._hooks.mkdir()
        (self._hooks / "deadbeef").mkdir()
        (self._hooks / "live1234").mkdir()
        (self._hooks / "deadbeef" / "status").write_text("idle")

        live_session = {"name": "agent-cli-claude-live1234"}
        with mock.patch.object(core_sessions, "list_sessions", return_value=[live_session]):
            removed = cli_launch.gc_stale_hook_dirs()

        self.assertEqual(removed, 1)
        self.assertTrue((self._hooks / "live1234").exists())
        self.assertFalse((self._hooks / "deadbeef").exists())

    def test_no_hooks_root_is_noop(self):
        # HOOKS_ROOT doesn't exist; should return 0 without raising.
        self.assertEqual(cli_launch.gc_stale_hook_dirs(), 0)


if __name__ == "__main__":
    unittest.main()
