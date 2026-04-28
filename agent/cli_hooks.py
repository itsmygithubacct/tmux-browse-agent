"""Install/uninstall settings.json status hooks for hook-aware CLI agents.

Some CLI agents (Claude Code, Cursor, Gemini) emit lifecycle events to a
``settings.json`` under their config dir. By registering a one-line shell
hook for the events we care about, we get authoritative status writes to
``/tmp/tba-hooks/<instance_id>/status`` instead of having to parse pane
content. The pane-parse detector still runs as a fallback for the brief
window before the first hook fires.

The installer is idempotent: re-running install replaces our previous
entries (identified by the ``tba-hooks`` marker substring). User-defined
hooks living next to ours are preserved. Uninstall reverses cleanly,
removing only entries with our marker.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import cli_registry


HOOK_MARKER = "tba-hooks"
HOOK_STATUS_DIR = "/tmp/tba-hooks"


def _hook_command(status: str) -> str:
    """Shell snippet that writes the status to the per-instance state file.

    The ``[ -n "$TBA_INSTANCE_ID" ] || exit 0`` guard means the hook is a
    no-op outside a tmux-browse-launched session, so users can leave the
    settings entries in place even when running the CLI standalone.
    """
    return (
        f'sh -c \'[ -n "$TBA_INSTANCE_ID" ] || exit 0; '
        f'mkdir -p {HOOK_STATUS_DIR}/$TBA_INSTANCE_ID && '
        f'printf {status} > {HOOK_STATUS_DIR}/$TBA_INSTANCE_ID/status\''
    )


def _is_our_command(cmd: str) -> bool:
    return isinstance(cmd, str) and HOOK_MARKER in cmd


def _build_event_entries(events: tuple[cli_registry.HookEvent, ...]) -> dict:
    """Translate registry HookEvents into the settings.json hooks shape.

    Output shape (matches Claude Code's hooks contract):
    ``{"PreToolUse": [{"matcher"?: "...", "hooks": [{"type": "command", "command": "..."}]}], ...}``
    """
    out: dict[str, list[dict]] = {}
    for event in events:
        if event.status is None:
            continue  # lifecycle-only entries can't write a status
        entry: dict = {
            "hooks": [{
                "type": "command",
                "command": _hook_command(event.status),
            }]
        }
        if event.matcher is not None:
            entry["matcher"] = event.matcher
        out.setdefault(event.name, []).append(entry)
    return out


def _strip_ours(matchers: list) -> list:
    """Filter a matcher array, keeping only groups that have at least one
    non-tba-hook command. A group whose every command is ours is dropped."""
    kept: list = []
    for matcher in matchers:
        if not isinstance(matcher, dict):
            kept.append(matcher)
            continue
        hooks_arr = matcher.get("hooks")
        if not isinstance(hooks_arr, list) or not hooks_arr:
            kept.append(matcher)
            continue
        if all(_is_our_command(h.get("command", "")) if isinstance(h, dict) else False
               for h in hooks_arr):
            continue  # whole group is ours; drop it
        kept.append(matcher)
    return kept


def _settings_path_for(cli: cli_registry.CliAgentDef) -> Path:
    if cli.hooks is None:
        raise ValueError(f"agent {cli.name!r} has no hooks config")
    return Path.home() / cli.hooks.settings_rel_path


def _atomic_write(path: Path, payload: dict) -> None:
    """Write JSON via tmp + os.replace at mode 0644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tba-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def install(name: str) -> dict:
    """Install our hook entries into the agent's settings.json. Idempotent.

    Returns ``{ok, settings_path, events_added}`` on success or
    ``{ok: False, error}`` on failure.
    """
    cli = cli_registry.find(name)
    if cli is None:
        return {"ok": False, "error": f"unknown CLI agent: {name!r}"}
    if cli.hooks is None:
        return {"ok": False, "error": f"{cli.name} has no hooks contract"}

    path = _settings_path_for(cli)
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    hooks_section = settings.get("hooks")
    if not isinstance(hooks_section, dict):
        hooks_section = {}

    new_entries = _build_event_entries(cli.hooks.events)
    for event_name, our_matchers in new_entries.items():
        existing = hooks_section.get(event_name)
        if isinstance(existing, list):
            cleaned = _strip_ours(existing)
            cleaned.extend(our_matchers)
            hooks_section[event_name] = cleaned
        else:
            hooks_section[event_name] = list(our_matchers)

    settings["hooks"] = hooks_section
    _atomic_write(path, settings)
    return {
        "ok": True,
        "settings_path": str(path),
        "events_added": sorted(new_entries.keys()),
    }


def uninstall(name: str) -> dict:
    """Strip our hook entries from the agent's settings.json.

    Returns ``{ok, settings_path, removed}`` where ``removed`` is True
    if the file was modified.
    """
    cli = cli_registry.find(name)
    if cli is None:
        return {"ok": False, "error": f"unknown CLI agent: {name!r}"}
    if cli.hooks is None:
        return {"ok": False, "error": f"{cli.name} has no hooks contract"}

    path = _settings_path_for(cli)
    if not path.exists():
        return {"ok": True, "settings_path": str(path), "removed": False}

    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "settings_path": str(path), "removed": False}
    if not isinstance(settings, dict):
        return {"ok": True, "settings_path": str(path), "removed": False}

    hooks_section = settings.get("hooks")
    if not isinstance(hooks_section, dict):
        return {"ok": True, "settings_path": str(path), "removed": False}

    modified = False
    for event_name in list(hooks_section.keys()):
        matchers = hooks_section.get(event_name)
        if not isinstance(matchers, list):
            continue
        before = len(matchers)
        cleaned = _strip_ours(matchers)
        if len(cleaned) != before:
            modified = True
        if cleaned:
            hooks_section[event_name] = cleaned
        else:
            del hooks_section[event_name]

    if not modified:
        return {"ok": True, "settings_path": str(path), "removed": False}

    if not hooks_section:
        settings.pop("hooks", None)
    else:
        settings["hooks"] = hooks_section

    _atomic_write(path, settings)
    return {"ok": True, "settings_path": str(path), "removed": True}


def is_installed(name: str) -> bool:
    """Return True if the named agent's settings.json has any of our entries."""
    cli = cli_registry.find(name)
    if cli is None or cli.hooks is None:
        return False
    path = _settings_path_for(cli)
    if not path.exists():
        return False
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    hooks_section = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks_section, dict):
        return False
    for matchers in hooks_section.values():
        if not isinstance(matchers, list):
            continue
        for m in matchers:
            if not isinstance(m, dict):
                continue
            for h in m.get("hooks", []) or []:
                if isinstance(h, dict) and _is_our_command(h.get("command", "")):
                    return True
    return False
