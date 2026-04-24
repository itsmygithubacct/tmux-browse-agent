"""Dashboard agent API handlers."""

import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import server  # noqa: E402
from lib.errors import StateError, UsageError  # noqa: E402


class _FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = None
        # Empty headers dict is sufficient when no config lock is active
        # (the common case for these tests). Lock-enforcement tests
        # populate this via headers["X-TB-Unlock-Token"] directly.
        self.headers = {}

    def _send_json(self, obj, status=200):
        self.payload = obj
        self.status = status

    def _send_tb_error(self, err):
        return server.Handler._send_tb_error(self, err)

    def _check_unlock(self):
        return server.Handler._check_unlock(self)


class AgentRouteTableTests(unittest.TestCase):

    def test_routes_are_registered(self):
        self.assertIn("/api/agents", server.Handler._GET_ROUTES)
        self.assertIn("/api/agent-log", server.Handler._GET_ROUTES)
        self.assertIn("/api/agent-log-json", server.Handler._GET_ROUTES)
        self.assertIn("/api/agent-workflows", server.Handler._GET_ROUTES)
        self.assertIn("/api/agents", server.Handler._POST_ROUTES)
        self.assertIn("/api/agents/remove", server.Handler._POST_ROUTES)
        self.assertIn("/api/agent-workflows", server.Handler._POST_ROUTES)
        self.assertIn("/api/agent-conversation", server.Handler._POST_ROUTES)


