"""Agent wire-API provider registry + dispatch."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from agent import providers as ap  # noqa: E402
from lib.errors import AuthError, StateError, UsageError  # noqa: E402


class RegistryTests(unittest.TestCase):

    def test_openai_and_anthropic_registered(self):
        self.assertIn("openai-chat", ap.PROVIDERS)
        self.assertIn("anthropic-messages", ap.PROVIDERS)

    def test_complete_unknown_wire_api_raises(self):
        with self.assertRaises(UsageError):
            ap.complete({"wire_api": "nonesuch"}, [], timeout=1.0)

    def test_complete_dispatches_to_matching_provider(self):
        calls = {}

        def fake_provider(agent, messages, timeout):
            calls["agent"] = agent
            return ap.ProviderResult(content="ok")

        with mock.patch.dict(ap.PROVIDERS, {"fake": fake_provider}):
            out = ap.complete({"wire_api": "fake", "x": 1}, [{"r": "u"}], timeout=1.0)
        self.assertEqual(out.content, "ok")
        self.assertEqual(calls["agent"], {"wire_api": "fake", "x": 1})


class TextExtractionTests(unittest.TestCase):

    def test_openai_string_content(self):
        self.assertEqual(ap._text_from_openai_content("hello"), "hello")

    def test_openai_list_of_parts(self):
        blocks = [{"text": "first"}, {"text": "second"}, {"nope": "ignored"}]
        self.assertEqual(ap._text_from_openai_content(blocks), "first\nsecond")

    def test_anthropic_filters_by_type_text(self):
        blocks = [{"type": "text", "text": "keep"},
                  {"type": "thinking", "text": "drop"}]
        self.assertEqual(ap._text_from_anthropic_content(blocks), "keep")


class PostJsonErrorMappingTests(unittest.TestCase):

    def _urlopen_http_error(self, code):
        err = urllib.error.HTTPError(
            "http://x", code, "err", hdrs=None, fp=io.BytesIO(b"denied"),
        )
        return mock.patch("urllib.request.urlopen", side_effect=err)

    def test_401_maps_to_auth_error(self):
        with self._urlopen_http_error(401):
            with self.assertRaises(AuthError):
                ap._post_json("http://x", {}, {}, timeout=1.0)

    def test_403_maps_to_auth_error(self):
        with self._urlopen_http_error(403):
            with self.assertRaises(AuthError):
                ap._post_json("http://x", {}, {}, timeout=1.0)

    def test_500_maps_to_state_error(self):
        with self._urlopen_http_error(500):
            with self.assertRaises(StateError):
                ap._post_json("http://x", {}, {}, timeout=1.0)

    def test_urlerror_maps_to_state_error(self):
        urlerr = urllib.error.URLError("down")
        with mock.patch("urllib.request.urlopen", side_effect=urlerr):
            with self.assertRaises(StateError):
                ap._post_json("http://x", {}, {}, timeout=1.0)


class OpenAIChatShapeTests(unittest.TestCase):
    """Validate the request payload + response parsing without network."""

    def test_sends_expected_payload_and_parses_choice(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode())
            return _FakeResp(json.dumps({
                "choices": [{"message": {"content": "hello"}}]
            }).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = ap.openai_chat(
                {"model": "m", "api_key": "k", "base_url": "https://api/v1"},
                [{"role": "user", "content": "hi"}],
                timeout=1.0,
            )
        self.assertIsInstance(out, ap.ProviderResult)
        self.assertEqual(out.content, "hello")
        self.assertEqual(captured["url"], "https://api/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "m")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer k")

    def test_malformed_response_raises_state_error(self):
        def fake_urlopen(req, timeout):
            return _FakeResp(b'{"weird": true}')

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(StateError):
                ap.openai_chat(
                    {"model": "m", "api_key": "k", "base_url": "https://x"},
                    [{"role": "user", "content": "hi"}],
                    timeout=1.0,
                )

    def test_minimax_sets_reasoning_split(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["body"] = json.loads(req.data.decode())
            return _FakeResp(json.dumps({
                "choices": [{"message": {"content": "{\"type\":\"final\",\"message\":\"ok\"}"}}]
            }).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = ap.openai_chat(
                {"model": "MiniMax-M2.7", "api_key": "k", "base_url": "https://api.minimax.io/v1", "provider": "minimax"},
                [{"role": "user", "content": "hi"}],
                timeout=1.0,
            )
        self.assertEqual(out.content, '{"type":"final","message":"ok"}')
        self.assertTrue(captured["body"]["reasoning_split"])


class AnthropicMessagesShapeTests(unittest.TestCase):

    def test_system_messages_are_folded_out(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _FakeResp(json.dumps({
                "content": [{"type": "text", "text": "pong"}]
            }).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = ap.anthropic_messages(
                {"model": "m", "api_key": "k", "base_url": "https://a/v1"},
                [
                    {"role": "system", "content": "be helpful"},
                    {"role": "user", "content": "hi"},
                ],
                timeout=1.0,
            )
        self.assertEqual(out.content, "pong")
        # system rolled into payload["system"], not messages
        self.assertEqual(captured["url"], "https://a/v1/messages")
        self.assertEqual(captured["body"]["system"], "be helpful")
        self.assertEqual(captured["body"]["messages"],
                         [{"role": "user", "content": "hi"}])

    def test_base_url_without_v1_gets_messages_under_v1(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            return _FakeResp(json.dumps({
                "content": [{"type": "text", "text": "pong"}]
            }).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = ap.anthropic_messages(
                {"model": "m", "api_key": "k", "base_url": "https://api.kimi.com/coding"},
                [{"role": "user", "content": "hi"}],
                timeout=1.0,
            )
        self.assertEqual(out.content, "pong")
        self.assertEqual(captured["url"], "https://api.kimi.com/coding/v1/messages")

    def test_invalid_role_raises_usage_error(self):
        with mock.patch("urllib.request.urlopen"):
            with self.assertRaises(UsageError):
                ap.anthropic_messages(
                    {"model": "m", "api_key": "k", "base_url": "https://a"},
                    [{"role": "tool", "content": "x"}],
                    timeout=1.0,
                )


class _FakeResp:
    """Minimal urlopen context-manager stand-in."""
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


if __name__ == "__main__":
    unittest.main()
