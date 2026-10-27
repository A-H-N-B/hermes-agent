# Prompt Claude Code — Gold News Agent (XAUUSD) Real-Time 24/7

> File ini punya 2 bagian:
> **A. Persiapan** → dikerjakan kamu sendiri sebelum mulai.
> **B. Prompt** → instruksi untuk Claude Code.
>
> Cara paling mudah: simpan file ini di folder project, buka Claude Code di folder itu, lalu ketik:
> `Baca PROMPT_XAUUSD_NEWS_AGENT.md bagian B, masuk plan mode, lalu mulai dari Fase 0.`

---

## A. PERSIAPAN (untuk kamu, bukan untuk Claude Code)

### A1. Plugin Claude Code yang dipasang

Jalankan di dalam sesi Claude Code (marketplace `claude-plugins-official` biasanya sudah tersedia; kalau belum, jalankan dulu `/plugin marketplace add anthropics/claude-plugins-official`):

```
/plugin install context7@claude-plugins-official
/plugin install feature-dev@claude-plugins-official
/plugin install pyright-lsp@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
/plugin install code-review@claude-plugins-official
/plugin install playwright@claude-plugins-official
/reload-plugins
```

| Plugin | Gunanya di project ini |
|---|---|
| context7 | Mengambil dokumentasi library versi terbaru (anthropic, telethon, MetaTrader5, fastapi, dll) supaya Claude Code tidak menulis API yang sudah usang |
| feature-dev | Alur kerja fitur bertahap (eksplorasi → desain → implementasi → review) |
| pyright-lsp | Cek tipe Python secara real-time |
| security-guidance | Mencegah API key bocor ke kode / git, praktik keamanan |
| code-review | Review kode di akhir tiap fase |
| playwright | Menguji dashboard web di browser otomatis |

### A2. MCP opsional (hanya alat bantu saat development)

MCP dipakai Claude Code untuk *mengeksplorasi* data saat ngoding — **bukan** bagian dari program produksi (program memanggil API langsung).

- Twelve Data hosted MCP: `https://mcp.twelvedata.com` (open source: github.com/twelvedata/mcp). Cek dokumentasi Twelve Data untuk cara mengirim API key, lalu tambahkan dengan `claude mcp add --transport http twelvedata https://mcp.twelvedata.com ...`

### A3. Akun & API key yang perlu disiapkan

| Variabel | Status | Dapat dari | Catatan |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Wajib** | console.anthropic.com | Otak analisis (Claude API) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | **Wajib** | @BotFather di Telegram | Kirim sinyal ke HP |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Sangat disarankan | my.telegram.org | Membaca channel berita cepat (squawk) secara push — sumber gratis tercepat |
| `FRED_API_KEY` | Disarankan (gratis) | fred.stlouisfed.org | Real yield, data makro AS |
| `TWELVEDATA_API_KEY` | Opsional | twelvedata.com | Paket gratis: REST terbatas (sekitar 8 kredit/menit); WebSocket real-time perlu paket berbayar |
| `FINNHUB_API_KEY` | Opsional | finnhub.io | Berita umum/forex; gratis 60 call/menit, beberapa endpoint forex berbayar |
| Akun MT5 (demo dulu) | Opsional | Broker kamu | Harga broker real-time + eksekusi. Library Python MT5 **hanya jalan di Windows** |
| X (Twitter) API | Opsional, berbayar | developer.x.com | Pay-per-use, sekitar $0,005 per post yang dibaca |

---

## B. PROMPT UNTUK CLAUDE CODE

### 1. Peran & tujuan

Kamu adalah senior Python engineer sekaligus quant analyst yang paham pasar emas. Bangun sistem bernama **Gold News Agent** yang:

1. Memantau berita & data yang relevan dengan emas / XAUUSD **24 jam nonstop**, dengan latensi serendah mungkin (target: hitungan detik sejak berita muncul di sumber tercepat).
2. Menganalisis setiap berita relevan secara real-time: arah dampak ke emas (bullish/bearish/netral), besarnya dampak, tingkat kejutan vs ekspektasi, dan apakah sudah *priced-in*.
3. Menggabungkan analisis berita dengan konteks pasar (harga XAUUSD real-time, USD/DXY, yield AS, teknikal multi-timeframe, sesi pasar).
4. Menghasilkan **rekomendasi BUY / SELL / WAIT** lengkap (entry, stop loss, take profit, masa berlaku, alasan) dan **outlook per jam (WIB)** untuk 24 jam ke depan.
5. Mengirim notifikasi ke Telegram dan menampilkan semuanya di dashboard web.
6. Mencatat semua sinyal dan mengevaluasi performanya secara otomatis (paper trading), supaya bobot & aturan bisa dikalibrasi dengan data, bukan perasaan.

### 2. Prinsip & batasan (WAJIB dipatuhi)

- **Ini sistem pendukung keputusan, bukan jaminan profit.** Mode default: `SIGNAL_ONLY`. Eksekusi order otomatis hanya ada di fase opsional terakhir, mati secara default, dan wajib diuji di akun demo dulu.
- **Jujur soal latensi.** Jangan pernah mengklaim angka latensi tanpa mengukurnya. Setiap item wajib mencatat timestamp di tiap tahap (lihat bagian 4).
- **Hormati sumber data.** Patuhi ToS dan robots.txt, jangan bypass paywall, jangan scraping halaman yang butuh login, pakai rate limit dan header `If-None-Match` / `If-Modified-Since`.
- **Anti-halusinasi.** LLM hanya boleh menganalisis teks yang diberikan. Dilarang mengarang angka, kutipan, atau berita. Setiap kesimpulan harus merujuk ke ID item sumber.
- **Tidak ada lookahead bias.** Di backtest, keputusan hanya boleh memakai data yang *sudah dilihat sistem* pada waktu itu (`first_seen_at`, bukan `published_at`).
- **Waktu.** Simpan semua timestamp dalam UTC. Tampilkan dalam WIB (`Asia/Jakarta`). Jam sesi London/New York dihitung dengan `zoneinfo` (DST otomatis) — **jangan hardcode offset**.
- **Rahasia.** Semua key di `.env` (sediakan `.env.example`), `.env` masuk `.gitignore`. Tidak ada key di kode, log, atau commit.
- **Bahasa.** Kode, nama variabel, dan komentar teknis dalam Bahasa Inggris. Semua output untuk pengguna (Telegram, dashboard, alasan sinyal, laporan) dalam **Bahasa Indonesia**.
- **Data palsu hanya untuk test.** Mock/fixture boleh di folder `tests/`, tidak boleh dipakai diam-diam di jalur produksi. Kalau sumber data mati, sistem harus bilang "data tidak tersedia", bukan mengisi angka karangan.

