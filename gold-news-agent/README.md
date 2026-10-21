# Gold News Agent (XAUUSD)

Sistem pemantau berita emas 24/7 yang menganalisis dampak berita ke XAUUSD dan memberi rekomendasi
BUY / SELL / WAIT beserta outlook per jam (WIB). Hasilnya dikirim ke Telegram dan dashboard.

> **Bukan saran keuangan.** Ini sistem pendukung keputusan. Mode default `SIGNAL_ONLY`, dan eksekusi
> order tidak tersedia sampai Fase 8 disetujui dan paper trading memadai.

## Status

| Fase | Isi | Status |
|---|---|---|
| 0 | Scaffold, config tervalidasi, verifikasi sumber, keputusan arsitektur | selesai |
| 1 | Ingestion: collector RSS/JSON/Telegram, dedup, arsip, latensi | selesai |
| 2 | Harga & waktu: sesi/DST, feed harga, candle, indikator, kalender ekonomi | selesai |
| 3 | Analisis: prefilter, fast path rilis data, triage LLM, verifikasi, lonjakan harga | selesai |
| 4 | Sinyal BUY/SELL/WAIT, outlook 24 jam per jam WIB, playbook event, mode akhir pekan | selesai |
| 5 | Telegram (notifikasi + perintah bot) dan dashboard web | selesai |
| 6 | Paper trading otomatis, event study, backtest walk-forward, laporan harian/mingguan | selesai |
| 7 | Deploy 24/7 (Docker Compose, watchdog, replay, backup, runbook) | selesai di kode; burn-in 72 jam menunggu VPS |
| 8 | Eksekusi order MT5 | **tidak dibangun** (butuh persetujuan + paper trading ≥ 2–4 minggu) |

Semua sumber di `config/sources.yaml` masih `candidate`: belum ada yang diverifikasi dari jaringan
produksi, jadi collector belum menarik berita sampai `verify-sources` dijalankan di VPS. Detail per
fase ada di `CLAUDE.md`. Spesifikasi lengkap ada di `PROMPT_XAUUSD_NEWS_AGENT.md` (bagian C berisi
tambahan hasil review Fase 0).

## Cara kerja sinyal (ringkas)

- Skor gabungan `S = 0,35·Berita + 0,20·Makro + 0,25·Teknikal + 0,20·Konfirmasi harga`, dikali
  faktor likuiditas sesi. BUY/SELL hanya jika |S| ≥ 0,35 dan confidence ≥ 60.
- Selalu WAIT jika: pasar tutup/jeda harian, harga basi, spread lebar, 15 menit sebelum rilis
  high-impact sampai 60 detik sesudahnya, batas rugi harian / jumlah sinyal tercapai, berita dan
  harga bertentangan kuat, atau `/stop`.
- Berita `needs_confirmation` (sumber tunggal) tidak pernah menjadi dasar sinyal: skor tanpa berita
  itu juga harus lolos ambang.
- Pembalikan arah butuh |S| ≥ 0,50 dan ≥ 15 menit sejak sinyal terakhir (hysteresis); ada cooldown
  10 menit antar-sinyal.
- Lot hanya dihitung jika `risk.account_equity` dan contract size broker diketahui. Tidak pernah
  ditebak.
- Outlook 24 jam dibuat tiap jam (dan saat berita penting); jam > 4 jam ke depan ditulis bersyarat.
  Minggu 16:00 WIB ada "outlook pembukaan Senin" dengan estimasi gap dari PAXG.

Semua angka di atas ada di `config/settings.yaml` dan bisa diubah tanpa mengubah kode: tulis
nilai sendiri di `config/local.yaml` (tidak ikut git) atau `uv run gold-agent config set KEY NILAI`.

## Prasyarat

