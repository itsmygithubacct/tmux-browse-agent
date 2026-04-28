"""K6: GET /api/agent-cli + POST /api/agent-cli/launch route surface."""

import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_EXT))

from lib import server  # noqa: E402
from server import routes  # noqa: E402


class _FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = None
        self.headers = {}

    def _send_json(self, obj, status=200):
        self.payload = obj
        self.status = status

    def _check_unlock(self):
        return server.Handler._check_unlock(self)


class CliRouteTableTests(unittest.TestCase):

    def test_routes_are_registered(self):
        reg = routes.register()
        self.assertIn("/api/agent-cli", reg.get_routes)
        self.assertIn("/api/agent-cli/launch", reg.post_routes)
        self.assertIn("/api/agent-cli/install-hooks", reg.post_routes)
        self.assertIn("/api/agent-cli/uninstall-hooks", reg.post_routes)


class CliGetHandlerTests(unittest.TestCase):

    def test_get_returns_registry_with_install_state(self):
        fake = _FakeHandler()
        with mock.patch("server.routes.agent_cli_launch.is_installed",
                        side_effect=lambda c: c.name == "claude"), \
             mock.patch("server.routes.agent_cli_hooks.is_installed", return_value=False):
            routes._h_agent_cli_get(fake, urlparse("/api/agent-cli"))
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["ok"])
        names = {row["name"] for row in fake.payload["agents"]}
        # All 10 registry entries surface in the GET payload.
        self.assertEqual(
            names,
            {"claude", "opencode", "codex", "vibe", "gemini",
             "cursor", "copilot", "pi", "droid", "settl"},
        )
        # Only claude was reported as installed by our patch.
        installed = {row["name"] for row in fake.payload["agents"] if row["installed"]}
        self.assertEqual(installed, {"claude"})
        # Hooks-supported flag tracks the registry config.
        with_hooks = {row["name"] for row in fake.payload["agents"] if row["hooks_supported"]}
        self.assertEqual(with_hooks, {"claude", "gemini", "cursor"})


class CliLaunchHandlerTests(unittest.TestCase):

    def test_launch_unlock_required(self):
        # Default _FakeHandler has no unlock token; the lock check should
        # short-circuit the handler. Mock the env so the lock is "active".
        fake = _FakeHandler()
        with mock.patch.object(server.Handler, "_check_unlock", return_value=False):
            routes._h_agent_cli_launch(fake, urlparse("/api/agent-cli/launch"), {"name": "claude"})
        # When _check_unlock returns False, the handler returns early without
        # populating payload. _check_unlock itself emits the 401.
        self.assertIsNone(fake.payload)

    def test_launch_missing_name(self):
        fake = _FakeHandler()
        with mock.patch.object(server.Handler, "_check_unlock", return_value=True):
            routes._h_agent_cli_launch(fake, urlparse("/api/agent-cli/launch"), {})
        self.assertEqual(fake.status, 400)
        self.assertFalse(fake.payload["ok"])

    def test_launch_delegates_to_cli_launch(self):
        fake = _FakeHandler()
        fake_result = {"ok": True, "session": "agent-cli-claude-deadbeef",
                       "instance_id": "deadbeef", "binary": "claude", "name": "claude",
                       "cwd": "/tmp"}
        with mock.patch.object(server.Handler, "_check_unlock", return_value=True), \
             mock.patch("server.routes.agent_cli_launch.launch", return_value=fake_result) as launch:
            routes._h_agent_cli_launch(
                fake, urlparse("/api/agent-cli/launch"),
                {"name": "claude", "cwd": "/tmp", "yolo": True},
            )
        launch.assert_called_once_with("claude", cwd="/tmp", instruction=None, yolo=True)
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["ok"])

    def test_launch_install_required_returns_409(self):
        fake = _FakeHandler()
        fake_result = {"ok": False, "error": "claude not found",
                       "install_required": True,
                       "install_hint": "npm install -g @anthropic-ai/claude-code"}
        with mock.patch.object(server.Handler, "_check_unlock", return_value=True), \
             mock.patch("server.routes.agent_cli_launch.launch", return_value=fake_result):
            routes._h_agent_cli_launch(
                fake, urlparse("/api/agent-cli/launch"), {"name": "claude"},
            )
        self.assertEqual(fake.status, 409)


if __name__ == "__main__":
    unittest.main()
