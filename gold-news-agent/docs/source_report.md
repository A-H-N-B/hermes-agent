# Laporan Verifikasi Sumber

> Dibuat otomatis oleh `scripts/verify_sources.py`. Jangan diedit manual: jalankan ulang skripnya. Semua angka berasal dari request nyata pada waktu di bawah.

## Lingkungan pengukuran

- Waktu: 2026-10-13 20:47:08 WIB s/d 2026-10-13 20:47:14 WIB
- Host: Linux 6.18.44-fc-v114 (x86_64) · Python 3.12.3
- User-Agent: `gold-news-agent/0.0.1 (personal research bot)`
- Selisih jam lokal vs server: median -0,5 dtk dari 1 server (resolusi header `Date` 1 dtk; hanya cek kasar, bukan pengganti NTP/chrony)
- Mode watch: **tidak dijalankan**, jadi latensi sumber belum terukur. Jalankan dengan `--watch-minutes 60` atau lebih di host produksi.
- **Catatan lingkungan:** Dijalankan di container cloud Claude Code; kebijakan jaringan environment ini menolak koneksi ke host sumber (proxy CONNECT rejected). Ini BUKAN pengukuran host produksi.

## Ringkasan

Total 30 sumber: ✅ OK 1, ⚠️ Cek 0, ❌ Gagal 24, ⏭️ Dilewati 5

## Hasil per sumber

| Status | ID | Tier | Jenis | HTTP | Respons | Item | Item terbaru (umur) | Presisi waktu | Cache | robots.txt | Alasan |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ✅ OK | `anthropic_api` | 1 | infra/html | 404 | 0,1 dtk | — | — | — | tidak ada | n/a | terjangkau (HTTP 404) |
| ❌ Gagal | `telegram_bot_api` | 1 | infra/html | — | — | — | — | — | — | n/a | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `binance_paxg_rest` | 1 | market/json | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `binance_paxg_ws` | 1 | market/websocket | — | — | — | — | — | — | n/a | InvalidProxyStatus: proxy rejected connection: HTTP 403 |
| ⏭️ Dilewati | `twelvedata_xau_quote` | 1 | market/json | — | — | — | — | — | — | n/a | butuh TWELVEDATA_API_KEY di .env |
| ❌ Gagal | `financialjuice_rss` | 1 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `investinglive_rss` | 1 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ⏭️ Dilewati | `telegram_squawk` | 1 | news/telegram | — | — | — | — | — | — | n/a | belum ada channel dipilih (pertanyaan Q5) |
| ❌ Gagal | `bls_schedule_ics` | 2 | calendar/ics | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `ff_calendar_week` | 2 | calendar/json | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `fomc_calendar_page` | 2 | calendar/html | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `treasury_auctions_upcoming` | 2 | calendar/json | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `bea_releases` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `bls_latest` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `boj_whatsnew` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `cnbc_economy` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `ecb_press` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `fed_press_all` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `fed_press_monetary` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `fed_speeches` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `fxstreet_news` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `google_news_fed` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `google_news_gold` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `kitco_news` | 2 | news/rss | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ❌ Gagal | `cftc_cot_gold` | 3 | macro/json | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ⏭️ Dilewati | `fred_dfii10` | 3 | macro/json | — | — | — | — | — | — | n/a | butuh FRED_API_KEY di .env |
| ⏭️ Dilewati | `fred_dgs10` | 3 | macro/json | — | — | — | — | — | — | n/a | butuh FRED_API_KEY di .env |
| ❌ Gagal | `spdr_gld_holdings` | 3 | macro/csv | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |
| ⏭️ Dilewati | `finnhub_forex_news` | 3 | news/json | — | — | — | — | — | — | n/a | butuh FINNHUB_API_KEY di .env |
| ❌ Gagal | `gdelt_gold` | 3 | news/json | — | — | — | — | — | — | unknown | ditolak proxy/kebijakan jaringan: 403 Forbidden |

## Catatan per sumber & ToS