### 3. Pertanyaan yang harus kamu tanyakan ke saya di Fase 0

Tanyakan sekaligus (pakai pilihan ganda kalau bisa), sertakan default yang kamu sarankan:

1. Gaya trading: scalping (M1–M5), intraday (M15–H1), atau swing (H4–D1)? *(default: intraday)*
2. Sumber harga: MT5 broker saya (Windows), Twelve Data, atau gratis dulu (Binance PAXGUSDT sebagai proxy)? Kalau MT5: nama simbol emas di broker (mis. `XAUUSD`, `XAUUSDm`, `GOLD`).
3. Modal, risiko per trade (%), maksimal kerugian harian (%). *(default: risiko 0,5–1%, max loss harian 3%)*
4. Budget bulanan untuk API berbayar (Claude API, data feed, X API). Ini menentukan sumber mana yang diaktifkan dan batas biaya LLM harian.
5. Channel Telegram berita cepat yang mau dipantau (saya yang pilih; sistem harus configurable).
6. Di mana program akan jalan 24/7: VPS Linux, VPS Windows, atau PC rumah?
7. Jam tenang notifikasi (mis. 00:00–05:00 WIB hanya kirim yang high-impact)?
8. Mau ikut "news trading" saat rilis data (berisiko tinggi, spread melebar) atau hanya masuk setelah konfirmasi harga?

### 4. Target latensi & ekspektasi realistis

Setiap item berita wajib menyimpan: `published_at` (dari sumber, jika ada) → `first_seen_at` (sistem menerima) → `triaged_at` → `analyzed_at` → `signal_at` → `notified_at`. Tampilkan p50/p95 per sumber di dashboard.

| Jalur | Target internal (dari item diterima → notifikasi terkirim) |
|---|---|
| Fast path (aturan + parser angka rilis data, tanpa LLM) | < 1–2 detik |
| Triage LLM cepat (model kecil) | p95 < 5 detik |
| Analisis mendalam LLM (model besar, hanya untuk berita dampak tinggi) | p95 < 20 detik |

Perkiraan keterlambatan dari sumber (WAJIB diverifikasi & diukur di Fase 0 — ini hanya titik awal):

| Jenis sumber | Perkiraan keterlambatan |
|---|---|
| Channel Telegram squawk (push) | beberapa detik |
| Feed headline cepat (FinancialJuice, investingLive) dipolling tiap 10–15 detik | ~10–60 detik |
| RSS media besar / Google News | menit hingga belasan menit |
| GDELT | ~15 menit (hanya untuk konteks, bukan pemicu) |

Catatan penting: harga sering bergerak **sebelum** headline gratis muncul. Karena itu sistem harus punya **price-led detection** (bagian 7.9) — lonjakan harga memicu pencarian penyebabnya.

### 5. Arsitektur

```
                ┌─────────────────────── COLLECTORS (async, 1 per sumber) ───────────────────────┐
                │ Telegram (Telethon push) │ RSS/HTTP pollers │ WebSocket news │ Official (Fed, BLS) │
                └───────────────┬────────────────────────────────────────────────────────────────┘
                                ▼
                    Normalizer → Dedup/Clustering → Redis Stream "news.raw"
                                ▼
                 ┌──────────── PREFILTER (keyword/regex, < 10 ms) ────────────┐
                 │ tidak relevan → arsip saja      relevan ↓                   │
                 └────────────────────────────────────────────────────────────┘
                                ▼
        ┌── FAST PATH ──────────────────────┐     ┌── LLM PATH ───────────────────────────┐
        │ Parser angka rilis data            │     │ Triage (model cepat) → skor & arah     │
        │ (actual vs forecast) + playbook    │     │ Deep analysis (model besar) jika       │
        │ skenario pra-hitung → FLASH ALERT  │     │ impact ≥ 3 atau event terjadwal        │
        └──────────────┬────────────────────┘     └──────────────┬────────────────────────┘
                       └───────────────┬──────────────────────────┘
                                       ▼
   MARKET CONTEXT ──────────► SIGNAL ENGINE (skor gabungan + aturan risiko + sesi/jam)
   (tick XAUUSD, candle,                       ▼
    DXY, yield, teknikal,        Signals DB → Telegram │ Dashboard (WebSocket) │ Paper-trade tracker
    kalender ekonomi)
                                       ▲
   SCHEDULER: sync kalender, playbook pra-event, outlook per jam, brief sesi, laporan harian/mingguan
```

Komponen dipisah sebagai service/proses yang berkomunikasi lewat Redis Streams, sehingga satu komponen crash tidak menjatuhkan yang lain.

### 6. Sumber data

Semua sumber didaftarkan di `config/sources.yaml` (bukan hardcode), contoh:

```yaml
- id: financialjuice_rss
  type: rss
  url: https://www.financialjuice.com/feed.ashx?xy=rss   # verifikasi di Fase 0
  poll_seconds: 10
  tier: 1
  reliability: 0.8
  enabled: true
  tos_note: "cek ketentuan penggunaan sebelum produksi"
```

Di Fase 0 buat `scripts/verify_sources.py` yang mengecek tiap sumber: status HTTP, format, kesegaran item terbaru, selisih `published_at` vs waktu fetch, dan catatan ToS. Hasilnya `docs/source_report.md`. **Jangan pakai URL yang belum terverifikasi.**

#### 6.1 Harga & pasar terkait (adapter pluggable, antarmuka sama)

| Adapter | Kegunaan | Catatan |
|---|---|---|
| `mt5` (paket `MetaTrader5`, opsional wrapper `pdmt5`) | Tick & candle broker real-time, spread, contract size, eksekusi | Hanya Windows. Pakai `symbol_info_tick`, `copy_ticks_from`, `copy_rates_range`, `symbol_info` |
| `twelvedata` | XAU/USD REST + WebSocket | WebSocket perlu paket berbayar; free plan cukup untuk candle historis terbatas |
| `binance_paxg` | Proxy emas real-time gratis via WebSocket publik PAXGUSDT | Proxy, bukan XAUUSD spot: hitung & tampilkan basis vs harga referensi, beri label "proxy" |
| `history` | Data historis untuk backtest (MT5 history, Dukascopy, Twelve Data) | Verifikasi library/format Dukascopy via Context7/web sebelum dipakai |

Instrumen konteks: DXY (atau proxy dari EURUSD jika DXY tidak tersedia), yield US 10Y & 2Y, real yield 10Y (FRED `DFII10`, harian), XAGUSD, S&P 500 futures/indeks, minyak. Tampilkan sumber dan keterlambatan masing-masing.

#### 6.2 Berita (tier)

