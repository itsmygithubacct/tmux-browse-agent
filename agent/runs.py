"""Agent run identifiers and lifecycle constants.

Every invocation of ``run_agent`` gets a unique ``run_id`` so that log
entries, conversation turns, and (later) cost records can be correlated
back to a single execution.
"""

from __future__ import annotations

import time
import uuid


LOG_SCHEMA_VERSION = 1

# Lifecycle statuses written to agent log entries.
STATUS_STARTED = "run_started"
STATUS_COMPLETED = "run_completed"
STATUS_FAILED = "run_failed"
STATUS_RATE_LIMITED = "run_rate_limited"


def new_run_id() -> str:
    """Return a compact, unique, time-sortable run identifier."""
    # 8-hex epoch seconds + 12-hex random → unique + chronological
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:12]
    return f"{ts:08x}-{suffix}"
