"""Conversation session management for configured agents."""

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

from agent import conversations as ac  # noqa: E402
from agent import runtime as rt  # noqa: E402


class _TmpMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            ac, "CONVERSATIONS_DIR", Path(self._tmpdir.name),
        )
        self._patch.start()
        # Clear the in-memory cache between tests.
        rt._active.clear()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


class GetOrCreateTests(_TmpMixin, unittest.TestCase):

    def test_creates_on_first_call(self):
        cid = rt.get_or_create_conversation("opus")
        self.assertIsInstance(cid, str)
        header = ac.load_header(cid)
        self.assertEqual(header["agent_name"], "opus")

    def test_returns_same_on_second_call(self):
        cid1 = rt.get_or_create_conversation("opus")
        cid2 = rt.get_or_create_conversation("opus")
        self.assertEqual(cid1, cid2)

    def test_different_agents_different_conversations(self):
        c1 = rt.get_or_create_conversation("opus")
        c2 = rt.get_or_create_conversation("gpt")
        self.assertNotEqual(c1, c2)


class LoadContextTests(_TmpMixin, unittest.TestCase):

    def test_empty_context_initially(self):
        ctx = rt.load_context("opus")
        self.assertEqual(ctx, [])

    def test_context_after_recording_turns(self):
        rt.record_turn("opus", role="user", content="hello")
        rt.record_turn("opus", role="assistant", content="hi")
        ctx = rt.load_context("opus")
        self.assertEqual(len(ctx), 2)
        self.assertEqual(ctx[0], {"role": "user", "content": "hello"})


class RecordTurnTests(_TmpMixin, unittest.TestCase):

    def test_record_and_load(self):
        rt.record_turn("gpt", role="user", content="test")
        cid = rt.get_or_create_conversation("gpt")
        turns = ac.load_turns(cid)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["content"], "test")

    def test_record_with_run_id(self):
        rt.record_turn("gpt", role="assistant", content="done", run_id="r1")
        cid = rt.get_or_create_conversation("gpt")
        turns = ac.load_turns(cid)
        self.assertEqual(turns[0]["run_id"], "r1")


class ForkTests(_TmpMixin, unittest.TestCase):

    def test_fork_creates_new_conversation(self):
        rt.record_turn("opus", role="user", content="hello")
        old_cid = rt.get_or_create_conversation("opus")
        new_cid = rt.fork_conversation("opus")
        self.assertNotEqual(old_cid, new_cid)

    def test_fork_copies_turns(self):
        rt.record_turn("opus", role="user", content="shared")
        rt.fork_conversation("opus")
        ctx = rt.load_context("opus")
        self.assertEqual(len(ctx), 1)
        self.assertEqual(ctx[0]["content"], "shared")

    def test_fork_switches_active(self):
        rt.record_turn("opus", role="user", content="a")
        old_cid = rt.get_or_create_conversation("opus")
        new_cid = rt.fork_conversation("opus")
        current = rt.get_or_create_conversation("opus")
        self.assertEqual(current, new_cid)
        self.assertNotEqual(current, old_cid)


class StartNewTests(_TmpMixin, unittest.TestCase):

    def test_start_new_replaces_active(self):
        cid1 = rt.get_or_create_conversation("opus")
        cid2 = rt.start_new_conversation("opus")
        self.assertNotEqual(cid1, cid2)
        self.assertEqual(rt.get_or_create_conversation("opus"), cid2)


class ClearTests(_TmpMixin, unittest.TestCase):

    def test_clear_removes_conversation(self):
        rt.record_turn("opus", role="user", content="hi")
        self.assertTrue(rt.clear_conversation("opus"))
        ctx = rt.load_context("opus")
        self.assertEqual(ctx, [])

    def test_clear_nonexistent_returns_false(self):
        self.assertFalse(rt.clear_conversation("nope"))


class NamingTests(unittest.TestCase):

    def test_conversation_session_name(self):
        self.assertEqual(rt.conversation_session_name("Opus"),
                         "agent-repl-opus")

    def test_agent_name_from_session(self):
        self.assertEqual(rt.agent_name_from_session("agent-repl-gpt"), "gpt")

    def test_agent_name_from_non_repl_session(self):
        self.assertIsNone(rt.agent_name_from_session("dashboard"))


if __name__ == "__main__":
    unittest.main()
