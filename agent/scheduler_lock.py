"""File-based scheduler ownership lock.

Only one dashboard process should execute workflows at a time.  This
module provides a PID-based lock file that prevents duplicate execution
when multiple dashboard instances start (e.g. after a restart that
doesn't fully kill the old process).

The lock file stores the owning PID.  ``acquire`` succeeds if:
- the lock file does not exist, or
- the PID in the lock file no longer refers to a running process.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from lib import config


LOCK_FILE = config.STATE_DIR / "agent-scheduler.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def acquire() -> bool:
    """Try to acquire the scheduler lock for this process.

    Returns True if the lock was acquired (or already held by us).
    """
    config.ensure_dirs()
    my_pid = os.getpid()

    if LOCK_FILE.exists():
        try:
            data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            held_pid = int(data.get("pid", 0))
        except (ValueError, OSError, TypeError):
            held_pid = 0

        if held_pid == my_pid:
            return True
        if held_pid and _pid_alive(held_pid):
            return False
        # Stale lock — previous holder is dead.

    _write_lock(my_pid)
    return True


def release() -> None:
    """Release the scheduler lock if we hold it."""
    my_pid = os.getpid()
    if not LOCK_FILE.exists():
        return
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        if int(data.get("pid", 0)) != my_pid:
            return
    except (ValueError, OSError, TypeError):
        pass
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_owned() -> bool:
    """Return True if the current process holds the lock."""
    if not LOCK_FILE.exists():
        return False
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        return int(data.get("pid", 0)) == os.getpid()
    except (ValueError, OSError, TypeError):
        return False


def owner_info() -> dict | None:
    """Return the lock file contents, or None if no lock."""
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError, TypeError):
        return None


def _write_lock(pid: int) -> None:
    data = {"pid": pid, "acquired_ts": int(time.time())}
    try:
        LOCK_FILE.write_text(
            json.dumps(data) + "\n", encoding="utf-8",
        )
    except OSError:
        pass
