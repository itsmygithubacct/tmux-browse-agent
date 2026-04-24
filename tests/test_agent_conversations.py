"""Persistent conversation storage for agent REPLs."""

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

from agent import conversations as ac  # noqa: E402


class _TmpDirMixin:
    """Redirect CONVERSATIONS_DIR to a temp dir for each test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            ac, "CONVERSATIONS_DIR", Path(self._tmpdir.name),
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


class CreateTests(_TmpDirMixin, unittest.TestCase):

    def test_create_returns_conversation_id(self):
        cid = ac.create("opus")
        self.assertIsInstance(cid, str)
        self.assertGreater(len(cid), 5)

    def test_create_writes_header(self):
        cid = ac.create("opus")
        header = ac.load_header(cid)
        self.assertIsNotNone(header)
        self.assertEqual(header["type"], "header")
        self.assertEqual(header["agent_name"], "opus")
        self.assertEqual(header["conversation_id"], cid)
        self.assertIsNone(header["parent_id"])

    def test_create_with_parent(self):
        parent = ac.create("opus")
        child = ac.create("opus", parent_id=parent)
        header = ac.load_header(child)
        self.assertEqual(header["parent_id"], parent)


class TurnTests(_TmpDirMixin, unittest.TestCase):

    def test_append_and_load_turns(self):
        cid = ac.create("gpt")
        ac.append_turn(cid, role="user", content="hello")
        ac.append_turn(cid, role="assistant", content="hi there", run_id="r1")
        turns = ac.load_turns(cid)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["content"], "hello")
        self.assertEqual(turns[1]["role"], "assistant")
        self.assertEqual(turns[1]["run_id"], "r1")

    def test_load_messages_returns_role_content_pairs(self):
        cid = ac.create("gpt")
        ac.append_turn(cid, role="user", content="what sessions?")
        ac.append_turn(cid, role="assistant", content="found 3")
        msgs = ac.load_messages(cid)
        self.assertEqual(msgs, [
            {"role": "user", "content": "what sessions?"},
            {"role": "assistant", "content": "found 3"},
        ])

    def test_load_turns_of_nonexistent_returns_empty(self):
        self.assertEqual(ac.load_turns("nonexistent"), [])

    def test_load_messages_of_nonexistent_returns_empty(self):
        self.assertEqual(ac.load_messages("nonexistent"), [])


class ListTests(_TmpDirMixin, unittest.TestCase):

    def test_list_all(self):
        ac.create("opus")
        ac.create("gpt")
        convos = ac.list_conversations()
        self.assertEqual(len(convos), 2)
        names = {c["agent_name"] for c in convos}
        self.assertEqual(names, {"opus", "gpt"})

    def test_list_filtered(self):
        ac.create("opus")
        ac.create("gpt")
        convos = ac.list_conversations(agent_name="opus")
        self.assertEqual(len(convos), 1)
        self.assertEqual(convos[0]["agent_name"], "opus")


class ForkTests(_TmpDirMixin, unittest.TestCase):

    def test_fork_copies_turns(self):
        cid = ac.create("opus")
        ac.append_turn(cid, role="user", content="hello")
        ac.append_turn(cid, role="assistant", content="hi", run_id="r1")
        new_cid = ac.fork(cid)
        self.assertNotEqual(new_cid, cid)
        turns = ac.load_turns(new_cid)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["content"], "hello")
        self.assertEqual(turns[1]["content"], "hi")

    def test_fork_sets_parent_id(self):
        cid = ac.create("opus")
        new_cid = ac.fork(cid)
        header = ac.load_header(new_cid)
        self.assertEqual(header["parent_id"], cid)
        self.assertEqual(header["agent_name"], "opus")

    def test_fork_with_custom_agent_name(self):
        cid = ac.create("opus")
        new_cid = ac.fork(cid, agent_name="opus-fork")
        header = ac.load_header(new_cid)
        self.assertEqual(header["agent_name"], "opus-fork")

    def test_fork_nonexistent_raises(self):
        from lib.errors import StateError
        with self.assertRaises(StateError):
            ac.fork("nonexistent")

    def test_fork_and_original_diverge(self):
        cid = ac.create("opus")
        ac.append_turn(cid, role="user", content="shared")
        new_cid = ac.fork(cid)
        ac.append_turn(cid, role="user", content="original-only")
        ac.append_turn(new_cid, role="user", content="fork-only")
        orig_turns = ac.load_turns(cid)
        fork_turns = ac.load_turns(new_cid)
        self.assertEqual(len(orig_turns), 2)
        self.assertEqual(orig_turns[1]["content"], "original-only")
        self.assertEqual(len(fork_turns), 2)
        self.assertEqual(fork_turns[1]["content"], "fork-only")


class ClearTests(_TmpDirMixin, unittest.TestCase):

    def test_clear_existing(self):
        cid = ac.create("opus")
        self.assertTrue(ac.clear(cid))
        self.assertIsNone(ac.load_header(cid))

    def test_clear_nonexistent(self):
        self.assertFalse(ac.clear("nonexistent"))


if __name__ == "__main__":
    unittest.main()
