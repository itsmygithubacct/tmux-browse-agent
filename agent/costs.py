"""Per-run token/cost tracking.

Appends a cost record after each agent run that includes usage data
from the provider.  Supports querying by agent and time range, and
computing per-agent and daily totals.

Storage: ``~/.tmux-browse/agent-costs.jsonl`` (append-only).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from lib import config
from lib.errors import StateError


COSTS_FILE = config.STATE_DIR / "agent-costs.jsonl"


def record(*, run_id: str, agent: str, model: str,
           usage: dict[str, Any], origin: str = "") -> None:
    """Append a cost record if usage contains token counts."""
    if not usage:
        return
    config.ensure_dirs()
    row = {
        "ts": int(time.time()),
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "origin": origin,
        "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    # Compute total if provider didn't supply it.
    if not row["total_tokens"]:
        row["total_tokens"] = row["prompt_tokens"] + row["completion_tokens"]
    try:
        with COSTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError as e:
        raise StateError(f"cannot write {COSTS_FILE}: {e.strerror or e}")


def query(*, agent: str | None = None,
          since: int | None = None,
          until: int | None = None,
          limit: int = 500) -> list[dict[str, Any]]:
    """Read cost records with optional filters."""
    if not COSTS_FILE.exists():
        return []
    try:
        lines = COSTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise StateError(f"cannot read {COSTS_FILE}: {e.strerror or e}")
    results: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(results) >= limit:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if agent and row.get("agent") != agent:
            continue
        ts = int(row.get("ts") or 0)
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        results.append(row)
    return results


def per_agent_totals(*, since: int | None = None,
                     until: int | None = None) -> dict[str, dict[str, int]]:
    """Return ``{agent: {prompt_tokens, completion_tokens, total_tokens, runs}}``."""
    rows = query(since=since, until=until, limit=10000)
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "runs": 0},
    )
    for row in rows:
        name = row.get("agent", "unknown")
        totals[name]["prompt_tokens"] += int(row.get("prompt_tokens", 0))
        totals[name]["completion_tokens"] += int(row.get("completion_tokens", 0))
        totals[name]["total_tokens"] += int(row.get("total_tokens", 0))
        totals[name]["runs"] += 1
    return dict(totals)


def daily_totals(*, since: int | None = None,
                 until: int | None = None) -> dict[str, dict[str, int]]:
    """Return ``{YYYY-MM-DD: {prompt_tokens, completion_tokens, total_tokens, runs}}``."""
    rows = query(since=since, until=until, limit=10000)
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "runs": 0},
    )
    for row in rows:
        ts = int(row.get("ts") or 0)
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        totals[day]["prompt_tokens"] += int(row.get("prompt_tokens", 0))
        totals[day]["completion_tokens"] += int(row.get("completion_tokens", 0))
        totals[day]["total_tokens"] += int(row.get("total_tokens", 0))
        totals[day]["runs"] += 1
    return dict(totals)
