"""System clock offset from chrony (C4.1). Latency numbers are only as good as the clock.

Natively the watchdog asks `chronyc` itself. In Docker the container has no chrony, so a host
timer writes the offset in seconds to a file (scripts/clock_offset.sh) that is mounted in.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

OFFSET_FILE_MAX_AGE = 600.0  # seconds; the host timer writes every minute


def parse_tracking(csv_line: str) -> float | None:
    """`chronyc -c tracking`: field 5 is the system time offset in seconds."""
    fields = csv_line.strip().split(",")
    if len(fields) < 5:
        return None
    try:
        return float(fields[4])
    except ValueError:
        return None


async def chrony_offset(executable: str | None = None) -> float | None:
    chronyc = executable or shutil.which("chronyc")
    if chronyc is None:
        return None
    process = await asyncio.create_subprocess_exec(
        chronyc,
        "-c",
        "tracking",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        process.kill()
        return None
    return parse_tracking(stdout.decode(errors="replace")) if process.returncode == 0 else None


def file_offset(path: Path, *, max_age_seconds: float = OFFSET_FILE_MAX_AGE) -> float | None:
    """The offset the host timer wrote, unless it stopped writing: then nobody can tell."""
    try:
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


async def clock_offset(offset_file: str | None) -> float | None:
    """Offset in seconds (positive = our clock is ahead), or None when nobody can tell."""
    if offset_file:
        value = await asyncio.to_thread(file_offset, Path(offset_file))
        if value is not None:
            return value
    return await chrony_offset()
