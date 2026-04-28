"""Per-CLI status detection from tmux pane content + hook-file lookup.

Each detector takes raw pane bytes (with ANSI codes already in place) and
returns one of ``"running" | "waiting" | "idle"``. The dispatcher
``detect_status_from_content`` strips ANSI before handing off so plain
substring matches survive ``capture-pane -e`` colouring.

Hook-based CLIs (Claude Code, Cursor) ship stub detectors that always
return ``"idle"``; the real status comes from a file written by the
agent's own hooks (see ``cli_hooks.py``, K3).

``detect_for_session`` is the public entry for live sessions: given a
``agent-cli-<binary>-<uid>`` session name it prefers the hook-file at
``/tmp/tba-hooks/<uid>/status`` (authoritative) and falls back to
``capture-pane`` + the per-CLI detector.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Status = Literal["running", "idle", "waiting", "error"]

# Braille spinner glyphs that most TUIs cycle through while busy.
_SPINNER_CHARS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# ANSI escape sequence: ESC [ ... letter, plus a few rarer forms. capture-pane
# -e injects these between characters and would otherwise split signals like
# "esc interrupt" so they no longer match as plain substrings.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]")


def strip_ansi(content: str) -> str:
    """Remove ANSI escape codes so substring matchers see clean text."""
    return _ANSI_RE.sub("", content)


def _last_lines(text: str, n: int) -> tuple[list[str], str]:
    """Return (last n non-empty lines, them joined). Used by every detector."""
    lines = text.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    tail = non_empty[-n:]
    return tail, "\n".join(tail)


def detect_claude_status(_content: str) -> Status:
    """Claude Code uses hook-based detection (settings.json events). The
    file written by the hook is the authoritative source; this stub only
    runs in the brief gap before the first hook fires."""
    return "idle"


def detect_opencode_status(raw_content: str) -> Status:
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    # RUNNING — OpenCode shows "esc to interrupt" while working
    if "esc to interrupt" in last_lines or "esc interrupt" in last_lines:
        return "running"
    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"

    # WAITING — selection menus
    if "enter to select" in last_lines or "esc to cancel" in last_lines:
        return "waiting"

    # WAITING — permission/confirmation prompts
    permission_prompts = ("(y/n)", "[y/n]", "continue?", "proceed?", "approve", "allow")
    if any(p in last_lines for p in permission_prompts):
        return "waiting"

    # WAITING — numbered selection ("❯ 1.", "❯ 2.", "❯ 3.")
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("❯") and len(trimmed) > 2:
            after = trimmed[1:].lstrip()
            if after.startswith(("1.", "2.", "3.")):
                return "waiting"
    if any(("❯" in ln) and (" 1." in ln or " 2." in ln or " 3." in ln) for ln in lines):
        return "waiting"

    # WAITING — bare prompt cursor in last 10 non-empty lines
    for line in non_empty[-10:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> ", ">>"):
            return "waiting"
        if clean.startswith("> ") and "esc" not in clean and len(clean) < 100:
            return "waiting"

    # WAITING — completion phrase + prompt cursor nearby
    completion = (
        "complete", "done", "finished", "ready",
        "what would you like", "what else", "anything else",
        "how can i help", "let me know",
    )
    if any(c in last_lines for c in completion):
        for line in non_empty[-10:][::-1]:
            clean = strip_ansi(line).strip()
            if clean in (">", "> ", ">>"):
                return "waiting"

    return "idle"


def detect_vibe_status(raw_content: str) -> Status:
    """Vibe (Mistral) renders via Textual TUI which can lay text vertically
    (one char per line) — joining recent single-char lines reconstructs words
    so substring matching still works."""
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])
    recent_text = "".join(ln.strip() for ln in non_empty[-50:])
    recent_lower = recent_text.lower()

    # WAITING — navigation hints, tool warning, approval options.
    if (
        "↑↓ navigate" in last_lines
        or "enter select" in last_lines
        or "esc reject" in last_lines
    ):
        return "waiting"
    raw_last = "\n".join(non_empty[-30:])  # case-preserving for symbols
    if "⚠" in raw_last and "command" in last_lines:
        return "waiting"
    approval_options = (
        "yes and always allow", "no and tell the agent",
        "› 1.", "› 2.", "› 3.",
    )
    if any(opt in last_lines for opt in approval_options):
        return "waiting"
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("›") and len(trimmed) > 2:
            return "waiting"

    # RUNNING — spinners (anywhere) + activity indicators (recent text).
    if any(s in recent_text for s in _SPINNER_CHARS):
        return "running"
    activity = (
        "running", "reading", "writing", "executing",
        "processing", "generating", "thinking",
    )
    if any(a in recent_lower for a in activity):
        return "running"
    if recent_text.endswith("…") or recent_text.endswith("..."):
        return "running"

    return "idle"


def detect_gemini_status(raw_content: str) -> Status:
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    if "esc to interrupt" in last_lines or "ctrl+c to interrupt" in last_lines:
        return "running"
    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"

    approval_prompts = (
        "(y/n)", "[y/n]", "allow", "approve", "execute?",
        "enter to select", "esc to cancel",
    )
    if any(p in last_lines for p in approval_prompts):
        return "waiting"

    for line in non_empty[-10:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> "):
            return "waiting"

    return "idle"


def detect_cursor_status(_content: str) -> Status:
    """Cursor CLI uses hook-based detection (same JSON shape as Claude Code)."""
    return "idle"


def detect_copilot_status(raw_content: str) -> Status:
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"
    running_markers = (
        "thinking", "working", "esc to interrupt", "ctrl+c to interrupt",
    )
    if any(m in last_lines for m in running_markers):
        return "running"

    approval_prompts = (
        "approve", "allow", "(y/n)", "[y/n]",
        "continue?", "run command?", "allow this tool", "approve for the rest",
    )
    if any(p in last_lines for p in approval_prompts):
        return "waiting"
    if "enter to select" in last_lines or "esc to cancel" in last_lines:
        return "waiting"

    for line in non_empty[-10:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> ", "copilot>"):
            return "waiting"
        if clean.startswith("> ") and "esc" not in clean and len(clean) < 100:
            return "waiting"

    return "idle"


def detect_pi_status(raw_content: str) -> Status:
    """Pi auto-approves all tool use, so we only distinguish running vs
    waiting-for-input (no approval-gate state). The prompt cursor takes
    priority over activity words because words like ``reading`` can linger
    in scrollback after the agent finishes and shows a prompt."""
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"
    if "esc to interrupt" in last_lines or "ctrl+c to interrupt" in last_lines:
        return "running"

    # Prompt cursor first (priority over activity words in scrollback).
    for line in non_empty[-5:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> ", "pi>"):
            return "waiting"
        if clean.startswith("> ") and "esc" not in clean and len(clean) < 100:
            return "waiting"

    activity = ("thinking", "working", "reading", "writing", "executing")
    if any(a in last_lines for a in activity):
        return "running"

    return "idle"


def detect_droid_status(raw_content: str) -> Status:
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"
    running_markers = (
        "esc to interrupt", "ctrl+c to interrupt",
        "thinking", "working", "executing",
    )
    if any(m in last_lines for m in running_markers):
        return "running"

    approval_prompts = (
        "approve", "allow", "(y/n)", "[y/n]",
        "continue?", "proceed?", "execute?",
    )
    if any(p in last_lines for p in approval_prompts):
        return "waiting"
    if "enter to select" in last_lines or "esc to cancel" in last_lines:
        return "waiting"

    for line in non_empty[-10:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> ", "droid>"):
            return "waiting"
        if clean.startswith("> ") and "esc" not in clean and len(clean) < 100:
            return "waiting"

    return "idle"


def detect_settl_status(_content: str) -> Status:
    """settl uses TOML-based hooks instead of a JSON settings file. We
    don't ship a TOML installer in K3, so this stub returns idle and the
    operator wires the hooks manually if they want them. Harmless default."""
    return "idle"


def detect_codex_status(raw_content: str) -> Status:
    content = raw_content.lower()
    lines = content.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    last_lines = "\n".join(non_empty[-30:])

    # RUNNING — Codex's working/thinking indicators
    running_markers = (
        "esc to interrupt", "ctrl+c to interrupt", "working", "thinking",
    )
    if any(m in last_lines for m in running_markers):
        return "running"
    for line in lines:
        if any(s in line for s in _SPINNER_CHARS):
            return "running"

    # WAITING — approval prompts
    approval_prompts = (
        "approve", "allow", "(y/n)", "[y/n]",
        "continue?", "proceed?", "execute?", "run command?",
    )
    if any(p in last_lines for p in approval_prompts):
        return "waiting"

    # WAITING — selection menus
    if "enter to select" in last_lines or "esc to cancel" in last_lines:
        return "waiting"

    # WAITING — numbered selection
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("❯") and len(trimmed) > 2:
            after = trimmed[1:].lstrip()
            if after.startswith(("1.", "2.", "3.")):
                return "waiting"

    # WAITING — input prompt ready
    for line in non_empty[-10:][::-1]:
        clean = strip_ansi(line).strip()
        if clean in (">", "> ", "codex>"):
            return "waiting"
        if clean.startswith("> ") and "esc" not in clean and len(clean) < 100:
            return "waiting"

    return "idle"


# Map canonical CLI name -> detector. Built lazily so cli_registry can import
# this module without a circular dep.
_DETECTORS = {
    "claude": detect_claude_status,
    "opencode": detect_opencode_status,
    "codex": detect_codex_status,
    "vibe": detect_vibe_status,
    "gemini": detect_gemini_status,
    "agent": detect_cursor_status,  # cursor's binary is "agent"
    "copilot": detect_copilot_status,
    "pi": detect_pi_status,
    "droid": detect_droid_status,
    "settl": detect_settl_status,
}


def detect_status_from_content(content: str, name: str) -> Status:
    """Strip ANSI then dispatch to the named CLI's detector. Unknown names
    return ``"idle"`` so the caller can fall back to the core content-hash
    idle baseline."""
    detector = _DETECTORS.get(name)
    if detector is None:
        return "idle"
    return detector(strip_ansi(content))


# Set in __init__-time wiring; held as a module attribute so tests can patch
# without importing cli_launch (which would create a real circular dep).
HOOKS_ROOT = Path("/tmp/tba-hooks")


def _read_hook_status(instance_id: str) -> Status | None:
    """Return the status string written by the CLI's settings.json hook,
    or None if no file exists or its contents aren't a known status."""
    if not instance_id:
        return None
    path = HOOKS_ROOT / instance_id / "status"
    try:
        raw = path.read_text(encoding="utf-8").strip().lower()
    except (OSError, ValueError):
        return None
    if raw in ("running", "idle", "waiting", "error"):
        return raw  # type: ignore[return-value]
    return None


