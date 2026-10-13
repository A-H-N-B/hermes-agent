# Gold News Agent (XAUUSD)

Sistem pemantau berita emas 24/7 yang menganalisis dampak berita ke XAUUSD dan memberi rekomendasi
BUY / SELL / WAIT beserta outlook per jam (WIB). Hasilnya dikirim ke Telegram dan dashboard.

> **Bukan saran keuangan.** Ini sistem pendukung keputusan. Mode default `SIGNAL_ONLY`, dan eksekusi
> order tidak tersedia sampai Fase 8 disetujui dan paper trading memadai.

## Status

**Fase 0 (discovery), menunggu persetujuan.** Isinya: scaffold project, config tervalidasi, skrip
verifikasi sumber, dan keputusan arsitektur. Detail per fase ada di `CLAUDE.md`. Spesifikasi lengkap
ada di `PROMPT_XAUUSD_NEWS_AGENT.md`, dan bagian C berisi tambahan hasil review Fase 0.

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

## Test & kualitas kode

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

## Struktur

```
config/      settings.yaml (perilaku), sources.yaml (registry sumber), keywords.yaml,
             gold_drivers.yaml (knowledge base), fed_officials.yaml (diisi dari situs resmi)
src/gold_agent/
  config.py  pemuat + validasi config dan .env
  ops/       source_check.py (verifikasi), source_report.py (laporan)
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
