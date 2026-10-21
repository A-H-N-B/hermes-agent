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
uv run pytest -q -m slow                             # + whole-day replay E2E (~2 min)
uv run ruff check . && uv run ruff format --check .  # lint/format
uv run pyright                                       # strict type check
uv run gold-agent verify-sources                     # one verification pass -> docs/source_report.md
uv run gold-agent verify-sources --watch-minutes 120 --note "VPS <region>"  # + source lag
uv run gold-agent run all                            # every service in one process (memory bus)
uv run gold-agent replay 2026-10-13 --from 19:00 --to 22:00  # deterministic replay of a day
docker compose build && docker compose up -d         # production stack (docs/runbook.md)
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
  `beta.messages.parse` structured output, cached system prompt + KB, every route on Opus 5.5
  (thinking always on, `effort` per route, server-side refusal `fallbacks: default` priced per
  `usage.iterations`), timeouts, circuit breaker, WIB-day budget, `llm_usage` cost log, refusal
  handling), rules fallback, anti-hallucination checks (ids, quotes, numbers), cross-verification
  weights, deep analysis (Opus 5.5, effort high), price-led spikes → alert + targeted
  `collector.poll_now`, analyzer service.
  `gold-agent labels export/import` + `gold-agent eval-triage [--llm]` measure the Phase 3 bar
  (precision ≥ 0.9 on ≥ 100 labelled headlines, p95 < 5 s) — needs the real archive to run.
- **Phase 4 (done).** `signals/scoring.py` (N/M/T/P, decay, priced-in, novelty, confirmed-only
  composite whose price check is anchored on confirmed news), `levels.py` (SL beyond swing or
  1.2 ATR, TP 1R/2R/structure, lots only with equity + broker contract size), `engine.py` (WAIT
  filters, event blackout + post-release confirm, hysteresis, cooldown, daily loss/count limits,
  kill switch, full 10.5 schema, `EngineState` in kv `signal_engine`), `outlook.py` (24 WIB rows,
  TUNGGU DATA + playbook thresholds on release hours, conditional rows > 4 h, confidence decay,
  Monday-open notes with PAXG gap estimate, optional LLM narrative that never edits the table),
  `profile.py` (hourly_profile per WIB / London / New York hour so DST does not smear buckets;
  rebuilt weekly from stored H1). Calendar service now builds playbooks at T-60 and publishes them
  at T-30 (`calendar.playbooks`). `services/signal.py` publishes `signals`, `signals.state`,
  `signals.outlook`; `/stop` = `{"type": "signal.halt"}` on `app.events`, persisted in kv
  `trading_halt`; the paper tracker reports `signal.closed` and `paper.daily`.
- **Phase 5 (done).** `notify/`: Telegram HTML templates (signal per spec, flash, news, alerts,
  outlook, playbook, status, calendar, stats, digest; untrusted text always escaped, 4096-char
  split on line boundaries), `policy.py` (what notifies at which priority, max age, dedupe
  windows), `dispatcher.py` (quiet hours, /mute, dedupe, rate limit + merge, digest, retry after
  a Telegram failure even in quiet hours for 🔴, notified latency; every decision persisted in
  `notifications`), `commands.py` (admin allowlist + chat check, /stop writes kv directly),
  `telegram.py` (PTB 22: RetryAfter/TimedOut/BadRequest handling). `evaluation/performance.py`
  (Wilson CI, bootstrap expectancy, drawdown, per category/session/hour/setup/confidence bin,
  "sampel belum cukup"). `dashboard/`: FastAPI JSON APIs + WebSocket hub, plain-JS SPA under a
  strict CSP (ADR-014), vendored lightweight-charts 5.2.1, HMAC cookie login, refuses
  non-loopback bind without a password. Services `notifier`, `dashboard`. Playwright E2E test.
