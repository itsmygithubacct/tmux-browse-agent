"""Config-lock enforcement on agent mutation endpoints.

These mirror the core ``tests/test_config_lock.py`` coverage for the
handlers that moved into the extension. The unlock-token machinery
still lives on the core :class:`lib.server.Handler` — extension
handlers call ``handler._check_unlock()`` which delegates there.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from lib import server  # noqa: E402
from lib import config as cfg  # noqa: E402
from server import routes  # noqa: E402


class _FakeHandler:
    def __init__(self, headers=None):
        self.payload = None
        self.status = None
        self.headers = headers or {}

    def _send_json(self, obj, status=200):
        self.payload = obj
        self.status = status

    def _send_tb_error(self, err):
        return server.Handler._send_tb_error(self, err)

    def _check_unlock(self):
        return server.Handler._check_unlock(self)


class _LockedConfigMixin:
    password = "hunter2"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            cfg, "CONFIG_LOCK_FILE", Path(self._tmp.name) / "lock")
        self._patch.start()
        cfg.CONFIG_LOCK_FILE.write_text(
            hashlib.sha256(self.password.encode()).hexdigest() + "\n")
        server._unlock_tokens.clear()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class AgentMutationGateTests(_LockedConfigMixin, unittest.TestCase):

    def test_locked_dashboard_rejects_agents_post_without_token(self):
        fake = _FakeHandler(headers={})
        routes._h_agents_post(fake, urlparse("/api/agents"),
                              {"agent": {"name": "x"}})
        self.assertEqual(fake.status, 403)
        self.assertIn("config locked", fake.payload["error"])

    def test_locked_dashboard_rejects_agents_post_with_bad_token(self):
        fake = _FakeHandler(headers={"X-TB-Unlock-Token": "nope"})
        routes._h_agents_post(fake, urlparse("/api/agents"),
                              {"agent": {"name": "x"}})
        self.assertEqual(fake.status, 403)

    def test_locked_dashboard_accepts_agents_post_with_valid_token(self):
        t = server._issue_unlock_token()
        fake = _FakeHandler(headers={"X-TB-Unlock-Token": t})
        with mock.patch("server.routes.agent_store.save_agent", return_value={
            "name": "x", "has_api_key": True, "provider": "custom",
            "model": "m", "base_url": "http://x", "wire_api": "openai-chat",
        }):
            routes._h_agents_post(fake, urlparse("/api/agents"),
                                  {"agent": {"name": "x", "api_key": "sk-x"}})
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["ok"])

    def test_hooks_post_gated(self):
        fake = _FakeHandler(headers={})
        routes._h_agent_hooks_post(fake, urlparse("/api/agent-hooks"),
                                   {"hooks": {}})
        self.assertEqual(fake.status, 403)

    def test_workflows_post_gated(self):
        fake = _FakeHandler(headers={})
        routes._h_agent_workflows_post(fake, urlparse("/api/agent-workflows"),
                                       {"config": {}})
        self.assertEqual(fake.status, 403)

    def test_agents_remove_gated(self):
        fake = _FakeHandler(headers={})
        routes._h_agents_remove(fake, urlparse("/api/agents/remove"),
                                {"name": "x"})
        self.assertEqual(fake.status, 403)


class UnlockedAgentMutationTests(unittest.TestCase):
    """When no lock is configured, agent mutations proceed without a token."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            cfg, "CONFIG_LOCK_FILE", Path(self._tmp.name) / "no-lock")
        self._patch.start()
        server._unlock_tokens.clear()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_no_lock_allows_agents_post(self):
        fake = _FakeHandler(headers={})
        with mock.patch("server.routes.agent_store.save_agent", return_value={
            "name": "x", "has_api_key": True, "provider": "custom",
            "model": "m", "base_url": "http://x", "wire_api": "openai-chat",
        }):
            routes._h_agents_post(fake, urlparse("/api/agents"),
                                  {"agent": {"name": "x", "api_key": "sk-x"}})
        self.assertEqual(fake.status, 200)


if __name__ == "__main__":
    unittest.main()
