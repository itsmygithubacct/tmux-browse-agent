"""Tool registry for agent runs.

Each tool declares how it dispatches on host vs sandbox. The runner
looks up the callable by (tool_name, sandbox_mode) pair so a new tool
adds itself by registering here; no runner surgery required.

Current tools:
- ``tb_command``: existing ``tb`` shell-out; dispatches via
  :func:`agent_runner._run_tb_command` on host and
  :meth:`docker_sandbox.Sandbox.exec_tb` in Docker mode.
- ``read_file``: bounded file read, 64 KiB cap. Host path-validated
  against the same blocklist as docker_sandbox mounts. Sandbox dispatch
  reads container-local paths via ``docker exec head -c``.

See ``~/research/tmux-browse/plans/plan_tool_sandbox.md`` for the
design rationale.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from lib import docker_sandbox


READ_FILE_MAX_BYTES = 64 * 1024
READ_FILE_DEFAULT_MAX_BYTES = 16 * 1024


@dataclass
class ToolResult:
    ok: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    json_data: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    # host-mode dispatch: (repo_root, args, stdin) -> ToolResult
    run_host: Callable[..., ToolResult]
    # sandbox-mode dispatch: (sandbox, args, stdin) -> ToolResult
    # Set to None for a tool that doesn't make sense under Docker.
    run_sandbox: Callable[..., ToolResult] | None


# -----------------------------------------------------------------------------
# tb_command (kept here for registry lookup; actual implementation wraps
# agent_runner's existing callable to avoid a circular import).
# -----------------------------------------------------------------------------

def _tb_command_host(repo_root, args, stdin):
    from . import runner
    return agent_runner._run_tb_command(repo_root, args, stdin)


def _tb_command_sandbox(sandbox, args, stdin, timeout=60):
    return sandbox.exec_tb(args, stdin, timeout=timeout)


# -----------------------------------------------------------------------------
# read_file
# -----------------------------------------------------------------------------

def _is_sensitive_path(resolved: Path) -> bool:
    """Mirror docker_sandbox.BLOCKED_HOME_SUBPATHS for the host path."""
    home = Path.home().resolve()
    try:
        rel = resolved.relative_to(home)
    except ValueError:
        return False
    first = rel.parts[0] if rel.parts else ""
    return first in docker_sandbox.BLOCKED_HOME_SUBPATHS


def _read_file_host(repo_root: Path, args: dict, _stdin: str | None) -> ToolResult:
    path_str = str(args.get("path") or "").strip()
    try:
        max_bytes = int(args.get("max_bytes") or READ_FILE_DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = READ_FILE_DEFAULT_MAX_BYTES
    max_bytes = max(1, min(READ_FILE_MAX_BYTES, max_bytes))
    cmd = ["read_file", path_str, str(max_bytes)]
    if not path_str:
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr="path required")
    target = Path(path_str).expanduser()
    if not target.is_absolute():
        target = (repo_root / target)
    try:
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr=f"cannot resolve path: {e}")
    if _is_sensitive_path(resolved):
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr="path is in a blocked directory")
    if not resolved.exists():
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr=f"file not found: {resolved}")
    if not resolved.is_file():
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr=f"not a regular file: {resolved}")
    try:
        with resolved.open("rb") as fh:
            data = fh.read(max_bytes)
    except OSError as e:
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr=f"read failed: {e}")
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = repr(data)
    return ToolResult(
        ok=True, command=cmd, exit_code=0,
        stdout=text,
        stderr="" if len(data) < max_bytes else f"truncated at {max_bytes} bytes",
        json_data={"path": str(resolved), "bytes_read": len(data)},
    )


def _read_file_sandbox(sandbox, args: dict, _stdin: str | None) -> ToolResult:
    """Read a container-local file via ``docker exec head -c``.

    The container mounts only ``/workspace`` (rw) and ``/opt/tmux-browse``
    (ro), so host paths are physically unreachable. We still enforce
    an allowlist on the container-side prefix so the model's mistakes
    surface with a clear error.
    """
    path_str = str(args.get("path") or "").strip()
    try:
        max_bytes = int(args.get("max_bytes") or READ_FILE_DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = READ_FILE_DEFAULT_MAX_BYTES
    max_bytes = max(1, min(READ_FILE_MAX_BYTES, max_bytes))
    cmd = ["read_file", path_str, str(max_bytes)]
    if not path_str:
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="", stderr="path required")
    if not (path_str.startswith("/workspace/") or path_str == "/workspace"
            or path_str.startswith("/opt/tmux-browse")):
        return ToolResult(ok=False, command=cmd, exit_code=2,
                          stdout="",
                          stderr=("in Docker mode read_file only accepts "
                                  "paths under /workspace or /opt/tmux-browse"))
    docker_cmd = [
        "docker", "exec", sandbox.container_name,
        "head", "-c", str(max_bytes), path_str,
    ]
    try:
        proc = subprocess.run(docker_cmd, capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return ToolResult(ok=False, command=docker_cmd, exit_code=2,
                          stdout="", stderr=f"docker exec failed: {e}")
    if proc.returncode != 0:
        return ToolResult(ok=False, command=docker_cmd,
                          exit_code=proc.returncode,
                          stdout=proc.stdout or "",
                          stderr=(proc.stderr or "").strip())
    return ToolResult(
        ok=True, command=docker_cmd, exit_code=0,
        stdout=proc.stdout or "",
        stderr="",
        json_data={"path": path_str, "bytes_read": len(proc.stdout or "")},
    )


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------

TOOLS: dict[str, ToolSpec] = {
    "tb_command": ToolSpec(
        name="tb_command",
        description=("Run a non-interactive tb.py command. "
                     "args: list of strings; stdin: optional string."),
        run_host=_tb_command_host,
        run_sandbox=_tb_command_sandbox,
    ),
    "read_file": ToolSpec(
        name="read_file",
        description=("Read up to max_bytes (default 16 KiB, cap 64 KiB) "
                     "from a file. args: {path, max_bytes?}. In Docker "
                     "mode only /workspace/... or /opt/tmux-browse/... "
                     "paths are accepted."),
        run_host=_read_file_host,
        run_sandbox=_read_file_sandbox,
    ),
}

DEFAULT_TOOLS = ["tb_command"]


def tool_names_for_agent(agent_cfg: dict[str, Any]) -> list[str]:
    """Return the enabled tool list with sane defaults. Unknown names
    are dropped so a typo in agents.json doesn't crash the runner."""
    raw = agent_cfg.get("tools")
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_TOOLS)
    out = [t for t in raw if isinstance(t, str) and t in TOOLS]
    return out or list(DEFAULT_TOOLS)


def tool_prompt_block(names: list[str]) -> str:
    """Render the prompt section describing enabled tools."""
    lines = ["Enabled tools:"]
    for name in names:
        spec = TOOLS.get(name)
        if not spec:
            continue
        lines.append(f"- {name}: {spec.description}")
    return "\n".join(lines) + "\n"
