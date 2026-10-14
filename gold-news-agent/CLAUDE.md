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
uv run gold-agent verify-sources                     # one verification pass -> docs/source_report.md
uv run gold-agent verify-sources --watch-minutes 120 --note "VPS <region>"  # + source lag
uv run gold-agent run all                            # every service in one process (memory bus)
uv run gold-agent db upgrade                         # Alembic migrations (DATABASE_URL)
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

- **Phase 0 (done).** Scaffold, validated config, `verify-sources` (+report), prompt section C.
  The build container's network policy rejects every source host, so `docs/source_report.md`
  holds no real latency yet: re-run it on the production host. 2026-10-13: the user said
  "lanjutkan sampai jadi" without answering Q1–Q13, so the recommended defaults in
  `settings.yaml` stand (intraday, Binance PAXG proxy, 0.5% risk, 3% daily loss, Linux VPS,
  quiet 00:00–05:00 WIB, no news trading). Changing an answer = editing config, not code.
- **Phase 1 (done).** Models (`models/`), bus (`core/bus.py`: MemoryBus + Redis Streams with
  consumer groups, XAUTOCLAIM, dead-letter), storage (`storage/`: SQLAlchemy async, Alembic
  `0001`, verified on SQLite + PostgreSQL 16), collectors (RSS/JSON poller with ETag/304,
  Retry-After, jittered backoff, backfill marking; Telethon push collector incl. edits),
  normaliser, dedup/cluster (hash, rapidfuzz, number guard, optional embeddings), archive,
  latency stages + p50/p95 summary, `gold-agent run collector`. Integration tests run when
  `GOLD_TEST_POSTGRES_URL` / `GOLD_TEST_REDIS_URL` are set.
- **Phase 2 (done).** `timeutils/sessions.py` (sessions, trading day 17:00 NY, weekly open/close,
  daily break, US/UK holidays) + `risky.py`; DST tests cover both one-country-switched windows.
  Market: candle builder (H4/D1 anchored to 17:00 NY), numpy indicators (EMA/RSI/ATR Wilder,
  BB, VWAP→TWAP fallback, pivots, swings), levels, USD tracker (synthetic DXY or EURUSDT proxy),
  FRED yields, adapters (Binance PAXG/EURUSDT, Twelve Data, MT5 with server-offset detection,
  MT5 bridge over Redis, failover), market service (context every 5 s, stale-price alerts,
  weekend PAXG drift, closed-market bars dropped). Calendar: `config/events.yaml` specs (prior
  surprise_std), Forex Factory sync (ETag, 429 back-off, actuals preserved), FOMC page parser +
  blackout window, countdown. Services: `collector`, `market`, `calendar`.
- **Phase 3 (done, pending real data).** Prefilter (`keywords.yaml`), fast-path release parser
  (18 US events, country guard, calendar forecast fallback, prior vs calibrated surprise s.d.),
  playbooks (11.4 buckets → actual thresholds), Claude client (`analysis/llm_client.py`:
  `messages.parse` structured output, cached system prompt + KB, Haiku with thinking off,
  timeouts, circuit breaker, WIB-day budget, `llm_usage` cost log, refusal handling), rules
  fallback, anti-hallucination checks (ids, quotes, numbers), cross-verification weights, deep
  analysis (Sonnet), price-led spikes → alert + targeted `collector.poll_now`, analyzer service.
  `gold-agent labels export/import` + `gold-agent eval-triage [--llm]` measure the Phase 3 bar
  (precision ≥ 0.9 on ≥ 100 labelled headlines, p95 < 5 s) — needs the real archive to run.
- Phases 4–7 in progress. Phase 8 (order execution) is NOT built: it needs explicit approval
  after ≥ 2–4 weeks of recorded paper trading.