- **Tier 1 — tercepat (pemicu sinyal):**
  - Channel Telegram squawk pilihan saya via **Telethon** (event handler `NewMessage`, push, bukan polling).
  - FinancialJuice RSS, investingLive (dulu ForexLive, rebrand Juli 2025) — cek feed resmi yang tersedia.
  - (Opsional, berbayar) X API untuk daftar akun tertentu; (opsional, berbayar) feed premium seperti FinancialJuice Pro/Elite atau news websocket komersial.
- **Tier 2 — resmi & media besar (konfirmasi + detail):**
  - Federal Reserve press releases RSS, kalender rilis BLS/BEA, US Treasury.
  - Google News RSS dengan query terarah (mis. `gold price`, `Federal Reserve`, `XAUUSD`), Kitco, FXStreet, CNBC, dll — verifikasi URL feed.
- **Tier 3 — konteks (bukan pemicu):** GDELT 2.0, Finnhub market news (`category=forex/general`), laporan World Gold Council, data ETF emas, pembelian emas bank sentral.

Untuk artikel panjang, ekstrak isi dengan `trafilatura` hanya jika diizinkan; untuk pemicu cepat cukup headline + ringkasan.

#### 6.3 Kalender ekonomi

- Feed JSON mingguan Forex Factory: `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (berisi forecast & previous, **tidak berisi actual**). Cache lokal, sync maks beberapa kali per jam (ada rate limit/Cloudflare 429), konversi zona waktu dengan benar.
- Nilai **actual** diambil dari headline squawk/feed cepat (parser di 7.5) atau rilis resmi.
- Fokus event: CPI, Core CPI, PCE, NFP, unemployment rate, jobless claims, retail sales, ISM/PMI, GDP, FOMC (statement, dot plot, konferensi pers), notulen FOMC, pidato pejabat Fed, keputusan ECB/BoJ/PBoC, lelang Treasury besar.
- Daftar nama Ketua & anggota FOMC disimpan di config dan diperbarui berkala (jangan hardcode dari ingatan).

### 7. Pipeline pemrosesan berita

**7.1 Ingestion.** Semua collector `async` (asyncio), reconnect otomatis dengan exponential backoff + jitter, heartbeat per sumber. Polling memakai ETag/Last-Modified. Interval polling per sumber dari config.

**7.2 Normalisasi.** Skema `NewsItem` (pydantic): `id, source_id, url_canonical, title, body, lang, published_at, first_seen_at, raw_hash, tier, reliability`.

**7.3 Dedup & clustering.**
- Hash judul ternormalisasi (lowercase, tanpa tanda baca & angka waktu).
- Kemiripan fuzzy (`rapidfuzz`, rasio > 90) dalam jendela 30 menit.
- Embedding kecil (`sentence-transformers/all-MiniLM-L6-v2`, cosine > 0,9) untuk parafrase.
- Item mirip digabung dalam satu `NewsCluster`. Sumber pertama menentukan waktu; sumber berikutnya menambah `confirmation_count`.

**7.4 Prefilter cepat.** Regex/keyword EN + ID dari `config/keywords.yaml` (gold, XAU, bullion, Fed, FOMC, CPI, PCE, payrolls, NFP, jobless claims, Treasury yields, dollar/DXY, tariff, sanctions, war, ceasefire, missile, central bank gold purchases, PBoC, ETF holdings, COMEX, emas, suku bunga, dll). Tidak relevan → arsip saja (tetap disimpan untuk riset).

**7.5 Fast path: parser rilis data.** Parser regex untuk pola headline seperti `US CPI MoM 0.4% vs 0.3% exp; prior 0.2%`. Hitung `surprise = actual − forecast` dan z-score memakai standar deviasi surprise historis event itu. Cocokkan ke **playbook skenario** yang sudah dihitung sebelum event (bagian 11.4) → kirim FLASH ALERT dalam < 1–2 detik tanpa menunggu LLM. LLM memperkaya analisis setelahnya.

**7.6 Triage LLM (model cepat).** Untuk setiap cluster yang lolos prefilter. Output terstruktur (lihat 13.2). Timeout ketat; jika gagal/timeout → fallback ke skor aturan + label "analisis AI tertunda".

**7.7 Analisis mendalam (model besar).** Hanya jika `impact_score ≥ 3`, event terjadwal high-impact, atau ada konflik antar sinyal. Menghasilkan narasi, skenario lanjutan, dan risiko.

**7.8 Verifikasi silang.** Klaim besar (perang, sanksi, perubahan kebijakan mendadak) dari satu sumber tidak resmi → `needs_confirmation = true`, bobot dikurangi sampai ada sumber kedua atau sumber resmi. Skor `reliability` tiap sumber diperbarui otomatis dari riwayat akurasinya.

**7.9 Price-led detection.** Pantau stream tick. Jika return 1 menit > k × ATR(1m) (k di config, mis. 3) atau lonjakan spread/volatilitas → kirim "VOLATILITY ALERT", cari berita 5 menit terakhir; jika tidak ada, jalankan pencarian terarah (Google News RSS query, channel Telegram) dan tandai "penyebab belum diketahui" sampai ketemu.

### 8. Knowledge base fundamental emas

Simpan di `config/gold_drivers.yaml`, dipakai oleh rule engine **dan** dimasukkan ke system prompt LLM (di-cache). Isi minimal (arah = efek tipikal jangka pendek, bisa berbeda kalau sudah priced-in):

| Driver | Arah tipikal ke emas | Half-life awal (dikalibrasi nanti) |
|---|---|---|
| Real yield AS naik / turun | Bearish / bullish | jam – hari |
| USD (DXY) menguat / melemah | Bearish / bullish | jam |
| Fed hawkish (lebih sedikit pemangkasan, nada ketat) / dovish | Bearish / bullish | 1–3 hari |
| CPI/PCE di atas ekspektasi | Umumnya bearish jangka pendek (ekspektasi suku bunga lebih tinggi) | 30–90 menit untuk reaksi awal |
| Data tenaga kerja kuat / lemah | Bearish / bullish | 30–90 menit |
| Eskalasi geopolitik / de-eskalasi | Bullish (safe haven) / bearish | 2–6 jam |
| Pembelian emas bank sentral | Bullish | minggu |
| Perang dagang / tarif baru | Umumnya bullish | jam – hari |
| Kekhawatiran fiskal AS, shutdown, utang | Bullish | hari |
| Aksi jual saham tajam (risk-off) | Campuran: bisa turun dulu (likuidasi margin), lalu naik | jam |
| Arus ETF emas masuk / keluar | Bullish / bearish | hari |
| Opini/analisis tanpa informasi baru | Bobot sangat kecil | 1–2 jam |

Aturan penting: **reaksi pasar ditentukan oleh kejutan vs ekspektasi, bukan oleh headline mentah.** Selalu bandingkan dengan konsensus/forecast dan konteks positioning.

### 9. Konteks pasar & teknikal

- Candle M1, M5, M15, H1, H4, D1 dibangun dari tick (atau dari adapter).
- Indikator (implementasi sendiri dengan pandas/numpy + unit test, hindari dependensi yang tidak terawat): EMA 20/50/200, RSI 14, ATR 14, VWAP sesi, Bollinger Bands, pivot harian & mingguan.
- Level penting: high/low hari sebelumnya, range sesi Asia, swing high/low, angka bulat (kelipatan $10 dan $50).
- Regime volatilitas: persentil ATR 90 hari terakhir.
- Spread real-time (dari MT5 jika ada) — spread melebar = filter WAIT.
- Momentum DXY & yield 10Y (perubahan 15m/1h dalam z-score).

### 10. Mesin sinyal

**10.1 Skor komponen (rentang −1 s/d +1, positif = bullish emas):**

- `N` (berita): Σ arah × (impact/5) × confidence × reliability × exp(−ln2 × umur / half_life), lalu `tanh`.
- `M` (makro): kombinasi −z(ΔDXY) dan −z(Δyield/real yield).
- `T` (teknikal): keselarasan tren H1/H4 (EMA), regime RSI, posisi terhadap VWAP & level kunci.
- `P` (konfirmasi harga): pergerakan sejak berita searah analisis, dinormalisasi ATR.

**10.2 Skor gabungan:** `S = wN·N + wM·M + wT·T + wP·P` dengan bobot awal `0.35 / 0.20 / 0.25 / 0.20` (di config, dikalibrasi lewat backtest). Dikali faktor likuiditas sesi.

**10.3 Aturan keputusan (nilai awal, dikalibrasi nanti):**
- `|S| ≥ 0.35` dan confidence ≥ 60 → BUY/SELL; selain itu WAIT.
- **Hysteresis:** membalik arah butuh `|S| ≥ 0.5` arah berlawanan dan minimal 15 menit sejak sinyal terakhir. Ada cooldown dan batas jumlah sinyal per hari.
- **Blackout event:** event high-impact dalam ≤ 15 menit → WAIT, kecuali mode news-trading diaktifkan.
- **Filter:** spread > batas, likuiditas rendah (sekitar rollover harian), data harga basi (> N detik tanpa tick), atau LLM & sumber konflik berat → WAIT dengan alasan jelas.
- Setelah rilis data besar, tunggu konfirmasi harga 30–120 detik (configurable) sebelum sinyal trade (hindari whipsaw dan spread spike). FLASH ALERT tetap langsung dikirim.

**10.4 Level & ukuran posisi:**
- SL = max(1,2 × ATR(M15), di luar swing terdekat). TP1 = 1R, TP2 = 2R, TP3 = level struktur berikutnya. Saran pindah SL ke BE setelah TP1.
- Lot = (equity × risk%) / (jarak SL dalam $ × contract size). Contract size diambil dari broker (`symbol_info().trade_contract_size`), jangan diasumsikan.

**10.5 Skema output sinyal:**

```
signal_id, created_at_utc, created_at_wib, session, action (BUY|SELL|WAIT),
setup_type (news_momentum|news_fade|trend_continuation|breakout|range),
entry_type (market|limit|stop), entry_low, entry_high, stop_loss, tp1, tp2, tp3,
risk_reward, risk_pct, lot_size, valid_until_wib, confidence (0–100),
composite_score, components {N, M, T, P, session_factor},
top_reasons[3] (Bahasa Indonesia), invalidation[] (kondisi pembatal),
upcoming_events_4h[], linked_news_ids[], model_versions, config_hash
```

### 11. Rekomendasi berbasis jam (WIB)

**11.1 Peta sesi.** Hitung dinamis dari zona waktu asli setiap hari (`Asia/Tokyo`, `Asia/Shanghai`, `Europe/London`, `America/New_York`), lalu konversi ke WIB. Tabel acuan (untuk verifikasi unit test):

| Momen | Waktu lokal | WIB (DST AS & UK aktif) | WIB (keduanya winter) |
|---|---|---|---|
| Tokyo buka | 09:00 JST | 07:00 | 07:00 |
| Shanghai Gold Exchange buka sesi pagi | 09:00 Beijing | 08:00 | 08:00 |
| London buka | 08:00 London | 14:00 | 15:00 |
| LBMA Gold Price AM | 10:30 London | 16:30 | 17:30 |
| Rilis data AS (CPI, NFP, dll) | 08:30 New York | 19:30 | 20:30 |
| LBMA Gold Price PM | 15:00 London | 21:00 | 22:00 |
| Overlap London–New York (NY buka 08:00 s/d London tutup 16:00) | — | ±19:00–22:00 | ±20:00–23:00 |
| Pernyataan FOMC | 14:00 New York | 01:00 (hari berikutnya) | 02:00 |
| Rollover / tutup harian (likuiditas tipis, spread lebar) | 17:00 New York | 04:00 | 05:00 |

Ada jendela transisi saat hanya satu negara yang sudah ganti jam (mis. 2026: UK kembali ke GMT 25 Oktober, AS kembali ke EST 1 November). **Wajib ada unit test untuk tanggal-tanggal transisi ini.**

**11.2 Profil statistik per jam.** Dari minimal 1–2 tahun data M5, hitung per jam WIB **dan** per waktu relatif sesi (mis. "menit sejak NY buka", supaya pergeseran DST tidak merusak statistik): rata-rata range, median, deviasi, rata-rata spread, persentase candle naik, dan reaksi tipikal di sekitar event. Simpan di tabel `hourly_profile`, hitung ulang mingguan.

**11.3 Outlook 24 jam.** Dibuat setiap jam pada menit :00, saat pembukaan sesi, dan setiap ada berita high-impact. Format tabel:

| Jam (WIB) | Sesi | Event terjadwal | Volatilitas ekspektasi (range $) | Bias | Skenario | Confidence |
|---|---|---|---|---|---|---|
| 19:00–20:00 | NY + London | US CPI 19:30 | Tinggi (±$X) | TUNGGU DATA | CPI > forecast → SELL setelah konfirmasi; < forecast → BUY | — |

Aturan: bias untuk jam > 4 jam ke depan harus ditulis **bersyarat** (skenario) dengan confidence lebih rendah; jam dekat memakai status sinyal terkini. Outlook langsung diperbarui saat ada berita penting.

**11.4 Playbook event terjadwal.** T−60 menit sebelum event high-impact, buat tabel skenario pra-hitung berbasis z-score surprise historis, contoh US CPI m/m: z ≥ +1,5 → bearish kuat; +0,5..+1,5 → bearish ringan; −0,5..+0,5 → netral (lihat core); −1,5..−0,5 → bullish ringan; ≤ −1,5 → bullish kuat. Kirim playbook ke Telegram T−30 menit. Saat angka keluar, fast path cukup mencocokkan → alert instan.

**11.5 Jam rawan.** Tandai otomatis: ±15 menit sekitar rilis besar, sekitar rollover harian, Jumat malam menjelang tutup, Senin pagi saat buka (risiko gap), dan hari libur AS/UK (likuiditas tipis).

**11.6 Mode akhir pekan.** Pasar tutup Sabtu–Minggu: tetap kumpulkan berita, buat "outlook pembukaan Senin" (risiko gap) pada Minggu sore WIB.

### 12. Manajemen risiko & eksekusi (opsional, fase terakhir)

- Default `EXECUTION_ENABLED=false`. Jika diaktifkan: hanya akun demo dulu, pakai `order_send` MT5, wajib ada max posisi terbuka, max loss harian, kill switch (perintah Telegram `/stop`), dan opsi konfirmasi manual lewat tombol Telegram sebelum order.
- Semua pesan sinyal diakhiri disclaimer singkat: bukan saran keuangan, keputusan & risiko di tangan pengguna.
- Jangan pernah membuka posisi berdasarkan berita berlabel `needs_confirmation`.

### 13. Penggunaan LLM (Anthropic) & kontrol biaya

**13.1 Routing model** (nama model di config; cek nama & fitur terbaru di docs.claude.com atau via Context7 sebelum implementasi):

| Tugas | Model awal | Kapan |
|---|---|---|
| Triage tiap cluster relevan | `claude-haiku-5-5` | Setiap item lolos prefilter |
| Analisis mendalam, outlook per jam, playbook | `claude-sonnet-5-5` | Impact ≥ 3, event terjadwal, tiap jam |
| Brief harian & review mingguan performa | `claude-opus-5-5` (opsional) | 1× sehari / seminggu |

- Pakai SDK `anthropic` (async client). Output terstruktur lewat tool use dengan JSON schema (atau fitur structured output bila tersedia — cek docs), lalu validasi pydantic. Gagal validasi → retry 1×, lalu fallback aturan.
- **Prompt caching** untuk system prompt + knowledge base (bagian 8) agar cepat & murah.
- Timeout per panggilan, retry dengan backoff, circuit breaker. Jika API Claude bermasalah → mode "rules-only" + notifikasi admin.
- Catat token & biaya setiap panggilan ke tabel `llm_usage`. Ada `LLM_DAILY_BUDGET_USD`; jika terlampaui → hanya high-impact yang dianalisis LLM, sisanya aturan, dan kirim peringatan.
- (Opsional) `ProsusAI/finbert` lokal sebagai fitur tambahan/fallback — ingat: sentimen finansial umum ≠ arah emas, jadi hanya dipakai sebagai fitur, bukan keputusan.

**13.2 Skema output triage (pydantic):**

```
is_gold_relevant: bool
category: fed_policy | us_inflation | us_labor | us_growth | usd | yields | geopolitics |
          central_bank_gold | trade_tariff | fiscal_debt | risk_sentiment | physical_demand |
          etf_flows | commentary | other
