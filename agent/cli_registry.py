"""Registry of supported CLI agents (binaries spawned in tmux sessions).

This is the second surface in the agent extension, alongside the wire-API
catalog in ``store.py``. Wire-API agents are LLM endpoints called directly
by the extension; CLI agents are external binaries (claude-code, codex,
opencode, etc.) launched inside tmux sessions and supervised through tmux
pane parsing or settings-file hooks.

Two surfaces, one Agents card. CLI agents share the dashboard's tmux
plumbing (idle alerts, hot buttons, send-bar) because every CLI session
is a normal tmux session tagged ``agent-cli-<binary>-<uid>``.

Adding a new CLI agent: append a ``CliAgentDef`` to ``_BUILTIN_REGISTRY``
and (if status detection is content-based) write a detector in
``cli_detect``. Users can override or add entries without patching source
by writing a JSON file at ``CLI_REGISTRY_OVERRIDE_FILE``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Literal

from lib import config

from . import cli_detect


CLI_REGISTRY_OVERRIDE_FILE = config.STATE_DIR / "agent-cli-registry.json"


Status = Literal["running", "idle", "waiting", "error"]


@dataclass(frozen=True)
class HookEvent:
    """One status-transition entry in a CLI's settings.json hooks map."""
    name: str
    matcher: str | None = None
    status: Status | None = None


@dataclass(frozen=True)
class HooksConfig:
    """Where and what hooks to install for file-based status detection."""
    settings_rel_path: str
    events: tuple[HookEvent, ...]


@dataclass(frozen=True)
class CliAgentDef:
    """Everything we know about a single CLI agent.

    ``name`` is the canonical short id (``"claude"``, ``"codex"``); ``binary``
    is the executable on ``$PATH``. They diverge only when the binary's name
    on disk doesn't match the brand (e.g. Cursor's CLI binary is ``agent``).
    """
    name: str
    binary: str
    label: str = ""
    aliases: tuple[str, ...] = ()
    detection_arg: str | None = None  # None = `which`; non-None = `binary <arg>`
    yolo: tuple[str, str] | None = None  # ("flag", value) | ("env", "K=V") | ("always", "")
    instruction_flag: str | None = None  # template with `{}` for the instruction
    detect_status: Callable[[str], Status] | None = None
    container_env: tuple[tuple[str, str], ...] = ()
    hooks: HooksConfig | None = None
    host_only: bool = False
    send_keys_enter_delay_ms: int = 0
    install_hint: str = ""
    set_default_command: bool = False  # if true, the binary itself is the launch cmd


# Hooks shared by Claude Code and Cursor CLI. They both read the same set of
# event names from a settings.json under their own config dir.
_CLAUDE_CURSOR_HOOK_EVENTS: tuple[HookEvent, ...] = (
    HookEvent(name="PreToolUse", status="running"),
    HookEvent(name="UserPromptSubmit", status="running"),
    HookEvent(name="Stop", status="idle"),
    HookEvent(
        name="Notification",
        matcher="permission_prompt|elicitation_dialog",
        status="waiting",
    ),
    HookEvent(name="ElicitationResult", status="running"),
)


_BUILTIN_REGISTRY: tuple[CliAgentDef, ...] = (
    CliAgentDef(
        name="claude",
        binary="claude",
        label="Claude Code",
        yolo=("flag", "--dangerously-skip-permissions"),
        instruction_flag="--append-system-prompt {}",
        detect_status=cli_detect.detect_claude_status,
        container_env=(("CLAUDE_CONFIG_DIR", "/root/.claude"),),
        hooks=HooksConfig(
            settings_rel_path=".claude/settings.json",
            events=_CLAUDE_CURSOR_HOOK_EVENTS,
        ),
        install_hint="npm install -g @anthropic-ai/claude-code",
    ),
    CliAgentDef(
        name="opencode",
        binary="opencode",
        label="OpenCode",
        aliases=("open-code",),
        yolo=("env", 'OPENCODE_PERMISSION={"*":"allow"}'),
        detect_status=cli_detect.detect_opencode_status,
        set_default_command=True,
        install_hint="curl -fsSL https://opencode.ai/install | bash",
    ),
    CliAgentDef(
        name="codex",
        binary="codex",
        label="OpenAI Codex",
        yolo=("flag", "--dangerously-bypass-approvals-and-sandbox"),
        instruction_flag="--config developer_instructions={}",
        detect_status=cli_detect.detect_codex_status,
        # Codex has a 120ms paste-burst window; Enter keys arriving inside it
        # are swallowed as newlines. 150ms outlasts the suppression.
        send_keys_enter_delay_ms=150,
        set_default_command=True,
        install_hint="npm install -g @openai/codex",
    ),
    CliAgentDef(
        name="vibe",
        binary="vibe",
        label="Mistral Vibe",
        aliases=("mistral-vibe",),
        # Vibe doesn't show up via `which` reliably; probe via --version.
        detection_arg="--version",
        yolo=("flag", "--agent auto-approve"),
        detect_status=cli_detect.detect_vibe_status,
        install_hint="pip install mistral-vibe",
    ),
    CliAgentDef(
        name="gemini",
        binary="gemini",
        label="Google Gemini",
        yolo=("flag", "--approval-mode yolo"),
        detect_status=cli_detect.detect_gemini_status,
        hooks=HooksConfig(
            settings_rel_path=".gemini/settings.json",
            events=(
                HookEvent(name="BeforeTool", status="running"),
                HookEvent(name="BeforeAgent", status="running"),
                HookEvent(name="AfterAgent", status="idle"),
                HookEvent(name="Notification", matcher="ToolPermission", status="waiting"),
            ),
        ),
        install_hint="npm install -g @google/gemini-cli",
    ),
    CliAgentDef(
        name="cursor",
        # Cursor's CLI ships under `agent`, not `cursor`. Aliasing keeps the
        # canonical name brand-aligned while detection/spawn use the binary.
        binary="agent",
        label="Cursor CLI",
        aliases=("agent",),
        yolo=("flag", "--yolo"),
        detect_status=cli_detect.detect_cursor_status,
        container_env=(("CURSOR_CONFIG_DIR", "/root/.cursor"),),
        hooks=HooksConfig(
            settings_rel_path=".cursor/settings.json",
            events=_CLAUDE_CURSOR_HOOK_EVENTS,
        ),
        install_hint="see https://docs.cursor.com/cli",
    ),
    CliAgentDef(
        name="copilot",
        binary="copilot",
        label="GitHub Copilot",
        aliases=("github-copilot",),
        yolo=("flag", "--yolo"),
        detect_status=cli_detect.detect_copilot_status,
        container_env=(("COPILOT_CONFIG_DIR", "/root/.copilot"),),
        install_hint="see https://docs.github.com/en/copilot/github-copilot-in-the-cli",
    ),
    CliAgentDef(
        name="pi",
        binary="pi",
        label="Pi Coding Agent",
        # Pi auto-approves everything; "always" yolo means there's no flag
        # or env to set, the binary is YOLO by default.
        yolo=("always", ""),
        detect_status=cli_detect.detect_pi_status,
        container_env=(("PI_CODING_AGENT_DIR", "/root/.pi/agent"),),
        install_hint="npm install -g @mariozechner/pi-coding-agent",
    ),
    CliAgentDef(
        name="droid",
        binary="droid",
        label="Factory Droid",
        aliases=("factory-droid",),
        yolo=("flag", "--skip-permissions-unsafe"),
        detect_status=cli_detect.detect_droid_status,
        install_hint="npm install -g droid",
    ),
    CliAgentDef(
        name="settl",
        binary="settl",
        label="settl",
        aliases=("settlers", "catan"),
        yolo=("always", ""),
        detect_status=cli_detect.detect_settl_status,
        # host_only because settl has its own runtime that doesn't sandbox cleanly.
        host_only=True,
        install_hint="brew install --cask mozilla-ai/tap/settl",
    ),
)