- **Phase 6 (done, pending real data).** `evaluation/paper.py` (one trade-management
  implementation shared by live paper and backtest: ask/bid + slippage fills, stop before
  target, 50% at TP1 then breakeven, validity before TP1, max hold after), `services/paper.py`
  (fills on the next quote from the signal's own source, TP1/close notifications,
  `signal.closed` + `paper.daily` back to the engine, restart catch-up from stored ticks,
  unfilled signals recorded with r = None). `event_study.py` (+1/+5/+15/+60 from first_seen,
  Wilson CI, typical release moves -> kv `event_moves` for the outlook), `reliability.py`
  (shrunk towards the prior), `calibration.py` (Spearman IC with tie-aware ranks, time-ordered
  holdout, refuses < 100 closed signals, output is a proposal only), `backtest.py` (replay of
  stored contexts + **analysis versions by `version_at`**: new append-only table
  `analysis_versions`, migration 0002, so later confirmations never leak backwards;
  expanding-window walk-forward), `reports.py` (daily/weekly, honest notes, optional brief LLM
  on sanitized input). `core/schedule.py` (restart-safe wall-clock jobs). Services `paper`,
  `evaluator`; CLI `report`, `event-study`, `backtest`, `calibrate` (`cli_eval.py`). A schema
  test now diffs Alembic head against the models on SQLite and PostgreSQL. First report
  produced on the empty archive ("sampel belum cukup"): weights stay at their configured priors
  until ≥ 100 closed paper signals exist (C3.2 takes precedence over "bobot awal dikalibrasi").