event_type: scheduled_release | speech | breaking | analysis | rumor | recap
is_new_information: bool
direction_gold: bullish | bearish | neutral | mixed
impact_score: int 0–5
confidence: float 0–1
expected_horizon: minutes | hours | days | weeks
surprise: {actual, forecast, previous, unit, surprise_value} | null
priced_in_risk: low | medium | high
needs_confirmation: bool
entities: list[str]
summary_id: str      # ≤ 2 kalimat, Bahasa Indonesia
reasoning_id: str    # ≤ 3 kalimat, kenapa arahnya begitu
evidence: list[str]  # potongan teks dari sumber yang mendukung
source_item_ids: list[str]
```

**13.3 Inti system prompt triage** (tulis versi lengkapnya di `prompts/triage_system.md`):
"Kamu analis makro emas. Analisis HANYA teks berita yang diberikan beserta konteks pasar terlampir. Jangan menambah fakta atau angka dari luar. Tentukan apakah ini informasi baru, arah dampaknya ke XAUUSD, besar dampaknya, dan seberapa besar kemungkinan sudah priced-in. Bedakan fakta, rumor, dan opini. Jika ragu, turunkan confidence. Jawab hanya melalui tool `record_gold_analysis`."

**13.4 Research agent (opsional, bukan jalur cepat).** Dengan `claude-agent-sdk` (Python), buat agen yang tiap 4 jam dan di awal sesi membaca materi panjang (laporan WGC, pidato bank sentral, ringkasan berita cluster) melalui custom tool in-process yang mengakses database kita, lalu menulis brief naratif Bahasa Indonesia ke dashboard & Telegram.

### 14. Penyimpanan

- MVP: SQLite + Redis. Produksi: PostgreSQL (+ TimescaleDB untuk tick/candle) + Redis (stream, dedup cache, pub/sub).
- Tabel: `sources, news_items, news_clusters, analyses, calendar_events, ticks, candles, market_context, signals, signal_outcomes, hourly_profile, outlooks, playbooks, notifications, latency_metrics, llm_usage, source_health`.
- Migrasi dengan Alembic. Backup harian.

### 15. Notifikasi Telegram

Pakai `python-telegram-bot`. Tingkat prioritas: 🔴 high-impact (langsung, tembus jam tenang), 🟡 normal, 🔵 digest. Rate limit & penggabungan pesan agar tidak spam. Perintah bot: `/status`, `/signal`, `/outlook`, `/calendar`, `/stats`, `/mute 1h`, `/stop`.

Template sinyal:
```
🟢 BUY XAUUSD | Confidence 72/100
🕐 {time} WIB · Sesi {session}
💵 Harga {bid}/{ask} · Spread {spread}
🎯 Entry {entry_low}–{entry_high} ({entry_type})
🛑 SL {sl} ({sl_dist}$ · {sl_atr}×ATR)
✅ TP1 {tp1} (1R) · TP2 {tp2} (2R) · TP3 {tp3}
⏳ Berlaku s/d {valid_until} WIB
📰 Pemicu: {ringkasan} — {sumber} (diterima {latency} dtk)
📊 Berita {N} · Makro {M} · Teknikal {T} · Konfirmasi {P}
⚠️ Batal jika: {invalidation}
📅 Event berikutnya: {event} pukul {time} WIB
Bukan saran keuangan. Keputusan & risiko di tanganmu.
```

Template flash (fast path):
```
🔴 FLASH | {headline_angka}
Surprise {surprise} (z={z}) → {arah} emas · skenario playbook: {bucket}
Menunggu konfirmasi harga {n} dtk sebelum sinyal…
Diterima {time} WIB · latensi {latency} dtk
```

Sertakan juga template: outlook per jam, playbook pra-event, volatility alert, brief sesi, laporan harian, dan alert kesehatan sistem (sumber mati, budget LLM, data harga basi).

### 16. Dashboard web

FastAPI + WebSocket untuk update live, frontend ringan (HTMX/Alpine atau React kecil) + TradingView **lightweight-charts** (open source). Responsif di HP, Bahasa Indonesia, tema gelap. Halaman:

1. **Live**: kartu sinyal aktif, grafik XAUUSD dengan marker berita & sinyal, feed berita berlabel arah/dampak/latensi.
2. **Outlook 24 jam** (tabel bagian 11.3) + countdown event berikutnya.
3. **Kalender ekonomi** dengan forecast/previous/actual dan playbook.
4. **Performa**: win rate, rata-rata R, expectancy, drawdown, per kategori berita, per jam, per sesi; grafik kalibrasi confidence.
5. **Kesehatan sistem**: status & latensi p50/p95 tiap sumber, umur tick terakhir, biaya LLM hari ini, error terbaru.

Proteksi dashboard dengan login sederhana jika dibuka di internet.

### 17. Backtest, event study & paper trading

- **Mulai arsipkan berita dari Fase 1** — arsip sendiri dengan `first_seen_at` adalah data backtest paling jujur.
- **Event study:** untuk tiap kategori/event, ukur pergerakan harga +1m, +5m, +15m, +60m setelah `first_seen_at`, terhadap arah prediksi. Hasilnya untuk mengkalibrasi bobot, half-life, threshold, dan z-score playbook.
- **Backtest** sinyal dengan spread & slippage realistis (spread emas melebar saat berita), walk-forward, tanpa lookahead.
- **Paper trading tracker:** setiap sinyal otomatis dievaluasi terhadap tick: TP/SL/expired, MFE/MAE, durasi. Laporan harian & mingguan Bahasa Indonesia.
- **Minimal 2–4 minggu paper trading** dengan hasil tercatat sebelum mempertimbangkan uang sungguhan. Tampilkan jika hasil belum layak (jangan dipoles).

### 18. Operasional 24/7

- Docker Compose: `collector`, `analyzer`, `signal_engine`, `notifier`, `api` (dashboard), `scheduler`, `redis`, `postgres`. `restart: unless-stopped`, healthcheck tiap service.
- Watchdog: jika sumber diam > X menit, websocket putus, tick basi, atau antrian menumpuk → alert admin di Telegram.
- Sinkronisasi waktu NTP (chrony) — wajib, karena pengukuran latensi bergantung pada jam yang akurat.
- Jika MT5 dipakai: buat service `mt5_bridge` di Windows yang meneruskan tick/candle ke core (Redis atau HTTP/ZeroMQ), atau jalankan seluruh stack di Windows. Jelaskan opsi & trade-off ke saya.
- Lokasi VPS: ukur latensi ke sumber berita dan ke server broker, lalu sarankan lokasi berdasarkan data.
- Logging terstruktur (`structlog`) + rotasi log. Mode **replay**: memutar ulang satu hari rekaman berita + tick dengan kecepatan dipercepat untuk uji end-to-end.
- Graceful degradation: LLM mati → rules-only; feed harga utama mati → adapter cadangan + label; semua tercatat & diberitahukan.

### 19. Struktur project

```
gold-news-agent/
├── CLAUDE.md                  # memori project: keputusan, cara run, konvensi
├── README.md                  # Bahasa Indonesia: setup, run, troubleshooting
├── .env.example
├── pyproject.toml             # dikelola dengan uv
├── docker-compose.yml
├── config/
│   ├── settings.yaml          # bobot, threshold, sesi, budget, jam tenang
│   ├── sources.yaml
│   ├── keywords.yaml
│   ├── gold_drivers.yaml
│   └── fed_officials.yaml
├── prompts/                   # system prompt LLM (versi tercatat)
├── src/gold_agent/
│   ├── collectors/            # telegram, rss, http, websocket, official
│   ├── market/                # adapters (mt5, twelvedata, binance_paxg, history), candles, indicators
│   ├── calendar/              # sync kalender, parser rilis, playbook
│   ├── processing/            # normalizer, dedup, prefilter
│   ├── analysis/              # llm_client, triage, deep, verification, price_led
│   ├── signals/               # scoring, rules, risk, sizing, outlook
│   ├── timeutils/             # sesi & DST
│   ├── notify/                # telegram bot, templates
│   ├── api/                   # FastAPI + websocket + frontend statis
│   ├── storage/               # models, repositories, migrations
│   ├── evaluation/            # event study, backtest, paper tracker, reports
│   ├── research_agent/        # (opsional) Claude Agent SDK
│   └── ops/                   # health, watchdog, metrics, replay
├── scripts/                   # verify_sources.py, backfill_history.py, replay_day.py
├── tests/                     # unit, integration, fixtures (rekaman nyata yang dianonimkan)
└── docs/                      # source_report.md, architecture.md, runbook.md
```

### 20. Tech stack

Python 3.12, `uv`, `asyncio`, `httpx`, `websockets`, `feedparser`, `trafilatura`, `telethon`, `python-telegram-bot`, `pydantic` v2, `pydantic-settings`, `sqlalchemy` 2 + `alembic`, `redis`, `apscheduler`, `pandas`, `numpy`, `rapidfuzz`, `sentence-transformers`, `anthropic`, `claude-agent-sdk` (opsional), `MetaTrader5` (opsional, Windows), `fastapi`, `uvicorn`, `structlog`, `pytest`, `pytest-asyncio`, `ruff`, `pyright`. Pakai Context7 untuk memastikan API setiap library sesuai versi terbaru sebelum menulis kode.

### 21. Standar kode & testing

- Type hints lengkap, pydantic untuk semua data lintas komponen, fungsi kecil & mudah dites.
- Ruff + pyright bersih sebelum commit. Pre-commit hook.
- Test wajib: konversi sesi & DST (termasuk tanggal transisi), parser headline rilis data (banyak variasi format), dedup, scoring & hysteresis, sizing lot, validasi skema LLM, fallback saat LLM/sumber mati, replay end-to-end satu hari.
- Fixture dari data nyata yang direkam, bukan data karangan.
- Setiap konstanta penting (bobot, threshold, half-life) ada di config, bukan di kode.

### 22. Fase pengerjaan & kriteria selesai

Kerjakan per fase. Di akhir tiap fase: jalankan test, tunjukkan demo/output nyata, commit, tulis ringkasan singkat di `CLAUDE.md`, lalu lanjut. **Berhenti dan minta persetujuan saya di akhir Fase 0 dan sebelum Fase 8.**

| Fase | Isi | Selesai jika |
|---|---|---|
| **0. Discovery** | Tanya pertanyaan bagian 3; init repo (uv, ruff, pytest, pre-commit, CLAUDE.md); `verify_sources.py` + `docs/source_report.md`; rekomendasi daftar sumber final | Laporan sumber berisi status & latensi terukur; saya menyetujui daftar sumber & config |
| **1. Ingestion** | Collector async, registry, normalisasi, dedup/cluster, Redis stream, DB, metrik latensi, arsip berita | Jalan 2 jam tanpa crash; test dedup lulus; latensi per sumber tercatat |
| **2. Pasar & waktu** | Adapter harga, candle, indikator, konteks DXY/yield, modul sesi + DST, sync kalender + countdown | Test DST lulus; candle cocok dengan sumber referensi; tick basi terdeteksi |
| **3. Analisis** | Prefilter, parser rilis, triage & deep LLM, knowledge base, verifikasi silang, price-led detection | Pada set 100+ headline berlabel (kita buat bersama): presisi relevansi ≥ 0,9, akurasi arah dilaporkan jujur; p95 triage < 5 dtk |
| **4. Sinyal & jam** | Skor gabungan, aturan, risiko & sizing, outlook 24 jam, playbook event, jam rawan, mode akhir pekan | Sinyal lengkap sesuai skema; hysteresis & blackout teruji |
| **5. Output** | Bot Telegram + perintah, dashboard 5 halaman | Diuji dengan Playwright; tampilan HP rapi; notifikasi sampai dengan latensi tercatat |
| **6. Evaluasi** | Event study, backtest, paper tracker, laporan harian/mingguan | Laporan pertama keluar; bobot awal dikalibrasi dari data |
| **7. Deploy 24/7** | Docker Compose, watchdog, NTP, backup, runbook, mode replay | Berjalan 72 jam tanpa intervensi; alert kesehatan teruji dengan mematikan sumber secara sengaja |
| **8. Eksekusi (opsional)** | Order MT5 demo-only, kill switch, konfirmasi manual | Hanya setelah saya setuju dan paper trading memadai |

### 23. Cara kamu bekerja

1. Mulai di **plan mode**: baca seluruh prompt, susun rencana, tunjukkan ke saya.
2. Pakai task list untuk melacak progres per fase.
3. Pakai **Context7** setiap kali memakai library eksternal; pakai **feature-dev** untuk fitur besar; jalankan **code-review** di akhir tiap fase; **security-guidance** aktif untuk mencegah kebocoran key.
4. Minta API key/keputusan ke saya saat benar-benar dibutuhkan; jangan menebak hal yang mahal untuk diulang.
5. Jika ada bagian prompt ini yang ternyata tidak mungkin, berbahaya, melanggar ToS, atau ada cara yang jelas lebih baik — katakan terus terang dan usulkan alternatif. Jangan diam-diam menurunkan kualitas.
6. Catat setiap keputusan arsitektur & alasannya di `CLAUDE.md` dan `docs/architecture.md`.

### 24. Definition of Done keseluruhan

- Sistem berjalan 24/7, pulih otomatis dari error, dan memberi tahu saya jika ada masalah.
- Berita relevan dianalisis dan diberi arah/dampak dalam hitungan detik sejak diterima, dengan latensi setiap tahap terukur dan terlihat.
- Sinyal BUY/SELL/WAIT lengkap (entry, SL, TP, masa berlaku, alasan, pembatal) dan outlook 24 jam per jam WIB terkirim ke Telegram dan tampil di dashboard.
- Setiap sinyal dievaluasi otomatis, dengan laporan performa yang jujur per kategori, jam, dan sesi.
- README Bahasa Indonesia yang cukup jelas untuk menjalankan ulang semuanya dari nol.

---

## C. TAMBAHAN & KOREKSI HASIL REVIEW FASE 0

> Jika bagian A/B berbeda dengan bagian C, **bagian C yang berlaku**. Alasan tiap keputusan ada di
> `docs/architecture.md`.

### C1. Koreksi teknis

1. **API Claude (dicek Okt 2026).** Model ID `claude-haiku-5-5`, `claude-sonnet-5-5`, dan
   `claude-opus-5-5` valid. Harga per 1 juta token input/output: Haiku 5.5 $0,10/$0,50, Sonnet 5.5
   $2/$10, Opus 5.5 $4/$20.
   - Bagian 13.1/13.3: **jangan memaksa tool** (`tool_choice` `any`/`tool` ditolak HTTP 400 oleh
     Sonnet 5.5 & Opus 5.5). Gunakan structured outputs (`output_config.format` /
     `messages.parse()` + pydantic), atau `tool_choice: auto` + `strict: true`.
   - Triage dengan Haiku 5.5 memakai `effort: low` dan thinking mati. Sonnet 5.5 tidak bisa
     `thinking: disabled`; Opus 5.5 selalu berpikir.
   - Prefill assistant tidak didukung. Tangani `stop_reason: "refusal"` sebagai kegagalan → fallback
     aturan.
   - Minimum prefix yang bisa di-cache 512 token; pantau `cache_read_input_tokens`.
2. **Database (bagian 14).** Arsitektur multi-proses + SQLite akan saling kunci. Pakai PostgreSQL
   (+TimescaleDB) sejak Fase 1. SQLite hanya untuk test/replay.
3. **Embedding (7.3).** `sentence-transformers` menarik PyTorch (GB). Mulai dengan rapidfuzz.
   Tambahkan embedding hanya jika terukur perlu, lewat runtime ONNX (mis. `fastembed`) dengan model
   yang sama. Verifikasi dulu.
4. **MT5 (6.1).** Waktu bar/tick MT5 adalah **jam server broker** (sering UTC+2/+3 mengikuti DST
   AS), bukan UTC. Offset dideteksi otomatis lalu dikonversi.
5. **Jeda harian (11.1/11.5).** Hampir semua broker menghentikan XAUUSD 17:00–18:00 New York
   (04:00–05:00 WIB saat DST AS, 05:00–06:00 WIB saat winter). Ini **pasar tutup**, bukan sekadar
   likuiditas tipis: tidak ada sinyal dan tidak ada alarm tick basi.
6. **DST (11.1).** Selain jendela musim gugur 2026 (UK 25 Okt, AS 1 Nov, yaitu 12 hari dari Fase 0),
   test juga jendela musim semi 2027 (AS 14 Mar, UK 28 Mar). Saat jendela transisi, overlap
   London–NY menjadi 4 jam (19:00–23:00 WIB).
7. **Binance (6.1).** `api.binance.com` menolak IP AS (HTTP 451). Gunakan endpoint market-data
   `data-api.binance.vision` / `data-stream.binance.vision` (wajib diverifikasi). PAXG
   diperdagangkan 24/7, jadi gerakannya di akhir pekan dipakai sebagai **estimator gap Senin** (11.6).
8. **DXY & yield (6.1/9).** Jika DXY tidak tersedia, hitung DXY sintetis dari 6 pasangan FX (rumus
   ICE di `docs/architecture.md` ADR-008). FRED hanya harian. Untuk yield intraday pakai CFD
   obligasi broker, feed berbayar, atau proxy TLT/IEF (jam AS). Jika tidak ada, beri label
   "yield harian saja".
9. **Z-score surprise (7.5/11.4).** Feed Forex Factory gratis hanya berisi minggu ini, tanpa riwayat
   konsensus. Standar deviasi surprise historis tidak tersedia gratis. Mulai dengan nilai
   `surprise_std` per event di config yang diberi label **perkiraan awal**. Kumpulkan sendiri
   forecast + actual setiap minggu sejak Fase 1, lalu ganti dengan nilai terukur setelah ≥ 12
   observasi per event. Opsi berbayar: penyedia kalender dengan riwayat (cek harga/ToS).
10. **Forex Factory (6.3).** Feed JSON-nya tidak resmi dan ToS-nya belum jelas. Jadwal otoritatif
    diambil dari sumber resmi (BLS iCal, BEA, kalender FOMC, TreasuryDirect). FF hanya untuk
    forecast/previous.
11. **Blackout Fed.** Pejabat Fed tidak berbicara soal kebijakan mulai tengah malam ET pada Sabtu
    kedua sebelum rapat FOMC sampai tengah malam ET sehari setelah rapat selesai. Tandai periode ini: "Fed speak" yang muncul saat
    blackout patut dicurigai, dan artikel "Fed whisperer" lebih berbobot.
12. **X API (A3).** Harga per-post tidak diasumsikan; cek developer.x.com sebelum diaktifkan.

### C2. Keamanan (tambahan wajib)

1. **Prompt injection.** Teks berita/channel adalah data tak tepercaya.
   - Teks hanya masuk ke blok user berdelimiter dan tidak pernah ke system prompt.
   - Output hanya lewat schema, dan LLM tidak punya tool yang bertindak.
   - Satu sumber yang belum terkonfirmasi dibatasi bobotnya.
   - Set evaluasi Fase 3 memuat headline injection.
2. **Bot Telegram.** Perintah (`/stop`, `/mute`, dst.) hanya diterima dari
   `TELEGRAM_ADMIN_USER_IDS` dan chat yang dikonfigurasi; selain itu diabaikan + dicatat. `/stop`
   harus tetap berfungsi saat LLM mati. Jika Hermes Agent juga memakai Telegram, gunakan **bot token
   terpisah** (long-polling ganda menyebabkan HTTP 409).
3. **Telethon.** File `*.session` setara password akun: gitignore, chmod 600, tidak masuk image
   Docker, dan backup dienkripsi. Pakai akun Telegram khusus pembaca; jangan akun utama.
4. **Dashboard.** Jangan dibuka langsung ke internet. Pakai Tailscale atau Cloudflare Tunnel
   (Access), atau reverse proxy dengan TLS + login + rate limit.
5. **Repo.** Fork `hermes-agent` bersifat **publik**. Pindahkan project ke repo private sebelum
   Fase 1 (modal, chat ID, daftar channel, dan rekaman berita tidak boleh publik). Pre-commit
   menjalankan gitleaks.

### C3. Data & evaluasi (tambahan)

1. Sumber konteks tambahan:
   - CFTC Commitments of Traders (posisi managed money emas, mingguan) dengan driver `positioning`.
   - Kepemilikan GLD harian.
   - Cadangan emas PBoC (bulanan, sekitar tanggal 7).
   - (Opsional) premi Shanghai Gold Exchange.
2. **Kejujuran statistik.**
   - Win rate ditampilkan dengan interval Wilson 95%, expectancy dengan bootstrap CI.
   - Bobot tidak dikalibrasi sebelum ≥ 100 sinyal (atau ≥ 30 per kategori). Sebelum itu tampilkan
     "sampel belum cukup".
   - Walk-forward + holdout; tidak boleh tuning di data test.
3. **Set label 100+ headline (Fase 3).**
   - Diambil dari arsip Fase 1 (headline nyata), lewat CLI `scripts/label_headlines.py`.
   - Sebagian dilabel dua kali untuk mengukur konsistensi label.
   - Wajib memuat contoh injection dan rumor.
4. **Event study.** Event study yang jujur hanya bisa memakai arsip sendiri (`first_seen_at`).
   Timestamp berita historis dari pihak ketiga tidak andal, jadi kekuatan statistiknya bertambah
   seiring waktu.

### C4. Operasional (tambahan)

1. Ekspor offset chrony sebagai metrik; beri alert jika |offset| > 100 ms.
2. Redis: persistence AOF, consumer group + `XAUTOCLAIM` untuk pesan menggantung, trimming
   `MAXLEN`.
3. TimescaleDB: kompresi + retensi tick. Hitung kebutuhan disk dari laju tick nyata.
4. Backup `pg_dump` harian ke lokasi lain. Uji restore sebulan sekali.
5. **Lokasi VPS.** Jalankan `verify_sources.py --watch-minutes 120` dari 2–3 kandidat region, ukur
   ping ke server broker, lalu pilih berdasarkan data.
6. **Lingkungan cloud Claude Code.** Network policy default memblokir host sumber. Agar Claude Code
   bisa memverifikasi sumber dari sesi cloud, izinkan domain di `config/sources.yaml` (atau akses
   penuh) di pengaturan environment. Cara lain: jalankan skrip di VPS/PC lalu commit laporannya.

### C5. Regulasi

Untuk pemakaian pribadi tidak ada masalah. Jika sinyal dibagikan ke orang lain (grup, berbayar,
copy-trading), cek dulu regulasi di Indonesia (Bappebti/OJK) sebelum melakukannya.

### C6. Pertanyaan tambahan Fase 0 (melengkapi bagian 3)

9. Broker & tipe akun: spread normal XAUUSD, contract size, zona waktu server, dan lokasi server.
10. Buat repo private `gold-news-agent` dan pindahkan project ke sana? *(default: ya)*
11. Sinyal hanya untuk kamu, atau dibagikan ke orang lain? *(default: pribadi)*
12. Email kontak untuk User-Agent (boleh alamat khusus), karena dibutuhkan BLS.
13. Verifikasi sumber dijalankan di mana: buka akses jaringan environment cloud ini, atau jalankan
    di VPS/PC kamu?
