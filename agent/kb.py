"""Per-agent knowledge base: small text files preloaded into the system
prompt at every turn.

Files live under ``~/.tmux-browse/agent-kb/<agent>/*``. The renderer
concatenates them into one ``## Knowledge base`` section, capped at
128 KiB total — files over the cap are skipped with a warning in the
rendered block.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from lib import config

TOTAL_BYTES_CAP = 128 * 1024


def _dir_for(agent_name: str) -> Path:
    return config.AGENT_KB_DIR / agent_name


def list_files(agent_name: str) -> list[dict]:
    path = _dir_for(agent_name)
    if not path.is_dir():
        return []
    rows: list[dict] = []
    for f in sorted(path.iterdir()):
        if not f.is_file():
            continue
        try:
            rows.append({"name": f.name, "size": f.stat().st_size})
        except OSError:
            continue
    return rows


def add_file(agent_name: str, source_path: str) -> dict:
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"not a file: {source_path}")
    # Enforce per-file size so one file can't eat the cap on its own.
    size = src.stat().st_size
    if size > TOTAL_BYTES_CAP:
        raise ValueError(
            f"{src.name} is {size} bytes; per-file cap is {TOTAL_BYTES_CAP}")
    dst_dir = _dir_for(agent_name)
    dst_dir.mkdir(parents=True, exist_ok=True)
    # Refuse if adding this would push the total over the cap.
    current = sum(row["size"] for row in list_files(agent_name))
    if current + size > TOTAL_BYTES_CAP:
        raise ValueError(
            f"adding {src.name} ({size} bytes) would exceed KB cap "
            f"of {TOTAL_BYTES_CAP}; current total is {current}")
    dst = dst_dir / src.name
    shutil.copyfile(src, dst)
    return {"name": dst.name, "size": size}


def remove_file(agent_name: str, filename: str) -> bool:
    dst_dir = _dir_for(agent_name)
    target = dst_dir / filename
    if not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def render_block(agent_name: str) -> str:
    """Return a ``## Knowledge base`` section string, or empty if none."""
    path = _dir_for(agent_name)
    if not path.is_dir():
        return ""
    parts: list[str] = []
    used = 0
    skipped: list[str] = []
    for f in sorted(path.iterdir()):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if used + size > TOTAL_BYTES_CAP:
            skipped.append(f.name)
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts.append(f"### {f.name}\n{content}")
        used += size
    if not parts:
        return ""
    body = "\n\n".join(parts)
    out = "\n---\n\n## Knowledge base\n\n" + body + "\n"
    if skipped:
        out += "\n*(Skipped over KB cap: " + ", ".join(skipped) + ")*\n"
    return out
