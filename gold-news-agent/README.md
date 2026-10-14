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
| 6–7 | Evaluasi paper trading, deploy 24/7 | dikerjakan |
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

Semua angka di atas ada di `config/settings.yaml` dan bisa diubah tanpa mengubah kode.

## Prasyarat

- Python 3.12 dan [uv](https://docs.astral.sh/uv/)
- Jam sistem tersinkron NTP (chrony). Pengukuran latensi bergantung pada jam yang akurat.
- Akses internet keluar ke host sumber berita (lihat `config/sources.yaml`)

## Setup

```bash
uv sync
cp .env.example .env        # isi key yang kamu punya; .env tidak pernah di-commit
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Isi juga `http.contact_email` di `config/settings.yaml`. BLS menolak bot tanpa alamat kontak di
User-Agent.

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
uv run gold-agent run collector market calendar analyzer signal notifier dashboard --bus redis
uv run gold-agent labels export --limit 150          # headline arsip -> data/labels/todo.jsonl
uv run gold-agent labels import data/labels/todo.jsonl --labeler kamu
uv run gold-agent eval-triage [--llm]                # presisi/recall triage -> docs/eval_triage.md
```

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

## Test & kualitas kode

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

Test integrasi PostgreSQL/Redis ikut berjalan jika `GOLD_TEST_POSTGRES_URL` dan
`GOLD_TEST_REDIS_URL` di-set.

## Struktur

```
config/      settings.yaml (perilaku), sources.yaml (registry sumber), keywords.yaml,
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
  services/        collector, market, calendar, analyzer, signal, notifier, dashboard
  storage/         SQLAlchemy async + migrasi Alembic
  evaluation/      label headline, evaluasi triage
  ops/             verifikasi sumber, health, metrik, redaksi secret
scripts/     verify_sources.py
tests/       test perilaku
docs/        architecture.md (keputusan), source_report.md (hasil verifikasi)
```

## Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Semua sumber `403 Forbidden` / "ditolak proxy" | Jaringan host memblokir domain tersebut (mis. container cloud dengan network policy). Jalankan di VPS/PC, atau izinkan domainnya. |
| `bls_*` HTTP 403 | Isi `http.contact_email` di `settings.yaml`. |
| Binance HTTP 451 | IP berada di AS. Pakai endpoint `*.binance.vision` (sudah default) atau VPS di luar AS. |
| Error sertifikat SSL di balik proxy kantor | Set `SSL_CERT_FILE` ke bundle CA proxy tersebut. Jangan pernah mematikan verifikasi TLS. |
| Sumber `skipped: butuh ... di .env` | Isi key terkait di `.env`. |
| `newest` berumur negatif / lag negatif | Jam atau zona waktu sumber salah, atau jam host tidak sinkron. Cek `chronyc tracking`. |
