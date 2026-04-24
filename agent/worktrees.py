"""Git worktree helpers for isolated agent task execution.

Worktrees are created under ``~/.tmux-browse/worktrees/<slug>/`` so
they stay out of the source repo.  Each worktree gets its own branch
(``tb-task/<slug>``) unless an existing branch is specified.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from lib import config
from lib.errors import StateError, UsageError


WORKTREE_DIR = config.STATE_DIR / "worktrees"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:60] or "task"


def _run_git(args: list[str], *, cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        capture_output=True, text=True, cwd=str(cwd), timeout=30,
    )


def create(repo_path: str | Path, slug: str, *,
           branch: str | None = None) -> dict:
    """Create a worktree for *repo_path* at ``WORKTREE_DIR/<slug>``.

    Returns ``{"path": str, "branch": str, "created": bool}``.
    If the worktree already exists, returns it without error.
    """
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        raise UsageError(f"{repo} is not a git repository")

    safe_slug = _slugify(slug)
    wt_path = WORKTREE_DIR / safe_slug
    branch_name = branch or f"tb-task/{safe_slug}"

    if wt_path.exists():
        return {"path": str(wt_path), "branch": branch_name, "created": False}

    WORKTREE_DIR.mkdir(parents=True, exist_ok=True)

    # Create branch if it doesn't exist.
    check = _run_git(["rev-parse", "--verify", branch_name], cwd=repo)
    if check.returncode != 0:
        r = _run_git(["branch", branch_name], cwd=repo)
        if r.returncode != 0:
            raise StateError(f"git branch failed: {r.stderr.strip()}")

    r = _run_git(["worktree", "add", str(wt_path), branch_name], cwd=repo)
    if r.returncode != 0:
        raise StateError(f"git worktree add failed: {r.stderr.strip()}")

    return {"path": str(wt_path), "branch": branch_name, "created": True}


def list_worktrees(repo_path: str | Path) -> list[dict]:
    """List git worktrees for *repo_path*."""
    repo = Path(repo_path).resolve()
    r = _run_git(["worktree", "list", "--porcelain"], cwd=repo)
    if r.returncode != 0:
        return []
    entries: list[dict] = []
    current: dict = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line == "bare":
            current["bare"] = True
    if current:
        entries.append(current)
    return entries


def remove(repo_path: str | Path, slug: str, *, force: bool = False) -> bool:
    """Remove a worktree by slug. Returns True if removed."""
    repo = Path(repo_path).resolve()
    safe_slug = _slugify(slug)
    wt_path = WORKTREE_DIR / safe_slug
    if not wt_path.exists():
        return False
    args = ["worktree", "remove", str(wt_path)]
    if force:
        args.append("--force")
    r = _run_git(args, cwd=repo)
    if r.returncode != 0:
        raise StateError(f"git worktree remove failed: {r.stderr.strip()}")
    return True
