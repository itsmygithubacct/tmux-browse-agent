"""Spawn CLI agents inside tmux sessions.

Bridge between the registry (``cli_registry``) and the core's tmux plumbing
(``lib.sessions``). One launch creates one session named
``agent-cli-<binary>-<8-char-uid>``. The uid doubles as
``TBA_INSTANCE_ID``; settings.json hooks (installed in K3) write status
files under ``/tmp/tba-hooks/<uid>/`` keyed off that env var.
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
from pathlib import Path

from lib import config, sessions

from . import cli_registry


SESSION_PREFIX = "agent-cli-"
HOOKS_ROOT = Path("/tmp/tba-hooks")


def is_installed(cli: cli_registry.CliAgentDef) -> bool:
    """Return True if the agent binary is available on the host.

    Uses ``which`` by default; agents that don't show up there (e.g. shims
    that only respond to a specific subcommand) opt into running
    ``<binary> <detection_arg>`` and checking the exit code instead.
    """
    if cli.detection_arg is None:
        return shutil.which(cli.binary) is not None
    try:
        r = subprocess.run(
            [cli.binary, cli.detection_arg],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def session_name_for(cli: cli_registry.CliAgentDef, instance_id: str) -> str:
    return f"{SESSION_PREFIX}{cli.binary}-{instance_id}"


def parse_session_name(session: str) -> tuple[str, str] | None:
    """Reverse of ``session_name_for``: return (binary, instance_id) or None.

    Used by ``runtime.agent_name_from_session`` (extended in K4) and by
    ``cli_detect.detect_for_session`` to locate the registry entry for a
    live session.
    """
    if not session.startswith(SESSION_PREFIX):
        return None
    rest = session[len(SESSION_PREFIX):]
    # The instance_id is always the trailing 8 hex chars after the last dash;
    # the binary may itself contain dashes (none in K1's set, but allow it).
    dash = rest.rfind("-")
    if dash <= 0:
        return None
    binary = rest[:dash]
    instance_id = rest[dash + 1:]
    if not binary or len(instance_id) != 8:
        return None
    return binary, instance_id


def gc_stale_hook_dirs() -> int:
    """Drop hook-state dirs whose tmux session no longer exists. Returns the
    count removed. Called opportunistically on each launch so /tmp doesn't
    accumulate forever on long-running hosts."""
    if not HOOKS_ROOT.exists():
        return 0
    live_uids: set[str] = set()
    try:
        for s in sessions.list_sessions():
            parsed = parse_session_name(s["name"])
            if parsed is not None:
                live_uids.add(parsed[1])
    except Exception:
        # If we can't list sessions, leave the dirs alone — better to leak
        # than to delete state for a session we just couldn't see.
        return 0
    removed = 0
    for child in HOOKS_ROOT.iterdir():
        if not child.is_dir():
            continue
        if child.name in live_uids:
            continue
        try:
            shutil.rmtree(child)
            removed += 1
        except OSError:
            pass
    return removed


def _build_command(cli: cli_registry.CliAgentDef,
                   instruction: str | None,
                   instance_id: str,
                   yolo_enabled: bool) -> str:
    """Assemble the shell command tmux runs as the new session's PID 1.

    Pieces, in order: ``TBA_INSTANCE_ID=<id>`` env, any ``yolo=("env", ...)``
    env vars, ``stty susp undef`` to swallow Ctrl-Z so the agent can't be
    suspended out of the user's reach, the binary, any ``yolo=("flag", ...)``
    appended, and the optional instruction template.
    """
    env_prefix = [f"TBA_INSTANCE_ID={shlex.quote(instance_id)}"]
    flags: list[str] = []

    if yolo_enabled and cli.yolo is not None:
        kind, value = cli.yolo
        if kind == "env":
            env_prefix.append(value)  # already in K=V form
        elif kind == "flag":
            flags.extend(value.split())
        # "always" needs no action

    parts: list[str] = list(env_prefix)
    parts.append("stty")
    parts.append("susp")
    parts.append("undef")
    parts.append("&&")
    parts.append(cli.binary)
    parts.extend(flags)

    if instruction and cli.instruction_flag:
        rendered = cli.instruction_flag.format(shlex.quote(instruction))
        parts.extend(rendered.split())

    return " ".join(parts)


def launch(name: str, *,
           cwd: str | None = None,
           instruction: str | None = None,
           yolo: bool = False) -> dict:
    """Spawn a CLI agent in a fresh tmux session.

    Returns a dict with ``ok``. On success, includes ``session``,
    ``instance_id``, ``binary``, and ``name``. On failure due to a missing
    binary, includes ``install_required: True`` and the registry's
    ``install_hint`` so the caller can surface it to the operator.
    """
    cli = cli_registry.find(name)
    if cli is None:
        return {"ok": False, "error": f"unknown CLI agent: {name!r}"}
    if not is_installed(cli):
        return {
            "ok": False,
            "error": f"{cli.binary} not found on $PATH",
            "install_required": True,
            "install_hint": cli.install_hint,
        }

    gc_stale_hook_dirs()

    instance_id = secrets.token_hex(4)
    session = session_name_for(cli, instance_id)
    cmd = _build_command(cli, instruction, instance_id, yolo)
    target_cwd = cwd or str(config.PROJECT_DIR)
    if not os.path.isdir(target_cwd):
        return {"ok": False, "error": f"cwd does not exist: {target_cwd}"}

    ok, err = sessions.new_session(session, cwd=target_cwd, cmd=cmd)
    if not ok:
        return {"ok": False, "error": err or "tmux new-session failed"}

    return {
        "ok": True,
        "session": session,
        "instance_id": instance_id,
        "binary": cli.binary,
        "name": cli.name,
        "cwd": target_cwd,
    }
