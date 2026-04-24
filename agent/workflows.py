"""Persistent workflow schedules for agent conversation sessions."""

from __future__ import annotations

import json
from typing import Any

from lib import config
from lib.errors import StateError


DEFAULTS: dict[str, Any] = {"agents": {}}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def normalize(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    agents = raw.get("agents")
    out_agents: dict[str, Any] = {}
    if isinstance(agents, dict):
        for name, spec in agents.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            rows = spec.get("workflows")
            normalized_rows: list[dict[str, Any]] = []
            if isinstance(rows, list):
                for row in rows[:16]:
                    if not isinstance(row, dict):
                        continue
                    normalized_rows.append({
                        "name": str(row.get("name") or "").strip()[:80],
                        "prompt": str(row.get("prompt") or "").strip(),
                        "interval_seconds": _coerce_int(row.get("interval_seconds"), 300, 5, 86400),
                    })
            out_agents[name.strip().lower()] = {
                "enabled": _coerce_bool(spec.get("enabled"), False),
                "workflows": normalized_rows,
            }
    return {"agents": out_agents}


def load() -> dict[str, Any]:
    config.ensure_dirs()
    try:
        raw = json.loads(config.AGENT_WORKFLOWS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)
    return normalize(raw)


def save(raw: Any) -> dict[str, Any]:
    config.ensure_dirs()
    normalized = normalize(raw)
    try:
        config.AGENT_WORKFLOWS_FILE.write_text(
            json.dumps(normalized, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        raise StateError(f"cannot write {config.AGENT_WORKFLOWS_FILE}: {e.strerror or e}")
    return normalized