- `anthropic_api` (Anthropic API): Reachability only.
- `bea_releases` (BEA news releases): US government public information.
- `binance_paxg_rest` (Binance PAXGUSDT book ticker (market-data-only REST)): Fallback for the WebSocket and basis checks.
- `binance_paxg_ws` (Binance PAXGUSDT trades (market-data-only WebSocket)): PAXG is a gold-backed token, a proxy not XAUUSD spot; trades 24/7 (weekend gap estimator). api.binance.com refuses US IPs. ToS: https://www.binance.com/en/terms.
- `bls_latest` (BLS latest releases): BLS blocks generic user agents; needs http.contact_email in the User-Agent. ToS: https://www.bls.gov/bls/linksite.htm.
- `bls_schedule_ics` (BLS release schedule (iCalendar)): Needs http.contact_email in the User-Agent, like bls_latest.
- `boj_whatsnew` (Bank of Japan what's new): Official; verify feed path.
- `cftc_cot_gold` (CFTC Commitments of Traders — COMEX gold (legacy, futures only)): Weekly positioning (Tuesday data, released Friday 15:30 New York). Dataset id to verify.
- `cnbc_economy` (CNBC economy): Headlines + summaries only; no full-text extraction.
- `ecb_press` (ECB press releases): Reuse allowed with attribution. ToS: https://www.ecb.europa.eu/services/disclaimer/html/index.en.html.
- `fed_press_all` (Federal Reserve press releases (all)): US government public information. ToS: https://www.federalreserve.gov/disclaimer.htm.
- `fed_press_monetary` (Federal Reserve monetary policy releases (FOMC statements, minutes)): Poll faster around 14:00 New York on FOMC days.
- `fed_speeches` (Federal Reserve speeches): Board speeches only; regional Fed presidents publish on their own sites.
- `ff_calendar_week` (Forex Factory weekly calendar (JSON)): Forecast/previous only, no actuals. Cloudflare 429 on frequent pulls; cache locally.
- `financialjuice_rss` (FinancialJuice headlines (RSS)): Free RSS; check whether automated polling every 10s is acceptable. Pro/Elite tiers are faster. ToS: https://www.financialjuice.com/terms.
- `finnhub_forex_news` (Finnhub market news (forex)): Free tier 60 calls/min; personal use. ToS: https://finnhub.io/terms-of-service.
- `fomc_calendar_page` (FOMC meeting calendar (HTML page)): Parsed weekly for meeting dates, SEP flags and the blackout window.
- `fred_dfii10` (FRED 10Y real yield (DFII10)): Daily series with ~1 day lag; context, not intraday. ToS: https://fred.stlouisfed.org/docs/api/terms_of_use.html.
- `fred_dgs10` (FRED 10Y nominal yield (DGS10)): Same terms as fred_dfii10. Intraday yields need another feed (section C).
- `fxstreet_news` (FXStreet news): Verify feed path and terms. ToS: https://www.fxstreet.com/terms-and-conditions.
- `gdelt_gold` (GDELT DOC 2.0 — gold articles, last hour): Open data; ~15 min update cadence. Context only, never a trigger. ToS: https://www.gdeltproject.org/about.html#termsofuse.
- `google_news_fed` (Google News search — Federal Reserve): Same terms as google_news_gold.
- `google_news_gold` (Google News search — gold): Personal, low-rate use only; links are Google redirect URLs, canonicalise before dedup. ToS: https://policies.google.com/terms.
- `investinglive_rss` (investingLive (ex-ForexLive) news feed): Feed URL after the July 2025 rebrand is unconfirmed; verify, then read the terms. ToS: https://investinglive.com/terms-and-conditions.
- `kitco_news` (Kitco news): Feed path unconfirmed; drop if no official feed exists (no HTML scraping).
- `spdr_gld_holdings` (SPDR Gold Shares (GLD) daily holdings): ETF flow proxy; check terms before automated download. ToS: https://www.spdrgoldshares.com/usa/terms-of-use/.
- `telegram_bot_api` (Telegram Bot API): Reachability only; notifications go through here.
- `telegram_squawk` (Telegram squawk channels (user-selected)): Read-only user session via Telethon; join channels manually; never send, scrape members, or automate joins. ToS: https://core.telegram.org/api/terms.
- `treasury_auctions_upcoming` (TreasuryDirect upcoming auctions): Official API.
- `twelvedata_xau_quote` (Twelve Data XAU/USD quote): Free plan ~8 credits/min; WebSocket needs a paid plan. ToS: https://twelvedata.com/terms.

## Langkah berikutnya

1. Sumber ✅ OK: baca ToS-nya, lalu ubah `status: candidate` menjadi `status: verified` di `config/sources.yaml`. Collector hanya menjalankan sumber `verified`.
2. Sumber ⚠️/❌: perbaiki URL (lihat redirect/catatan), isi key di `.env`, atau ubah menjadi `status: rejected`.
3. Ukur latensi di host produksi: `uv run python scripts/verify_sources.py --watch-minutes 120`, idealnya mencakup satu rilis data AS (19:30/20:30 WIB).
