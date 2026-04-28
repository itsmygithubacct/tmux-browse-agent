"""CLI-agent registry shape, lookup, and override merge."""

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

from agent import cli_registry  # noqa: E402
from lib import config as cfg  # noqa: E402


class _IsolatedStateMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(cfg, "STATE_DIR", d),
            mock.patch.object(cli_registry, "CLI_REGISTRY_OVERRIDE_FILE",
                              d / "agent-cli-registry.json"),
        ]
        for p in self._patches:
            p.start()
        self._dir = d

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class RegistryShapeTests(unittest.TestCase):
    """Each built-in entry must satisfy a few invariants the rest of the
    extension relies on (UI labels, install hints, dispatcher wiring)."""

    def test_every_entry_has_required_fields(self):
        for agent in cli_registry._BUILTIN_REGISTRY:
            self.assertTrue(agent.name, f"{agent} missing name")
            self.assertTrue(agent.binary, f"{agent.name} missing binary")
            self.assertTrue(agent.install_hint, f"{agent.name} missing install_hint")
            self.assertIsNotNone(agent.detect_status, f"{agent.name} missing detector")

    def test_names_are_unique(self):
        seen = [a.name for a in cli_registry._BUILTIN_REGISTRY]
        self.assertEqual(len(seen), len(set(seen)), "duplicate CLI agent names")

    def test_k1_entries_present(self):
        # K1 ships claude, opencode, codex. K5 fills the rest.
        names = cli_registry.names()
        self.assertIn("claude", names)
        self.assertIn("opencode", names)
        self.assertIn("codex", names)


class FindTests(unittest.TestCase):

    def test_find_by_name(self):
        agent = cli_registry.find("claude")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.binary, "claude")

    def test_find_is_case_insensitive_and_trims(self):
        self.assertIsNotNone(cli_registry.find("  CLAUDE  "))

    def test_find_by_alias(self):
        # opencode's alias is "open-code"
        agent = cli_registry.find("open-code")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.name, "opencode")

    def test_find_unknown_returns_none(self):
        self.assertIsNone(cli_registry.find("nope"))
        self.assertIsNone(cli_registry.find(""))


class SendKeysDelayTests(unittest.TestCase):

    def test_codex_has_paste_burst_delay(self):
        # Codex's 120ms paste-burst window swallows fast Enters; we delay 150ms.
        self.assertGreaterEqual(cli_registry.send_keys_enter_delay_ms("codex"), 150)

    def test_others_zero_delay(self):
        self.assertEqual(cli_registry.send_keys_enter_delay_ms("claude"), 0)
        self.assertEqual(cli_registry.send_keys_enter_delay_ms("opencode"), 0)
        self.assertEqual(cli_registry.send_keys_enter_delay_ms("unknown"), 0)


class OverrideMergeTests(_IsolatedStateMixin, unittest.TestCase):

    def test_override_adds_new_entry(self):
        path = self._dir / "agent-cli-registry.json"
        path.write_text(json.dumps({
            "myagent": {
                "binary": "myagent",
                "label": "My Custom Agent",
                "install_hint": "pip install myagent",
            }
        }), encoding="utf-8")
        names = cli_registry.names()
        self.assertIn("myagent", names)
        agent = cli_registry.find("myagent")
        self.assertEqual(agent.label, "My Custom Agent")

    def test_override_replaces_builtin(self):
        path = self._dir / "agent-cli-registry.json"
        path.write_text(json.dumps({
            "codex": {
                "binary": "/opt/codex-pinned/bin/codex",
                "install_hint": "internal install",
            }
        }), encoding="utf-8")
        agent = cli_registry.find("codex")
        self.assertEqual(agent.binary, "/opt/codex-pinned/bin/codex")
        # Built-in order preserved
        names = cli_registry.names()
        self.assertEqual(names.index("codex"), 2)

    def test_invalid_override_silently_ignored(self):
        path = self._dir / "agent-cli-registry.json"
        path.write_text("not json at all", encoding="utf-8")
        # Should not raise; just falls back to built-ins.
        names = cli_registry.names()
        self.assertEqual(set(names), {"claude", "opencode", "codex"})


if __name__ == "__main__":
    unittest.main()
