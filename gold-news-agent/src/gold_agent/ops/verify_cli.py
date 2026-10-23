"""Verify every source in config/sources.yaml and write docs/source_report.{md,json}.

Usage (either entry point):
    uv run gold-agent verify-sources                              # one pass, all enabled sources
    uv run gold-agent verify-sources --watch-minutes 120          # also measure source lag
    uv run gold-agent verify-sources --only fed_press_all,bls_latest
    uv run gold-agent verify-sources --record data/recordings     # keep raw bodies for fixtures
    uv run python scripts/verify_sources.py ...                   # same thing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from pathlib import Path

from gold_agent.config import PROJECT_ROOT, Secrets, load_settings, load_sources
from gold_agent.ops.source_check import Redactor, run_checks, utc_now
from gold_agent.ops.source_report import RunMeta, render_markdown


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--only", help="comma-separated source ids")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--watch-minutes", type=float, default=0.0)
    parser.add_argument("--record", type=Path, help="directory for raw response bodies")
    parser.add_argument("--note", help="free-text note about the measuring host, shown in report")
    parser.add_argument("--out-md", type=Path, default=PROJECT_ROOT / "docs" / "source_report.md")
    parser.add_argument(
        "--out-json", type=Path, default=PROJECT_ROOT / "docs" / "source_report.json"
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the verifier; exit code 1 when any source failed."""
    args = _parse_args(argv)
    settings = load_settings()
    registry = load_sources()
    env = Secrets().env_values()

    sources = [s for s in registry.sources if s.enabled or args.include_disabled]
    if args.only:
        wanted = {part.strip() for part in args.only.split(",")}
        unknown = wanted - {s.id for s in registry.sources}
        if unknown:
            print(f"unknown source ids: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        sources = [s for s in registry.sources if s.id in wanted]

    started = utc_now()
    results = asyncio.run(
        run_checks(
            sources,
            env,
            user_agent=settings.http.full_user_agent(),
            timeout_seconds=settings.http.timeout_seconds,
            min_poll_seconds=settings.http.min_poll_seconds,
            watch_minutes=args.watch_minutes,
            record_dir=args.record,
        )
    )
    meta = RunMeta(
        started_at=started,
        finished_at=utc_now(),
        display_tz=settings.time.display_tz,
        python=platform.python_version(),
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        user_agent=settings.http.full_user_agent(),
        watch_minutes=args.watch_minutes,
        environment_note=args.note,
    )

    redact = Redactor(env.values())
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(redact(render_markdown(results, meta)), encoding="utf-8")
    payload = {
        "meta": meta.model_dump(mode="json"),
        "results": [r.model_dump(mode="json") for r in results],
    }
    args.out_json.write_text(redact(json.dumps(payload, indent=2)) + "\n", encoding="utf-8")

    for r in results:
        print(f"{r.status:8} {r.source_id:28} {redact(r.reason)}")
    print(f"\nreport: {args.out_md}")
    return 1 if any(r.status == "fail" for r in results) else 0