def _load_override() -> dict[str, dict]:
    """Read the optional user override JSON. Silent on missing/invalid."""
    try:
        raw = json.loads(CLI_REGISTRY_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, spec in raw.items():
        if isinstance(name, str) and isinstance(spec, dict):
            out[name] = spec
    return out


def _coerce_def(name: str, spec: dict) -> CliAgentDef | None:
    """Build a CliAgentDef from a JSON dict. Returns None on bad shape."""
    binary = spec.get("binary")
    if not isinstance(binary, str) or not binary:
        return None
    aliases = spec.get("aliases") or ()
    if not isinstance(aliases, (list, tuple)):
        aliases = ()
    yolo = spec.get("yolo")
    if isinstance(yolo, list) and len(yolo) == 2:
        yolo = (str(yolo[0]), str(yolo[1]))
    else:
        yolo = None
    container_env = spec.get("container_env") or ()
    if isinstance(container_env, list):
        container_env = tuple(
            (str(k), str(v)) for k, v in container_env
            if isinstance((k, v), tuple) or (isinstance(k, str) and isinstance(v, str))
        )
    return CliAgentDef(
        name=name,
        binary=binary,
        label=str(spec.get("label", "")),
        aliases=tuple(str(a) for a in aliases),
        detection_arg=spec.get("detection_arg"),
        yolo=yolo,
        instruction_flag=spec.get("instruction_flag"),
        detect_status=None,  # overrides cannot ship code
        container_env=container_env,
        hooks=None,  # overrides skip hooks; install via the built-in entry
        host_only=bool(spec.get("host_only", False)),
        send_keys_enter_delay_ms=int(spec.get("send_keys_enter_delay_ms", 0) or 0),
        install_hint=str(spec.get("install_hint", "")),
        set_default_command=bool(spec.get("set_default_command", False)),
    )


def load_registry() -> tuple[CliAgentDef, ...]:
    """Return built-in entries plus any user overrides (override wins on name)."""
    by_name: dict[str, CliAgentDef] = {a.name: a for a in _BUILTIN_REGISTRY}
    for name, spec in _load_override().items():
        coerced = _coerce_def(name, spec)
        if coerced is not None:
            by_name[name] = coerced
    # Preserve built-in order; overrides slot in at their existing position
    # if they replace a built-in, else append in JSON-iteration order.
    ordered: list[CliAgentDef] = []
    seen: set[str] = set()
    for a in _BUILTIN_REGISTRY:
        ordered.append(by_name[a.name])
        seen.add(a.name)
    for name, a in by_name.items():
        if name not in seen:
            ordered.append(a)
    return tuple(ordered)


def find(name: str) -> CliAgentDef | None:
    """Look up an agent by canonical name or alias. Case-insensitive."""
    if not name:
        return None
    needle = name.strip().lower()
    if not needle:
        return None
    for agent in load_registry():
        if agent.name == needle:
            return agent
        for alias in agent.aliases:
            if alias.lower() == needle:
                return agent
    return None


def names() -> tuple[str, ...]:
    """Canonical names in registry order."""
    return tuple(a.name for a in load_registry())


def send_keys_enter_delay_ms(name: str) -> int:
    """Per-agent delay between literal text and submit-Enter.

    Non-zero for agents with paste-burst detection that swallows fast Enters
    (Codex's 120ms window in particular).
    """
    agent = find(name)
    return agent.send_keys_enter_delay_ms if agent else 0