- Python 3.12 dan [uv](https://docs.astral.sh/uv/)
- Jam sistem tersinkron NTP (chrony). Pengukuran latensi bergantung pada jam yang akurat.
- Akses internet keluar ke host sumber berita (lihat `config/sources.yaml`)

## Setup

Pasang di komputer sendiri (Windows/macOS/Linux, tanpa Docker, langkah demi langkah):
**`docs/pasang_di_komputer.md`**. Di VPS dengan Docker Compose: `docs/runbook.md`.

```bash
uv sync
cp .env.example .env        # isi key yang kamu punya; .env tidak pernah di-commit
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Isi juga email kontak: `uv run gold-agent config set http.contact_email emailmu@contoh.com`. BLS
menolak bot tanpa alamat kontak di User-Agent.

## Verifikasi sumber (Fase 0)

```bash
# satu putaran: status HTTP, format, item terbaru, ETag/304, robots.txt, lag WebSocket
uv run python scripts/verify_sources.py --note "VPS Singapura"

# ukur latensi sumber (first_seen − published) selama 2 jam, idealnya mencakup rilis data AS
uv run python scripts/verify_sources.py --watch-minutes 120 --note "VPS Singapura"

# hanya sumber tertentu / simpan body mentah untuk fixture test (folder data/ tidak di-commit)
uv run python scripts/verify_sources.py --only fed_press_all,bls_latest --record data/recordings
```

Hasilnya ditulis ke `docs/source_report.md` dan `docs/source_report.json`. Sumber yang lolos dan ToS
-nya sudah dibaca diubah menjadi `status: verified` di `config/sources.yaml`. Collector hanya
menjalankan sumber `verified`.

## Menjalankan

```bash
uv run gold-agent db upgrade          # migrasi database (DATABASE_URL di .env; default SQLite)
uv run gold-agent run all             # semua service dalam satu proses (bus memori)
uv run gold-agent run collector market calendar analyzer signal paper evaluator notifier dashboard watchdog --bus redis
uv run gold-agent healthcheck signal                 # exit 0 bila heartbeat < 3 menit
uv run gold-agent db backup                          # salinan SQLite ke backups/ (14 terbaru)
uv run gold-agent labels export --limit 150          # headline arsip -> data/labels/todo.jsonl
uv run gold-agent labels import data/labels/todo.jsonl --labeler kamu
uv run gold-agent eval-triage [--llm]                # presisi/recall triage -> docs/eval_triage.md
uv run gold-agent replay 2026-10-13 --from 19:00 --to 22:00   # putar ulang satu hari rekaman
uv run gold-agent mt5-bridge                         # di Windows: MT5 -> Redis core (opsional)
```

## Deploy 24/7

Produksi memakai Docker Compose di satu VPS Linux: satu container per service, PostgreSQL 16,
Redis (AOF), migrasi sekali jalan, healthcheck heartbeat, dan `restart: unless-stopped`. Hanya
dashboard yang dipublikasikan, dan hanya di `127.0.0.1` host. Langkah lengkap ada di
**`docs/runbook.md`**: memilih lokasi VPS dari data, instalasi, tabel alert dan tindakannya,
fire drill, backup/restore, MT5 bridge, replay, dan checklist burn-in 72 jam.

```bash
cp .env.example .env && chmod 600 .env    # isi key + POSTGRES/REDIS/DASHBOARD password
docker compose build && docker compose up -d
docker compose ps
```

Watchdog mengirim alert ke Telegram bila sumber mati, service berhenti, antrian menumpuk, jam
melenceng > 100 ms, atau disk hampir penuh, dan mengirim kabar saat pulih. Tiap malam ia
membersihkan tick > 30 hari dan snapshot konteks > 180 hari.

**Replay** memutar arsip satu hari melewati pipeline sungguhan ke database terpisah. Hasilnya
deterministik dan sama pada kecepatan berapa pun: jam replay berhenti selama pipeline bekerja.

## Telegram

1. Buat bot lewat @BotFather (token **khusus** untuk sistem ini; jangan dipakai bersama Hermes
   Agent, dua proses long-polling satu token saling bentrok).
2. Isi `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (chat tujuan notifikasi) dan
   `TELEGRAM_ADMIN_USER_IDS` (ID akunmu, pisahkan dengan koma).
3. Jalankan service `notifier` (ikut di `run all`).

Prioritas: 🔴 langsung & menembus jam tenang (sinyal, FLASH rilis data, playbook, berita dampak
≥ 4, lonjakan harga, alert kritis); 🟡 normal (ditahan saat 00:00–05:00 WIB atau `/mute`, lalu
dikirim sebagai ringkasan); 🔵 ringkasan berkala. Perintah hanya dilayani untuk admin, di chat yang
dikonfigurasi atau chat pribadi admin dengan bot:

| Perintah | Fungsi |
|---|---|
| `/status` | harga, sesi, status sinyal + alasan WAIT, event berikutnya |
| `/signal` | sinyal aktif (format lengkap) atau sinyal terakhir |
| `/outlook` | outlook 24 jam terbaru |
| `/calendar` | event high/medium 48 jam ke depan |
| `/stats` | performa paper trading (dengan interval keyakinan) |
| `/mute 1h` · `/mute off` | bisukan 🟡/🔵 (🔴 tetap masuk) |
| `/stop` · `/resume` | kill switch sinyal baru (tetap jalan walau LLM mati) |

Tanpa token, notifikasi tetap dicatat dan terlihat di dashboard.

## Dashboard

`run all` (atau `run dashboard`) membuka dashboard di `http://127.0.0.1:8787`: Live (kartu sinyal,
grafik dengan marker berita & sinyal, feed berita), Outlook 24 jam, Kalender + playbook, Performa,
Kesehatan sistem. Akses dari HP lewat Tailscale / Cloudflare Tunnel / SSH tunnel, **jangan** buka
port langsung ke internet. Jika `dashboard.host` bukan localhost, `DASHBOARD_PASSWORD` wajib diisi
(service menolak start tanpa itu).

## Evaluasi & laporan

Setiap sinyal otomatis di-*paper trade* oleh service `paper` dengan harga dari sumber yang sama:
isi di ask/bid + slippage, 50% ditutup di TP1 lalu stop ke breakeven, sisanya ke TP2, dan
kedaluwarsa jika TP1 tidak tersentuh dalam masa berlaku. Hasil (R, MFE/MAE, durasi) tersimpan
dan ikut menghitung batas rugi harian. Service `evaluator` tiap pagi (05:30 WIB) menjalankan
event study, memperbarui reliabilitas sumber, lalu mengirim laporan harian, dan tiap Sabtu
laporan mingguan. Laporan juga tersimpan di `data/reports/`.

```bash
uv run gold-agent report harian [--date 2026-10-13] [--llm]   # laporan + ringkasan AI opsional
uv run gold-agent report mingguan
uv run gold-agent event-study --days 90     # gerak +1/+5/+15/+60 mnt setelah berita
uv run gold-agent backtest --days 30 --folds 4   # replay snapshot tersimpan, walk-forward
uv run gold-agent calibrate                 # usulan bobot (butuh ≥ 100 sinyal tertutup)
```

Aturan kejujuran: win rate selalu dengan interval 95%, expectancy dengan CI bootstrap, dan sampel
kecil ditulis "sampel belum cukup". Bobot skor **tidak pernah** diubah otomatis: `calibrate` hanya
membuat usulan yang harus lebih baik di data uji (holdout) dan kamu terapkan sendiri di config.
Backtest memakai snapshot pasar dan setiap versi analisis berita sesuai waktu terbitnya, jadi
tidak ada informasi dari masa depan yang ikut terpakai.

## Test & kualitas kode

```bash
uv run pytest -q
uv run pytest -q -m slow          # replay satu hari penuh end-to-end (±2 menit)
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

Test integrasi PostgreSQL/Redis ikut berjalan jika `GOLD_TEST_POSTGRES_URL` dan
`GOLD_TEST_REDIS_URL` di-set.

## Struktur

```
config/      settings.yaml (perilaku), sources.yaml (registry sumber), local.yaml +
             sources.local.yaml (milik komputer ini, tidak ikut git), keywords.yaml,
             gold_drivers.yaml (knowledge base), events.yaml (rilis data AS), fed_officials.yaml
prompts/     system prompt LLM (triage, deep, outlook, brief), berversi
src/gold_agent/
  config.py        pemuat + validasi config dan .env
  core/            bus (Redis Streams / memori), clock, supervisi service, logging
  collectors/      RSS/JSON poller, Telegram (Telethon)
  processing/      normalisasi, dedup/cluster, prefilter kata kunci
  analysis/        triage LLM, aturan fallback, verifikasi silang, lonjakan harga
  econ_calendar/   kalender (FF + FOMC), parser rilis (fast path), playbook, kalibrasi surprise
  market/          adapter harga (Binance PAXG, Twelve Data, MT5), candle, indikator, level
  timeutils/       sesi pasar & DST, jam rawan
  signals/         skor gabungan, SL/TP & lot, mesin sinyal, outlook 24 jam, profil per jam
  notify/          template Telegram, kebijakan prioritas, dispatcher, perintah bot
  dashboard/       FastAPI + WebSocket, halaman statis (JS polos, CSP ketat), grafik vendored
  services/        collector, market, calendar, analyzer, signal, paper, evaluator, notifier,
                   dashboard, watchdog
  storage/         SQLAlchemy async + migrasi Alembic
  evaluation/      label & evaluasi triage, paper trading, performa, event study, reliabilitas,
                   kalibrasi bobot, backtest walk-forward, laporan
  ops/             verifikasi sumber, health, metrik, redaksi secret, watchdog, offset jam,
                   replay
scripts/     verify_sources.py, backup.sh, restore_check.sh, clock_offset.sh
deploy/      unit systemd: offset jam, backup malam, mode native tanpa Docker
tests/       test perilaku
docs/        architecture.md (keputusan), runbook.md (operasional), source_report.md
Dockerfile, docker-compose.yml, docker-compose.mt5-bridge.yml
```

## Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Semua sumber `403 Forbidden` / "ditolak proxy" | Jaringan host memblokir domain tersebut (mis. container cloud dengan network policy). Jalankan di VPS/PC, atau izinkan domainnya. |
| `bls_*` HTTP 403 | Isi `http.contact_email` (`gold-agent config set http.contact_email ...`). |
| Binance HTTP 451 | IP berada di AS. Pakai endpoint `*.binance.vision` (sudah default) atau VPS di luar AS. |
| Error sertifikat SSL di balik proxy kantor | Set `SSL_CERT_FILE` ke bundle CA proxy tersebut. Jangan pernah mematikan verifikasi TLS. |
| Sumber `skipped: butuh ... di .env` | Isi key terkait di `.env`. |
| `newest` berumur negatif / lag negatif | Jam atau zona waktu sumber salah, atau jam host tidak sinkron. Cek `chronyc tracking`. |