- **Phase 7 (done in code; the 72 h burn-in needs the VPS).** `ops/watchdog.py` +
  `services/watchdog.py` (alerts on state transitions: source down/silent, heartbeat stale, bus
  backlog, clock offset from chrony or a host-written offset file that goes "unknown" after
  10 min, low disk; nightly retention 04:15 WIB). MT5 bridge (`mt5-bridge` on Windows → Redis;
  `market/bridge_publisher.py`). Deterministic replay (`ops/replay.py`, `ReplayClock`,
  ADR-017): identical results across runs and speeds; periodic loops use `Clock.wait` and wait
  one period first. Deploy (ADR-016): `Dockerfile` (non-root, editable install so config/ and
  prompts/ resolve), `docker-compose.yml` (one container per service, healthchecks on
  heartbeats, only the dashboard published on host loopback via `run --dashboard-host 0.0.0.0`
  + required password), `docker-compose.mt5-bridge.yml`, `scripts/backup.sh` /
  `restore_check.sh` / `clock_offset.sh` (tested against PostgreSQL 16), `deploy/systemd/`.
  `docs/runbook.md` (Indonesian): VPS choice from data, install, alert table, fire drill,
  backup/restore, burn-in checklist. Tests: fire drill (`test_fire_drill.py`), deploy invariants
  (`test_deploy.py`), replay clock and replay E2E. Dropped unused pandas/apscheduler; vectorised
  Bollinger; multi-row candle upserts. Single-computer install (`docs/pasang_di_komputer.md`,
  Windows first): SQLite by default (empty `DATABASE_URL`), clock offset via one SNTP query to
  `watchdog.ntp_server` at most every 15 min when there is no chrony, optional rotating
  `logging.file`, `gold-agent db backup` (SQLite online backup), dashboard port bound
  exclusively on Windows. `scripts/install_windows.ps1` (`irm | iex`) does the whole Windows
  install in one command. Dashboard page **TradingView** (official embed widget,
  `dashboard.tradingview_symbol`, default `OANDA:XAUUSD`; CSP admits only its script + frame
  hosts, `null` turns it off; ADR-014). TradingView indicator `dashboard/static/gold_news_agent.pine`
  (Pine v6, draws only): Pine cannot fetch external data, so each signal carries a one-line code
  (`signals/tvcode.py`, `GNA1|...` 13 fields, levels shifted by chart-minus-reference price) shown
  in Telegram and on the Live card; user guide `docs/tradingview_indikator.md`. The TradingView
  page also draws the indicator itself (Lightweight Charts: signal price lines, session bands,
  08:30 NY marks), a "Rencana trading" table, and a 24 h forecast ahead of the last candle
  (outlook bias × confidence × expected range; heads to TP1 while a signal is active).
  Live page: WIB clock, "Kapan klik BUY/SELL" (`dashboard/timing.py`: now/blackout/next outlook
  windows/level triggers, browser-side countdowns), overlays + per-TF read
  (`dashboard/indicators_view.py`: EMA20/50/200, BB, RSI pane, VWAP, levels; the read never
  makes a signal). APIs `/api/timing`, `/api/indicators`. Stoch(5,3,3) + Parabolic SAR(0.02,
  0.2) in `market/indicators.py` (chart + per-TF votes). Claude credit (`dashboard/credit.py`,
  `/api/credit`, kv `llm_credit`): no API returns a prepaid balance for a normal key, so the user
  enters it once and `llm_usage` costs since then are subtracted; the POST is same-origin JSON only.
  MT5-style timeframe ring on the Live chart (M1..MN; W1/MN aggregated from D1 by NY trading
  date) with a next-candle forecast (`dashboard/forecast.py`, `/api/forecast`): 0.6 × engine score
  (active signal = ±1) + 0.4 × that TF's indicator vote, range = TF ATR14, TUNGGU DATA when a
  high-impact release falls inside the horizon. Not a calibrated probability. Live indicators
  start unticked (created on tick, removed with their pane on untick; choice kept in
  localStorage); the board shows a SELL↔BUY gauge of the engine score vs entry_threshold and
  refreshes every 30 s; a mirrored-market test pins BUY/SELL symmetry of the forecast.
  Trading windows (`trading.windows`, `timeutils/windows.py`; shipped: London M5 14:00–17:00 and
  New York M15 19:00–23:00 WIB, zone-aware so Europe/London times would follow DST; tests strip
  them in the `settings` fixture and opt in): outside every window the engine WAITs ("di luar jam
  trading", next window named); inside, levels use that TF's ATR14 + nearest fractal swing
  (`TimeframeState.swing_high/low`, context now carries M1..D1), validity = `validity_bars` (6)
  candles, and the entry is timed on the TF (price back past its EMA20, at most
  `max_chase_atr` ATR beyond it). Signals carry `timeframe` / `trading_window` (Telegram line).
  The timing board shows the open/next window and offers only outlook hours inside windows; the
  Live chart follows the window TF until the user picks one for that window. Forecast accuracy
  (`dashboard/forecast_score.py`): walk-forward over stored bars + `signal_states` scores
  (`SignalRepo.scores_between`), next-close hit rate with a Wilson 95% interval, news-only and
  chart-only on the same bars, NETRAL/TUNGGU DATA abstain, bars without an engine score
  skipped; served inside `/api/forecast` (cached 5 min). The forecast's chart state is the market
  service's own `timeframe_state` (one trend rule, `market/context.py:trend_of`).
  This computer's settings live in git-ignored `config/local.yaml` (deep-merged over
  settings.yaml) and `config/sources.local.yaml` (per-id fields); `gold_agent/local_config.py`
  is stdlib+PyYAML only so the Windows updater (`scripts/windows/perbarui.ps1`, desktop shortcut
  "Perbarui") can run the NEW revision's `migrate` before switching: edits to the tracked files
  move to the local ones (full diff kept in data/backups), then `merge --ff-only`, `uv sync
  --extra mt5`, config check, restart. `gold-agent config set|verified|migrate` (validated,
  rolled back on a bad value); the installer writes through it; tests load with `local=False`.
  `price_feed.primary: auto` (default) = MT5 when the MetaTrader5 package is installed (Windows)
  then Binance PAXG; `mt5_symbol: null` detects the broker's spot-gold symbol
  (`pick_gold_symbol`: Market Watch first, XAUUSD before GOLD, other quote currencies excluded).
  A feed switch (PAXG <-> MT5) resets the candle builder and reloads history from the feed now
  producing ticks (`FailoverAdapter.history` asks the active feed first), so two instruments'
  bars are never mixed.
- Phase 8 (order execution) is NOT built: it needs explicit approval after ≥ 2–4 weeks of
  recorded paper trading.
