"""settings.json hook installer: install / uninstall round-trip + user-hook preservation."""

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

from agent import cli_hooks  # noqa: E402


class _IsolatedHomeMixin:
    """Redirect ``Path.home()`` to a tempdir so the hook installer writes to
    a sandbox we can inspect, not the real user's settings.json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._home = Path(self._tmp.name)
        self._patch = mock.patch.object(cli_hooks.Path, "home",
                                        classmethod(lambda cls: self._home))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _claude_settings(self) -> Path:
        return self._home / ".claude" / "settings.json"


class InstallTests(_IsolatedHomeMixin, unittest.TestCase):

    def test_creates_file_when_missing(self):
        result = cli_hooks.install("claude")
        self.assertTrue(result["ok"], result)
        path = self._claude_settings()
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIn("hooks", data)
        self.assertIn("PreToolUse", data["hooks"])
        # Our marker must be in the installed command.
        cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("tba-hooks", cmd)
        self.assertIn("$TBA_INSTANCE_ID", cmd)

    def test_idempotent(self):
        cli_hooks.install("claude")
        first = json.loads(self._claude_settings().read_text())
        cli_hooks.install("claude")
        second = json.loads(self._claude_settings().read_text())
        # Same shape, no duplicates after re-install.
        self.assertEqual(first, second)
        self.assertEqual(len(second["hooks"]["PreToolUse"]), 1)

    def test_preserves_user_hooks(self):
        path = self._claude_settings()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "user-rule",
                    "hooks": [{"type": "command", "command": "echo my-own-thing"}],
                }],
            },
            "model": "claude-opus",
        }))
        cli_hooks.install("claude")
        data = json.loads(path.read_text())
        # User entry still present...
        commands = [
            h["command"]
            for matcher in data["hooks"]["PreToolUse"]
            for h in matcher.get("hooks", [])
        ]
        self.assertIn("echo my-own-thing", commands)
        # ...and ours appended too.
        self.assertTrue(any("tba-hooks" in c for c in commands))
        # Other top-level keys untouched.
        self.assertEqual(data["model"], "claude-opus")

    def test_unknown_agent(self):
        result = cli_hooks.install("nope")
        self.assertFalse(result["ok"])
        self.assertIn("unknown", result["error"])

    def test_agent_without_hooks_contract(self):
        # opencode has no hooks config in K1's registry.
        result = cli_hooks.install("opencode")
        self.assertFalse(result["ok"])
        self.assertIn("no hooks contract", result["error"])

    def test_is_installed_round_trip(self):
        self.assertFalse(cli_hooks.is_installed("claude"))
        cli_hooks.install("claude")
        self.assertTrue(cli_hooks.is_installed("claude"))


class UninstallTests(_IsolatedHomeMixin, unittest.TestCase):

    def test_no_op_when_file_missing(self):
        result = cli_hooks.uninstall("claude")
        self.assertTrue(result["ok"])
        self.assertFalse(result["removed"])

    def test_strips_ours_keeps_users(self):
        path = self._claude_settings()
        path.parent.mkdir(parents=True)
        # Pre-populate with a user hook + an old tba-hooks entry as if a
        # previous install had run.
        path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "user-rule", "hooks": [
                        {"type": "command", "command": "echo my-own-thing"}
                    ]},
                    {"matcher": None, "hooks": [
                        {"type": "command", "command": "sh -c 'tba-hooks stuff'"}
                    ]},
                ],
                "Stop": [
                    {"hooks": [
                        {"type": "command", "command": "sh -c 'tba-hooks stop'"}
                    ]},
                ],
            },
        }))
        result = cli_hooks.uninstall("claude")
        self.assertTrue(result["removed"])

        data = json.loads(path.read_text())
        # User PreToolUse entry kept.
        commands = [
            h["command"]
            for matcher in data["hooks"]["PreToolUse"]
            for h in matcher.get("hooks", [])
        ]
        self.assertIn("echo my-own-thing", commands)
        self.assertFalse(any("tba-hooks" in c for c in commands))
        # Stop event was entirely ours, so removed.
        self.assertNotIn("Stop", data["hooks"])

    def test_drops_empty_hooks_section(self):
        # Install + immediately uninstall leaves no `hooks` key behind so
        # the file is indistinguishable from never-installed.
        cli_hooks.install("claude")
        cli_hooks.uninstall("claude")
        data = json.loads(self._claude_settings().read_text())
        self.assertNotIn("hooks", data)


class HookCommandShapeTests(unittest.TestCase):

    def test_command_has_required_pieces(self):
        cmd = cli_hooks._hook_command("running")
        self.assertIn("$TBA_INSTANCE_ID", cmd)
        self.assertIn("/tmp/tba-hooks", cmd)
        self.assertIn("printf running", cmd)


if __name__ == "__main__":
    unittest.main()
