"""Agent extension lifecycle hooks.

The core loader calls ``register()`` at load time and receives a dict
of ``on_server_start`` / ``on_server_stop`` callables. The start hook
spins up the workflow scheduler and hangs it on the server so the stop
hook can clean it up.
"""

from __future__ import annotations

from agent import scheduler as agent_scheduler
from lib import config


# Holds the running scheduler between start and stop. The server process
# only ever has one, so a module-level handle is fine — the stop hook has
# no httpd reference to thread it through.
_sched: agent_scheduler.Scheduler | None = None


def register() -> dict:
    return {
        "on_server_start": [_start_scheduler],
        "on_server_stop": [_stop_scheduler],
    }


def _start_scheduler(httpd) -> None:
    global _sched
    _sched = agent_scheduler.Scheduler(repo_root=config.PROJECT_DIR)
    httpd.scheduler = _sched
    if _sched.start():
        print("  scheduler: STARTED (this process owns workflow execution)")
    else:
        print("  scheduler: passive (another process holds the lock)")


def _stop_scheduler() -> None:
    global _sched
    if _sched is not None:
        _sched.stop()
        _sched = None
