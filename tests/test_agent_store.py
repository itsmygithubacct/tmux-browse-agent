"""Agent persistence: name validation, catalog merge, add/remove round-trip."""

import json
import os
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

from agent import store as agent_store  # noqa: E402
from lib import config as cfg  # noqa: E402
from lib.errors import UsageError  # noqa: E402


class _IsolatedStateMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", d),
            mock.patch.object(cfg, "PID_DIR", d / "pids"),
            mock.patch.object(cfg, "LOG_DIR", d / "logs"),
            mock.patch.object(agent_store, "AGENTS_FILE", d / "agents.json"),
            mock.patch.object(agent_store, "SECRETS_FILE", d / "agent-secrets.json"),
            mock.patch.object(agent_store, "CATALOG_OVERRIDE_FILE",
                              d / "agent-catalog.json"),
        ]
        for p in self._patches:
            p.start()
        self._dir = d

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class ValidateNameTests(unittest.TestCase):

    def test_lowercases_and_trims(self):
        self.assertEqual(agent_store._validate_name("  Work  "), "work")

    def test_rejects_empty(self):
        with self.assertRaises(UsageError):
            agent_store._validate_name("")
        with self.assertRaises(UsageError):
            agent_store._validate_name("   ")

    def test_rejects_whitespace(self):
        with self.assertRaises(UsageError):
            agent_store._validate_name("my agent")
        with self.assertRaises(UsageError):
            agent_store._validate_name("x\ty")


class CatalogTests(_IsolatedStateMixin, unittest.TestCase):

    def test_builtins_always_present(self):
        cat = agent_store.load_catalog()
        for name in ("sonnet", "opus", "gpt", "kimi", "minimax"):
            self.assertIn(name, cat)

    def test_override_file_wins_on_collision(self):
        agent_store.CATALOG_OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        agent_store.CATALOG_OVERRIDE_FILE.write_text(json.dumps({
            "opus": {"model": "claude-opus-8-0"},
        }))
        cat = agent_store.load_catalog()
        self.assertEqual(cat["opus"]["model"], "claude-opus-8-0")
        # Other fields inherited from the built-in
        self.assertEqual(cat["opus"]["provider"], "anthropic")

    def test_override_adds_new_agent(self):
        agent_store.CATALOG_OVERRIDE_FILE.write_text(json.dumps({
            "grok": {
                "label": "xAI Grok", "provider": "xai",
                "model": "grok-4", "base_url": "https://x",
                "wire_api": "openai-chat",
            },
        }))
        cat = agent_store.load_catalog()
        self.assertEqual(cat["grok"]["model"], "grok-4")

    def test_malformed_override_ignored(self):
        agent_store.CATALOG_OVERRIDE_FILE.write_text("not json at all")
        cat = agent_store.load_catalog()
        # Still has the built-ins, no crash
        self.assertIn("sonnet", cat)

    def test_pep562_alias_returns_catalog(self):
        # agent_store.DEFAULT_CATALOG remains the public name
        self.assertEqual(agent_store.DEFAULT_CATALOG, agent_store.load_catalog())

    def test_pep562_unknown_attr_raises(self):
        with self.assertRaises(AttributeError):
            agent_store.TOTALLY_NOT_AN_ATTR  # noqa: B018


