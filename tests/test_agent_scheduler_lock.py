"""File-based scheduler ownership lock."""

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

from agent import scheduler_lock as lock  # noqa: E402


class _TmpLockMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._lock_path = Path(self._tmpdir.name) / "agent-scheduler.lock"
        self._patch = mock.patch.object(lock, "LOCK_FILE", self._lock_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()


class AcquireTests(_TmpLockMixin, unittest.TestCase):

    def test_acquire_when_no_lock(self):
        self.assertTrue(lock.acquire())
        self.assertTrue(self._lock_path.exists())

    def test_acquire_is_idempotent(self):
        lock.acquire()
        self.assertTrue(lock.acquire())

    def test_acquire_fails_if_another_pid_alive(self):
        data = {"pid": os.getpid() + 99999}
        self._lock_path.write_text(json.dumps(data))
        with mock.patch.object(lock, "_pid_alive", return_value=True):
            self.assertFalse(lock.acquire())

    def test_acquire_succeeds_if_stale_pid(self):
        data = {"pid": 99999}
        self._lock_path.write_text(json.dumps(data))
        with mock.patch.object(lock, "_pid_alive", return_value=False):
            self.assertTrue(lock.acquire())


class ReleaseTests(_TmpLockMixin, unittest.TestCase):

    def test_release_removes_lock(self):
        lock.acquire()
        lock.release()
        self.assertFalse(self._lock_path.exists())

    def test_release_noop_if_not_owner(self):
        data = {"pid": os.getpid() + 99999}
        self._lock_path.write_text(json.dumps(data))
        lock.release()
        self.assertTrue(self._lock_path.exists())

    def test_release_noop_if_no_lock(self):
        lock.release()  # should not raise


class IsOwnedTests(_TmpLockMixin, unittest.TestCase):

    def test_owned_after_acquire(self):
        lock.acquire()
        self.assertTrue(lock.is_owned())

    def test_not_owned_when_no_lock(self):
        self.assertFalse(lock.is_owned())


class OwnerInfoTests(_TmpLockMixin, unittest.TestCase):

    def test_returns_none_when_no_lock(self):
        self.assertIsNone(lock.owner_info())

    def test_returns_dict_when_locked(self):
        lock.acquire()
        info = lock.owner_info()
        self.assertIsInstance(info, dict)
        self.assertEqual(info["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
