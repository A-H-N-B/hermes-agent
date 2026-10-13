# CLAUDE.md — Gold News Agent

Project memory for Claude Code sessions. Spec: `PROMPT_XAUUSD_NEWS_AGENT.md` (sections A–B are the
original brief, section C holds the Phase 0 corrections and additions, and C wins when they differ).
Decisions and their reasons: `docs/architecture.md`.

## What this is

A 24/7 XAUUSD news monitor and decision-support system: collectors → dedup → prefilter → fast path /
LLM triage → signal engine → Telegram + dashboard, with paper-trading evaluation. Default mode is
`SIGNAL_ONLY`. Order execution does not exist until Phase 8 is explicitly approved;
`execution_enabled` is typed `Literal[False]` so config cannot switch it on.

## Commands

```bash
uv sync                                              # create/refresh .venv (Python 3.12)
uv run pytest -q                                     # tests
uv run ruff check . && uv run ruff format --check .  # lint/format
uv run pyright                                       # strict type check
uv run python scripts/verify_sources.py              # one verification pass -> docs/source_report.md
uv run python scripts/verify_sources.py --watch-minutes 120 --note "VPS <region>"  # + source lag
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Conventions

- Code, identifiers and technical comments in English. Everything a user reads (Telegram, dashboard,
  signal reasons, reports, README) in Bahasa Indonesia.
- Store UTC, display WIB (`Asia/Jakarta`). Session times come from `zoneinfo` per date, never fixed
  offsets. MT5 bar times are broker-server time and must be converted.
- Behaviour lives in `config/*.yaml`, validated by `src/gold_agent/config.py` (`extra="forbid"`).
  `.env` holds secrets only. New knobs get a model field and a validation test.
- Collectors only use sources with `enabled: true` and `status: verified`
  (`SourceRegistry.production()`). A URL becomes `verified` only after `verify_sources.py` plus a
  human ToS check.
- No invented data on production paths. When a source is down the output says
  "data tidak tersedia". Test doubles live in `tests/` only.
- Secrets never reach logs, reports or commits: use `ops.source_check.Redactor` on anything that
  could contain a URL or error text. Telethon `*.session` files are secrets too.
- Tests are behaviour contracts (no change-detector tests). Real recorded bodies
  (`--record data/recordings`) become fixtures once sources are reachable; recordings of third-party
  content stay out of public repos.

## Phase log

- **Phase 0 (in progress, awaiting approval).** Done: repo scaffold (uv, ruff, pyright strict,
  pytest, pre-commit with gitleaks), validated config (`settings.yaml`, `sources.yaml` with 31
  candidates, `keywords.yaml`, `gold_drivers.yaml`, `fed_officials.yaml` structure),
  `scripts/verify_sources.py` (+34 tests), section C of the prompt, architecture decisions.
  Blocked: the build container's network policy rejects every source host, so
  `docs/source_report.md` has no real latency yet. It has to be re-run on the production host
  (or after the cloud environment allows those domains). Open questions: Q1–Q13 (prompt §3 + §C6).
- Phase 1+ not started. Stop for approval at the end of Phase 0 and before Phase 8.
