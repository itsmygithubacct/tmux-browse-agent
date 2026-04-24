"""Persistent per-agent execution logs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from lib import config
from .runs import LOG_SCHEMA_VERSION
from lib.errors import StateError


def log_path(name: str) -> Path:
    safe = (name or "").strip().lower() or "unknown"
    return config.AGENT_LOG_DIR / f"{safe}.jsonl"


def append_entry(name: str, payload: dict[str, Any]) -> Path:
    config.ensure_dirs()
    path = log_path(name)
    record = dict(payload)
    record.setdefault("ts", int(time.time()))
    record.setdefault("schema_version", LOG_SCHEMA_VERSION)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot write {path}: {e.strerror or e}")
    return path


def get_latest_entry(name: str) -> dict[str, Any] | None:
    """Read the most recent log entry without loading the entire file."""
    path = log_path(name)
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return None
            # Read a generous tail — most entries are well under 8 KB.
            chunk_size = min(size, 8192)
            fh.seek(-chunk_size, 2)
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                return row
    except OSError:
        pass
    return None


def read_entries(name: str, *, limit: int = 200) -> list[dict[str, Any]]:
    path = log_path(name)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise StateError(f"cannot read {path}: {e.strerror or e}")
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def render_text(name: str, *, limit: int = 200) -> str:
    rows = read_entries(name, limit=limit)
    if not rows:
        return f"(no agent log entries for {name})\n"
    out: list[str] = []
    for row in rows:
        ts = int(row.get("ts") or 0)
        origin = str(row.get("origin") or "-")
        status = str(row.get("status") or "-")
        prompt = str(row.get("prompt") or "").strip()
        message = str(row.get("message") or "").strip()
        out.append(f"[{ts}] agent={name} origin={origin} status={status}")
        if prompt:
            out.append(f"prompt: {prompt}")
        if message:
            out.append(f"message: {message}")
        transcript = row.get("transcript")
        if isinstance(transcript, list):
            for item in transcript:
                if not isinstance(item, dict):
                    continue
                step = item.get("step")
                if "action" in item:
                    out.append(f"step {step}: action={json.dumps(item['action'], ensure_ascii=True)}")
                if "parse_error" in item:
                    out.append(f"step {step}: parse_error={item['parse_error']}")
                tool_result = item.get("tool_result")
                if isinstance(tool_result, dict):
                    out.append(
                        f"step {step}: tool_result="
                        f"{json.dumps(tool_result, ensure_ascii=True)}",
                    )
        error = str(row.get("error") or "").strip()
        if error:
            out.append(f"error: {error}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
