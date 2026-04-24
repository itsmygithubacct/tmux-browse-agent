"""Narrow re-export surface for everything the agent extension
consumes from tmux-browse core.

This module is the **one** place imports reach across the
extension/core boundary. When the extension moves to its own
repository, only this file needs a replacement strategy (a thin
``pip install tmux-browse`` shim, a pinned-version import path, or
whatever the packaging story looks like at that point).

Rules:
- Anywhere an agent module wants ``lib.X``, it imports ``agent.core_api``
  and uses the re-exported name instead.
- Adding a new re-export here is a deliberate act — it widens the
  contract with core and has to stay stable across extension
  releases.
- Nothing in this file does real work. If you want to do work, add
  a helper module in ``agent/`` and import core through here.
"""

from __future__ import annotations

# --- config: paths + ensure_dirs --------------------------------------

from lib.config import (  # noqa: F401
    STATE_DIR,
    PROJECT_DIR,
    AGENT_LOG_DIR,
    AGENT_CONVERSATIONS_DIR,
    AGENT_WORKFLOWS_FILE,
    AGENT_HOOKS_FILE,
    AGENT_NOTIFICATIONS_FILE,
    AGENT_CONDUCTOR_FILE,
    AGENT_CONDUCTOR_LOG,
    AGENT_CONTEXT_DIR,
    AGENT_KB_DIR,
    ensure_dirs,
)

# --- sandbox + session logging (shared primitives) -------------------

from lib import docker_sandbox, session_logs  # noqa: F401

# --- session / target / errors ---------------------------------------

from lib import sessions  # noqa: F401
from lib.targeting import Target  # noqa: F401
from lib.errors import (  # noqa: F401
    TBError,
    UsageError,
    StateError,
    Timeout,
    TmuxFailed,
    SessionNotFound,
    SessionExists,
)

# --- agent-adjacent core modules -------------------------------------
# tasks.py + worktrees.py stay in core because a task with an
# ``agent`` field is just data; the agent extension populates/consumes
# that field without core needing to know what an agent is.
from lib import tasks as _tasks, worktrees as _worktrees  # noqa: F401
