"""Which revision of the program is running, read from the git checkout it runs from.

Shown in the dashboard header so the user (and whoever helps them) can tell at a glance whether
an update reached the running program. Read from the files under .git, no git executable needed.
"""

from __future__ import annotations

from pathlib import Path

UNKNOWN = "tidak diketahui"


def _git_dir(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        candidate = folder / ".git"
        if candidate.is_dir():
            return candidate
    return None


def _resolve(git: Path, ref: str) -> str | None:
    loose = git / ref
    if loose.is_file():
        return loose.read_text(encoding="ascii", errors="replace").strip()
    packed = git / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="ascii", errors="replace").splitlines():
            sha, _, name = line.partition(" ")
            if name.strip() == ref:
                return sha
    return None


def running_commit(start: Path | None = None) -> str:
    """The short commit hash of the checkout holding this package, or UNKNOWN."""
    git = _git_dir(start or Path(__file__).resolve().parent)
    if git is None:
        return UNKNOWN
    try:
        head = (git / "HEAD").read_text(encoding="ascii", errors="replace").strip()
        sha = _resolve(git, head[5:].strip()) if head.startswith("ref:") else head
    except OSError:
        return UNKNOWN
    return sha[:8] if sha and len(sha) >= 8 else UNKNOWN