class AddRemoveTests(_IsolatedStateMixin, unittest.TestCase):

    def test_add_agent_with_builtin_defaults_fills_blanks(self):
        row = agent_store.add_agent("opus", "sk-abc")
        self.assertEqual(row["provider"], "anthropic")
        self.assertEqual(row["model"], "claude-opus-4-7")
        self.assertTrue(row["has_api_key"])

    def test_add_agent_writes_secrets_with_mode_0600(self):
        agent_store.add_agent("opus", "sk-abc")
        mode = agent_store.SECRETS_FILE.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_add_custom_agent_requires_model_and_url(self):
        with self.assertRaises(UsageError):
            agent_store.add_agent("custom", "k")   # no model, no url

    def test_remove_agent_happy_path(self):
        agent_store.add_agent("opus", "sk-abc")
        self.assertTrue(agent_store.remove_agent("opus"))
        # Subsequent remove is a no-op
        self.assertFalse(agent_store.remove_agent("opus"))

    def test_get_agent_missing_raises(self):
        with self.assertRaises(UsageError):
            agent_store.get_agent("ghost")

    def test_get_agent_returns_key(self):
        agent_store.add_agent("opus", "sk-xyz")
        got = agent_store.get_agent("opus")
        self.assertEqual(got["api_key"], "sk-xyz")

    def test_save_agent_preserves_existing_key_when_api_key_omitted(self):
        agent_store.add_agent("opus", "sk-keep")
        row = agent_store.save_agent(
            "opus",
            model="claude-opus-9-1",
            base_url="https://api.anthropic.com/v1",
            provider="anthropic",
            wire_api="anthropic-messages",
        )
        self.assertEqual(row["model"], "claude-opus-9-1")
        got = agent_store.get_agent("opus")
        self.assertEqual(got["api_key"], "sk-keep")
        self.assertEqual(got["model"], "claude-opus-9-1")

    def test_save_agent_without_existing_key_still_requires_one(self):
        with self.assertRaises(UsageError):
            agent_store.save_agent(
                "custom",
                model="m",
                base_url="https://api.example.test/v1",
                provider="example",
                wire_api="openai-chat",
            )

    def test_kimi_defaults_to_supported_provider_and_wire_api(self):
        row = agent_store.add_agent("kimi", "sk-kimi-test")
        self.assertEqual(row["provider"], "kimi")
        self.assertEqual(row["wire_api"], "anthropic-messages")
        self.assertEqual(row["base_url"], "https://api.kimi.com/coding")

    def test_kimi_rejects_openai_provider_override(self):
        with self.assertRaises(UsageError):
            agent_store.save_agent(
                "kimi",
                api_key="sk-kimi-test",
                model="K2.6-code-preview",
                base_url="https://api.kimi.com/coding",
                provider="openai",
                wire_api="anthropic-messages",
            )

    def test_kimi_rejects_openai_chat_wire_api(self):
        with self.assertRaises(UsageError):
            agent_store.save_agent(
                "kimi",
                api_key="sk-kimi-test",
                model="K2.6-code-preview",
                base_url="https://api.kimi.com/coding",
                provider="kimi",
                wire_api="openai-chat",
            )


class SandboxModeTests(_IsolatedStateMixin, unittest.TestCase):

    def test_docker_is_a_supported_mode(self):
        self.assertIn("docker", agent_store.SUPPORTED_SANDBOX_MODES)
        self.assertIn("worktree", agent_store.SUPPORTED_SANDBOX_MODES)
        self.assertIn("host", agent_store.SUPPORTED_SANDBOX_MODES)

    def test_docker_round_trips_through_save_and_get(self):
        agent_store.save_agent("opus", api_key="sk-x", sandbox="docker")
        got = agent_store.get_agent("opus")
        self.assertEqual(got["sandbox"], "docker")

    def test_invalid_sandbox_value_falls_back_to_host(self):
        # Existing invariant: garbage strings still degrade to host on read.
        # Docker remains untouched because it is in the supported set now.
        agent_store.save_agent("opus", api_key="sk-x", sandbox="docker")
        # Manually corrupt persisted value to simulate an unknown mode.
        import json as _json
        path = agent_store.AGENTS_FILE
        data = _json.loads(path.read_text())
        data["opus"]["sandbox"] = "podman"
        path.write_text(_json.dumps(data))
        got = agent_store.get_agent("opus")
        self.assertEqual(got["sandbox"], "host")

    def test_default_tools_is_tb_command(self):
        agent_store.save_agent("opus", api_key="sk-x")
        got = agent_store.get_agent("opus")
        self.assertEqual(got["tools"], ["tb_command"])

    def test_tools_round_trip(self):
        agent_store.save_agent("opus", api_key="sk-x",
                                tools=["tb_command", "read_file"])
        got = agent_store.get_agent("opus")
        self.assertEqual(got["tools"], ["tb_command", "read_file"])

    def test_empty_tools_falls_back_to_default(self):
        agent_store.save_agent("opus", api_key="sk-x", tools=[])
        got = agent_store.get_agent("opus")
        self.assertEqual(got["tools"], ["tb_command"])

    def test_docker_sandbox_persists_when_docker_unavailable(self):
        # save_agent does not consult docker_sandbox.SUPPORTED — config
        # persistence is independent of transient host capability.
        with mock.patch("lib.docker_sandbox.SUPPORTED", False):
            agent_store.save_agent("opus", api_key="sk-x", sandbox="docker")
            got = agent_store.get_agent("opus")
        self.assertEqual(got["sandbox"], "docker")


if __name__ == "__main__":
    unittest.main()