class AgentHandlerTests(unittest.TestCase):

    def test_agents_get_returns_public_rows_and_catalog(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.list_agents", return_value=[{
            "name": "gpt", "provider": "openai", "model": "gpt-5.4",
            "base_url": "https://api.openai.com/v1", "wire_api": "openai-chat",
            "has_api_key": True,
        }]), mock.patch("lib.server.agent_store.catalog_rows", return_value=[{
            "name": "gpt", "label": "OpenAI GPT", "provider": "openai",
            "model": "gpt-5.4", "base_url": "https://api.openai.com/v1",
            "wire_api": "openai-chat",
        }]), mock.patch("lib.server.agent_store.AGENTS_FILE", Path("/tmp/agents.json")), mock.patch(
            "lib.server.agent_store.SECRETS_FILE", Path("/tmp/agent-secrets.json"),
        ):
            server.Handler._h_agents_get(fake, urlparse("/api/agents"))
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["ok"])
        self.assertEqual(fake.payload["agents"][0]["name"], "gpt")
        self.assertNotIn("api_key", fake.payload["agents"][0])
        self.assertEqual(fake.payload["defaults"][0]["label"], "OpenAI GPT")
        self.assertEqual(fake.payload["paths"]["agents"], "/tmp/agents.json")
        # docker_supported is host-global capability, must always be present
        self.assertIn("docker_supported", fake.payload)
        self.assertIsInstance(fake.payload["docker_supported"], bool)

    def test_agent_log_returns_plain_text(self):
        fake = _FakeHandler()
        fake.text = None
        fake.text_status = None

        def send_text(text, status=200):
            fake.text = text
            fake.text_status = status

        fake._send_text = send_text
        with mock.patch("lib.server.agent_logs.render_text", return_value="hello\n"):
            server.Handler._h_agent_log(fake, urlparse("/api/agent-log?name=gpt"))
        self.assertEqual(fake.text_status, 200)
        self.assertEqual(fake.text, "hello\n")

    def test_agent_log_json_returns_entries(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_logs.read_entries", return_value=[{"ts": 1, "status": "ok"}]), mock.patch(
            "lib.server.agent_logs.log_path", return_value=Path("/tmp/gpt.jsonl"),
        ):
            server.Handler._h_agent_log_json(fake, urlparse("/api/agent-log-json?name=gpt"))
        self.assertEqual(fake.status, 200)
        self.assertEqual(fake.payload["entries"][0]["status"], "ok")
        self.assertEqual(fake.payload["path"], "/tmp/gpt.jsonl")

    def test_agent_workflows_get_returns_config(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_workflows.load", return_value={"agents": {}}), mock.patch(
            "lib.server.config.AGENT_WORKFLOWS_FILE", Path("/tmp/agent-workflows.json"),
        ):
            server.Handler._h_agent_workflows_get(fake, urlparse("/api/agent-workflows"))
        self.assertEqual(fake.status, 200)
        self.assertEqual(fake.payload["path"], "/tmp/agent-workflows.json")

    def test_agents_post_saves_agent(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.save_agent", return_value={
            "name": "gpt",
            "provider": "openai",
            "model": "gpt-5.4",
            "base_url": "https://api.openai.com/v1",
            "wire_api": "openai-chat",
            "has_api_key": True,
        }) as save_agent:
            server.Handler._h_agents_post(fake, urlparse("/api/agents"), {
                "agent": {
                    "name": "gpt",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "base_url": "https://api.openai.com/v1",
                    "wire_api": "openai-chat",
                    "api_key": "sk-abc",
                },
            })
        self.assertEqual(fake.status, 200)
        self.assertEqual(fake.payload["agent"]["name"], "gpt")
        save_agent.assert_called_once_with(
            "gpt",
            api_key="sk-abc",
            model="gpt-5.4",
            base_url="https://api.openai.com/v1",
            provider="openai",
            wire_api="openai-chat",
            sandbox=None,
            token_budget=None,
            daily_token_budget=None,
        )

    def test_agents_post_maps_usage_error_to_400(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.save_agent", side_effect=UsageError("bad agent")):
            server.Handler._h_agents_post(fake, urlparse("/api/agents"), {
                "agent": {"name": "", "provider": "", "model": "", "base_url": "", "wire_api": ""},
            })
        self.assertEqual(fake.status, 400)
        self.assertFalse(fake.payload["ok"])
        self.assertEqual(fake.payload["error"], "bad agent")

    def test_agents_post_rejects_invalid_token_budget(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.save_agent") as save_agent:
            server.Handler._h_agents_post(fake, urlparse("/api/agents"), {
                "agent": {
                    "name": "gpt",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "base_url": "https://api.openai.com/v1",
                    "wire_api": "openai-chat",
                    "token_budget": "not-a-number",
                },
            })
        self.assertEqual(fake.status, 400)
        self.assertFalse(fake.payload["ok"])
        self.assertEqual(fake.payload["error"], "token_budget must be an integer")
        save_agent.assert_not_called()

    def test_agents_remove_returns_removed_state(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.remove_agent", return_value=True) as remove_agent:
            server.Handler._h_agents_remove(fake, urlparse("/api/agents/remove"), {"name": "gpt"})
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["removed"])
        remove_agent.assert_called_once_with("gpt")

    def test_agent_workflows_post_saves(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_workflows.save", return_value={"agents": {"gpt": {"enabled": True, "workflows": []}}}), mock.patch(
            "lib.server.config.AGENT_WORKFLOWS_FILE", Path("/tmp/agent-workflows.json"),
        ):
            server.Handler._h_agent_workflows_post(fake, urlparse("/api/agent-workflows"), {"config": {"agents": {}}})
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["config"]["agents"]["gpt"]["enabled"])

    def test_agent_conversation_open_creates_session_and_ttyd(self):
        fake = _FakeHandler()
        fake.server = type("S", (), {"tls_paths": None, "ttyd_bind_addr": "127.0.0.1"})()
        with mock.patch("lib.server.agent_store.get_agent", return_value={"name": "gpt"}), mock.patch(
            "lib.server.sessions.exists", return_value=False,
        ), mock.patch(
            "lib.server.sessions.new_session", return_value=(True, ""),
        ) as new_session, mock.patch(
            "lib.server.ttyd.start",
            return_value={"ok": True, "port": 7777, "scheme": "http", "already": False},
        ):
            server.Handler._h_agent_conversation_open(fake, urlparse("/api/agent-conversation"), {"name": "gpt"})
        self.assertEqual(fake.status, 200)
        self.assertEqual(fake.payload["session"], "agent-repl-gpt")
        new_session.assert_called_once()

    def test_agents_get_maps_state_error_to_json_500(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.list_agents", side_effect=StateError("broken store")):
            server.Handler._h_agents_get(fake, urlparse("/api/agents"))
        self.assertEqual(fake.status, 500)
        self.assertFalse(fake.payload["ok"])
        self.assertEqual(fake.payload["error"], "broken store")

    def test_docker_supported_reflects_host_capability(self):
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.list_agents", return_value=[]), \
             mock.patch("lib.server.agent_store.catalog_rows", return_value=[]), \
             mock.patch("lib.server.docker_sandbox.SUPPORTED", True):
            server.Handler._h_agents_get(fake, urlparse("/api/agents"))
        self.assertTrue(fake.payload["docker_supported"])

        fake2 = _FakeHandler()
        with mock.patch("lib.server.agent_store.list_agents", return_value=[]), \
             mock.patch("lib.server.agent_store.catalog_rows", return_value=[]), \
             mock.patch("lib.server.docker_sandbox.SUPPORTED", False):
            server.Handler._h_agents_get(fake2, urlparse("/api/agents"))
        self.assertFalse(fake2.payload["docker_supported"])

    def test_save_path_accepts_docker_when_unavailable(self):
        # Persistence is independent of transient Docker availability.
        fake = _FakeHandler()
        with mock.patch("lib.server.agent_store.save_agent", return_value={
            "name": "opus", "provider": "anthropic", "model": "claude-opus-4-7",
            "base_url": "https://api.anthropic.com/v1", "wire_api": "anthropic-messages",
            "sandbox": "docker", "has_api_key": True,
        }) as save, mock.patch("lib.server.docker_sandbox.SUPPORTED", False):
            server.Handler._h_agents_post(fake, urlparse("/api/agents"), {
                "agent": {
                    "name": "opus", "api_key": "sk-x", "sandbox": "docker",
                },
            })
        self.assertEqual(fake.status, 200)
        self.assertTrue(fake.payload["ok"])
        self.assertEqual(save.call_args.kwargs["sandbox"], "docker")


if __name__ == "__main__":
    unittest.main()
