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
uv run pytest -q -m slow                             # + whole-day replay E2E (~14 min)
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
  date) with a next-candle forecast (`signals/forecast.py`, `/api/forecast`): 0.6 × engine score
  (active signal = ±1) + 0.4 × that TF's indicator vote, range = TF ATR14, TUNGGU DATA when a
  high-impact release falls inside the horizon. Not a calibrated probability. Live indicators
  start unticked (created on tick, removed with their pane on untick; choice kept in
  localStorage); the board shows a SELL↔BUY gauge of the engine score vs entry_threshold and
  refreshes on every context push (every second since 26 Oct; 30 s fallback without the
  WebSocket); a mirrored-market test pins BUY/SELL symmetry of the forecast.
  Automatic timeframe (`trading.timeframe`, `signals/timeframe.py`): every evaluation picks the
  chart a trade is sized, timed and valid on from M1..H1: usable = EMA20 + ATR14 present and
  spread / (1.2 ATR) <= `max_cost_ratio`; the usable one with the best walk-forward accuracy in
  the current session wins when proven (>= MIN_CALLS calls, Wilson lower bound > 0.5, hysteresis
  0.02), else `session_prior` (Asia/London M5, overlap/New York M15), else the nearest usable.
  Signals carry `timeframe` / `timeframe_reason`; levels use that TF's ATR14 + nearest fractal
  swing (`TimeframeState.swing_high/low`; context carries M1..D1 with Stoch/SAR); validity =
  `validity_bars` (6) candles; entry timing (`engine.entry_timing`: back past EMA20, at most
  `max_chase_atr` ATR beyond). Accuracy: `signals/forecast_score.py` (walk-forward over stored
  bars + `signal_states` scores via `SignalRepo.scores_between`, Wilson 95%, news-only and
  chart-only on the same bars, by session) loaded by `signals/accuracy.py` (cached 1 h, numeric
  part in a thread); the forecast and the per-TF vote moved to `signals/forecast.py` and
  `market/reads.py`. Opus decision desk (`signals/desk.py`, route `llm.desk`, effort low since
  26 Oct, prompt `prompts/desk_system.md` + driver KB): the engine's candidate goes to Opus in the
  background (state WAIT "menunggu keputusan AI"); the next evaluation applies the verdict on
  fresh prices. Guardrails: approve or WAIT only (a flip is held as WAIT), timeframe must be
  usable (else the selector's), levels always the engine's (re-evaluated on Opus's chart). A
  WAIT stands 3 min unless |S| moves 0.10 or the side or setup type changes; approvals live
  2 min; at most `llm.desk_reviews_per_day`; no AI (disabled, outage, cap) = engine alone with
  `SignalReview(model=None, note=...)`. Reviews logged in kv `desk_reviews`; Performa compares
  approved vs held-back candidates by first touch (TP1 vs SL) on M1 bars.
  Each review is `llm.desk_votes` (3) independent answers in parallel, counted by `desk.tally`
  (a strict majority approves, a tie holds; chart = the approving answers' most common pick,
  ties to the selector's; `SignalReview.votes/agree/dissent`). The brief carries triage
  confidence and each story's deep analysis (direction, confidence, narrative, risks).
  AI budget: `budget.llm_daily_usd` (20) is a hard ceiling per WIB day. `LlmClient._admit`
  holds each call's worst case (`worst_case_usd`) as an `llm_usage` row before reading the
  day's total (holds included, so concurrent calls in any process cannot pass it; one client's
  admissions take turns), and `_record` swaps the hold for the real cost. Routine calls stop at
  `routine_daily_usd` (100 − `priority_reserve_pct` %); `priority=True` calls (desk votes,
  `triage_high` = a release or rules impact >= 4 at effort medium, deep of impact >= 4) go on
  to the ceiling. The analyzer alerts on `budget_state` (ok/reserve/exhausted, sticky per day).
  This computer's settings live in git-ignored `config/local.yaml` (deep-merged over
  settings.yaml) and `config/sources.local.yaml` (per-id fields); `gold_agent/local_config.py`
  is stdlib+PyYAML only so the Windows updater (`scripts/windows/perbarui.ps1`, desktop shortcut
  "Perbarui") can run the NEW revision's `migrate` before switching: edits to the tracked files
  move to the local ones (full diff kept in data/backups), then `merge --ff-only`, `uv sync
  --extra mt5`, config check, restart. `gold-agent config set|verified|migrate` (validated,
  rolled back on a bad value); the installer writes through it; tests load with `local=False`.
  `price_feed.primary: auto` (default) = MT5 when the MetaTrader5 package is installed (Windows)
  then Binance XAUUSDT perpetual (`binance_xau`, since 27 Oct; ADR-029) then Binance PAXG;
  `mt5_symbol: null` detects the broker's spot-gold symbol (`pick_gold_symbol`: Market Watch
  first, XAUUSD before GOLD, other quote currencies excluded).
  A feed switch (PAXG <-> MT5) resets the candle builder and reloads history from the feed now
  producing ticks (`FailoverAdapter.history` asks the active feed first), so two instruments'
  bars are never mixed.
  Accuracy layer (ADR-018..021). Related markets from the broker's MT5
  (`market/cross_assets.py`): symbols matched by name (suffix-tolerant, Market Watch first,
  `market.mt5_symbols` overrides), DXY from the broker's FX pairs (partial basket fixed once
  resolved, EURUSD required), bond futures inverted, per-market 15m/1h z-scores and the 5-min
  return correlation with gold shrunk to a prior (150 pairs) that sets each market's weight in M;
  `usd_source: auto`. Daily fundamentals (`fundamentals/`, kv `fundamentals`, 15% of M):
  Treasury nominal/real curves (`${YEAR}` URL), CFTC disaggregated managed money, GLD holdings
  (never auto-verified: SPDR's terms), our US data-surprise index, geopolitics heat -> bias in
  [-1, 1]; a factor without data counts as neutral (0) and is named. Quant (`market/quant.py`): GARCH(1,1) MLE
  (EWMA fallback), Hurst, efficiency ratio, ADX, MACD, a Kalman drift filter whose t is
  calibrated on random walks (|t| >= 2 about 5%), regime thresholds calibrated the same way
  (tests guard both). `signals/odds.py`: TP1-before-SL by antithetic Monte Carlo over the
  validity window, drift only from a proven edge (Wilson lower bound > 0.5), blended
  (Dirichlet, 20 pseudo-setups) with the replayed record of past setups (`signals/setups.py`,
  one trade at a time, stop first on a two-sided bar); EV in R; the EV gate waits when the whole
  95% EV band is negative with >= `ev_gate_min_setups` setups. `Signal.odds` shows in Telegram,
  on Live and in the brief. Desk since 26 Oct: `desk_votes` (3) Opus 5.5 answers at effort
  low (25 s timeout), a strict majority of the answers that came; Fable 5.1 and `desk_second`
  removed (too slow: big moves were gone before the answer); `desk_reviews_per_day` 60 (about
  $0.15 a review). Dashboard: per-timeframe detail cards with a
  glossary and the "Pasar penggerak emas" card (related markets, macro parts, fundamentals).
  Windows: the updater tests the official fundamentals sources and marks the passing ones
  verified (step 6/7); the "Perbarui" shortcut runs the newest updater (fetched first), so a
  change to the update steps applies on the same update.
  Econophysics + meta-labeling (ADR-022/023): `market/econophysics.py` (numpy only, each
  estimator recovered on known series and calibrated on random walks in
  `tests/test_econophysics.py`): Hill tail index + Student-t nu of GARCH residuals (MLE),
  permutation entropy (structured < 0.989), MF-DFA h(2) and width, OU with Dickey-Fuller
  (-2.87), Hawkes MLE (branching, intensity, next-hour odds; time in trading minutes, weekend
  gaps are not moves), transfer entropy with shuffle surrogates + White-robust lag-1 corr,
  RMT vs Marchenko-Pastur, LPPLS confidence (interior fits, damping, gain filters). Per chart:
  cached in `quant.slow_reads` (cache=False for past reads in worker threads) onto
  `TimeframeState`; market-wide: `market/physics.py` built every 60 s on the context clock from
  copies of the minute series in a thread, `MarketContext.physics`, LPPLS once per new D1 bar
  (H1 history 7000 bars so D1 has ~300 days); gold minutes seeded from stored M1. Odds: 20,000
  paths, Student-t bars by scale mixture, OU pull when real. `signals/meta.py`: L2 logistic
  meta-labeling on the last 400 replayed setups with features from `timeframe_state` (no
  train/serve skew),
  walk-forward Brier vs base rate, used only when Diebold-Mariano t >= 2 on >= 40 predictions
  (then the record's TP1/SL split follows the model). Words in one place
  (`market/physics_read.py`) for the dashboard ("Ekonofisika pasar" card, per-TF rows), the
  brief (`<ekonofisika>` + per-TF fields, prompt rule 5) and Telegram (`Signal.physics_note`).
  Real-time: `AccuracyBook` on the wall clock never makes a caller wait (a stale or missing
  result starts a background rebuild, one timeframe at a time, and the caller gets what there
  is; replays and tests compute in line); the dashboard's book skips meta-labeling
  (`learn=False`). The engine state goes on the bus every evaluation (`publish_state_seconds`
  1, stored on a decision change, a wording change at most once a minute) and the dashboard
  serves the newest pushed one (`LiveHub.last_state`).
  Speed: minute series (`market/minutes.py`, shared by the dollar tracker and the related
  markets) keep numpy arrays rebuilt only when a minute is added, so a context costs a few ms
  instead of a few hundred; `market/physics.py` returns arrays, not per-minute dicts. One
  `AccuracyBook.shared(ctx)` per process (the dashboard reads the signal service's results; a
  learning user joining rebuilds once with the meta model); meta feature rows are memoised
  across hourly refits. The dashboard serves the pushed context (`LiveHub.context()`, parsed
  once per push) instead of the one stored a minute ago; between bars the Live page fetches the
  indicator read only (`/api/indicators?series=0`), the overlay lines on a new bar; the read,
  timing and forecast load side by side, so one failing panel never leaves the others on
  "Memuat…".
  MT5: the agent never logs in (no MT5_* secrets): it attaches to the terminal's logged-in
  account and fails with "belum login" otherwise; `FailoverAdapter.problem` says why no ticks
  come (context note "sumber harga: ...", the engine's WAIT reason, the Live price line), and
  when every feed is down it goes back to the primary at once instead of after 15 min.
  Fast moves (26 Oct, ADR-024..028): the user missed big rises and falls after the switch to
  Fable. Everything runs every second (`infra.context_interval_seconds`, `evaluate_seconds`,
  `publish_state_seconds` = 1): `ContextCache` recomputes a chart state only on a new bar or
  per `STATE_REFRESH` (close always current), odds Monte Carlo in volatility units with an LRU
  cache, dashboard bar reads cached per minute, the Live card and gauge follow the pushed state.
  Momentum impulses (`market/impulse.py`, `signal_engine.impulse`): triggers per minute from
  complete M1 bars (1/3/5/10-min move >= z 3 x RMS per-minute sigma x sqrt(k) AND a break of the
  last 30-min range), checked against the price every second; an impulse becomes a candidate
  ("impulse" setup, sized on M5, entry <= 1.5 ATR(M5) past where it began, replacing the EMA20
  rule on its side) unless the composite leans against it by > `max_against` or strong news
  opposes it. `signals/impulses.py` replays the last 7 days of impulses on stored M1 (intrabar
  trigger, stop first) into a record (accuracy book key "impulses") that blends into the odds
  and the EV gate; tests pin ~50% on random walks and a real edge on momentum data. Telegram
  "⚡ Momentum" line, Live impulse banner, browser notification + chime per new BUY/SELL
  ("🔕 Notifikasi" button). Proxy re-base (`market/rebase.py`): the broker bid typed on Live or
  `/finex <bid>` (bus `price.calibrate`, answer in kv) shifts ticks, in-memory and stored bars
  and minute series of the proxy feed by one offset (kv `price_offset`); refused for a broker
  feed or during an active signal; `/finex reset`. Tanya AI (`assistant/chat.py`, route
  `llm.chat` Opus 5.5 effort low, prompt `prompts/chat_system.md`): the program writes one brief
  (price + re-base, engine state, active signal, impulse, the timing view, charts, events, news,
  related markets, fundamentals), `LlmClient.text()` (messages.create, shares `_call` with
  `structured()`), answers made plain text; dashboard POST `/api/chat` (same-origin JSON),
  Telegram `/tanya` or any plain admin message (`block=False`, per-user thread of 6 turns);
  `llm.chat_per_day` (60) counted in `llm_usage` (shared by dashboard and Telegram); without AI
  the program's own summary answers. Pages and static files are sent `Cache-Control: no-cache`
  so an update shows without a hard refresh.
  Spot gold without MT5 (27 Oct, ADR-029): the user saw 4012 on PAXG vs 3999 at Finex. Feed
  `binance_xau` = Binance's USD-M XAUUSDT perpetual (index from XAUUSD spot vendors): bookTicker
  on the `/public` route of fstream (unrouted connections stay silent for /market streams), klines
  from `/fapi/v1/klines` (1500/request, `BinanceAdapter(klines=FUTURES_KLINES)`); still proxy,
  re-base still works. Updater/installer verify its two sources; a computer on `binance_paxg`
  moves to `binance_xau` (`gold-agent config get`). `FailoverAdapter`: a run that delivered ticks
  and then dropped is a reconnect, not a strike; on a fallback the primary is retried every
  `retry_primary_seconds` (600) with 15 s to deliver, else the fallback resumes at once. The
  Live price line shows Bid · Ask · session · feed in words.
- Phase 8 (order execution) is NOT built: it needs explicit approval after ≥ 2–4 weeks of
  recorded paper trading.