def detect_for_session(session_name: str, *, capture: callable | None = None) -> Status:
    """Resolve a status for a ``agent-cli-<binary>-<uid>`` tmux session.

    Hook-file wins because it's authoritative (the CLI itself wrote it).
    Pane-parse is a fallback for CLIs without a hooks contract or for the
    brief gap before the first hook fires. Unknown sessions return idle.

    ``capture`` lets callers (mostly tests) override the pane-fetch path.
    By default it uses ``lib.sessions.capture_target`` against window 0.
    """
    # Imported lazily to avoid a hard dep at import time when callers only
    # use the pane-parse half (e.g. unit tests of detectors alone).
    from . import cli_launch  # local import — already ours, no cycle risk

    parsed = cli_launch.parse_session_name(session_name)
    if parsed is None:
        return "idle"
    binary, instance_id = parsed

    status = _read_hook_status(instance_id)
    if status is not None:
        return status

    if capture is None:
        try:
            from lib import sessions as _sessions
            ok, content = _sessions.capture_target(
                _sessions.Target(session=session_name, window=None, pane=None),
                lines=200,
                ansi=False,
            )
            if not ok:
                return "idle"
        except Exception:
            return "idle"
    else:
        try:
            content = capture(session_name)
        except Exception:
            return "idle"

    return detect_status_from_content(content, binary)
