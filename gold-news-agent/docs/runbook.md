# Runbook — Gold News Agent

> Sistem pendukung keputusan, **bukan jaminan profit**. Mode bawaan `SIGNAL_ONLY`: tidak ada
> order yang dikirim ke broker. Eksekusi (Fase 8) belum dibuat dan butuh persetujuan eksplisit.

Dokumen ini untuk orang yang menjalankan sistem 24/7 di VPS: memasang, memeriksa, menangani
alert, backup/restore, dan uji 72 jam sebelum Fase 7 dinyatakan selesai. Alasan di balik
keputusan teknis ada di `docs/architecture.md`.

## 1. Gambaran

```
VPS Linux (Docker Compose)
  postgres ── semua state: berita, analisis, sinyal, hasil paper, notifikasi, heartbeat
  redis ───── bus antar-service (Redis Streams, AOF)
  migrate ─── sekali jalan: `db upgrade` sebelum service lain mulai
  collector ─ RSS/JSON + channel Telegram  → news.raw
  market ──── harga (Binance PAXG / Twelve Data / MT5 bridge), candle, konteks pasar
  calendar ── kalender ekonomi, FOMC, playbook T-60/T-30
  analyzer ── prefilter, parser rilis (fast path), triage Claude, deep analysis, lonjakan harga
  signal ──── skor gabungan → sinyal BUY/SELL/WAIT, outlook 24 jam
  paper ───── paper trading tiap sinyal (TP/SL/expired, R)
  evaluator ─ laporan harian/mingguan, event study, kalibrasi (usulan)
  notifier ── Telegram + perintah bot
  dashboard ─ web, hanya di 127.0.0.1:8787 host
  watchdog ── kesehatan sumber/service/antrian/jam/disk + retensi data tiap malam
Windows (opsional): mt5-bridge → Redis lewat Tailscale/WireGuard
```

Setiap service menulis heartbeat tiap 30 detik. Docker menandai container `unhealthy` bila
heartbeat lebih tua dari 3 menit (`gold-agent healthcheck <service>`), dan watchdog mengirim
alert ke Telegram.

## 2. Lokasi VPS: ukur dulu, jangan menebak

Spesifikasi minimum: 2 vCPU, 4 GB RAM, 40 GB SSD, Ubuntu 24.04 LTS atau Debian 12.

Kandidat region:

| Region | Kelebihan | Kekurangan |
|---|---|---|
| US East (Virginia/New York) | Dekat sumber data AS (Fed, BLS, BEA, Treasury) dan sebagian besar feed | Jauh dari kamu (dashboard sedikit lambat, tidak masalah) |
| London/Frankfurt | Dekat server broker MT5 (umumnya LD4/Equinix) dan sumber Eropa | Sumber AS +70–90 ms |
| Singapura/Jakarta | Dekat kamu | Paling jauh dari sumber berita; tidak disarankan |

Cara memilih (C4.5): di tiap kandidat jalankan

```bash
docker compose run --rm -v ./docs:/app/docs collector \
  verify-sources --watch-minutes 120 --note "VPS <region>"
```

Lalu bandingkan `docs/source_report.md`: lag p50/p95 sumber tier 1. Jika memakai MT5, ukur juga
ping ke server broker. Pilih region dengan lag tier 1 terendah. Bila selisihnya kecil, pilih yang
dekat broker. **Status saat ini:** pengukuran belum ada, karena environment build memblokir
semua host sumber. Langkah ini wajib di VPS.

## 3. Instalasi (Docker Compose)

1. Pasang Docker Engine + plugin compose, dan chrony:
   `sudo apt install chrony && chronyc tracking` (offset harus jauh di bawah 100 ms).
2. Clone repo (private) ke `/opt/gold-news-agent`.
3. Rahasia:
   ```bash
   cp .env.example .env && chmod 600 .env
   ```
   Isi minimal: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `TELEGRAM_ADMIN_USER_IDS`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `DASHBOARD_PASSWORD`.
   Buat password acak, mis. `openssl rand -hex 24`. Opsional: `TELEGRAM_API_ID/HASH` (baca
   channel), `FRED_API_KEY`, `TWELVEDATA_API_KEY`. `.env` hanya untuk rahasia. Perilaku (bobot,
   threshold, jam tenang) ada di `config/settings.yaml`.
4. Verifikasi sumber (langkah 2 di atas). Untuk sumber yang lolos **dan** ToS-nya sudah dibaca,
   ubah `status: verified` dan `enabled: true` di `config/sources.yaml`. Collector hanya memakai
   sumber yang verified.
5. Login Telethon (bila memakai channel Telegram), sekali saja:
   `docker compose run --rm -it collector telegram-login`. File session tersimpan di volume
   `golddata`. File itu setara password akun Telegram: jangan disalin ke mana-mana.
6. Timer offset jam (container tidak punya chrony):
   ```bash
   sudo cp deploy/systemd/gold-clock-offset.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now gold-clock-offset.timer
   cat /run/gold/clock_offset        # detik, mis. -0.000012
   ```
7. Jalankan:
   ```bash
   docker compose build && docker compose up -d
   docker compose ps                 # semua "healthy" dalam ±2 menit
   ```
8. Backup tiap malam (§7):
   ```bash
   sudo cp deploy/systemd/gold-backup.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now gold-backup.timer
   scripts/backup.sh && scripts/restore_check.sh   # uji sekarang, jangan menunggu malam
   ```
9. Dashboard dibuka lewat tunnel, tidak dibuka ke internet:
   `ssh -L 8787:127.0.0.1:8787 user@vps`, lalu buka http://localhost:8787. Cara lain: Tailscale
   serve atau Cloudflare Tunnel + Access. Di dalam container dashboard mendengarkan di `0.0.0.0`,
   karena itu `DASHBOARD_PASSWORD` wajib diisi. Port hanya dipublikasikan di loopback host.

Untuk image produksi: pin `python:3.12-slim-bookworm` dan `ghcr.io/astral-sh/uv` ke digest
(`docker buildx imagetools inspect <image>`) supaya build bisa diulang persis.

## 4. Operasi harian

| Perlu apa | Perintah |
|---|---|
| Status cepat | Telegram `/status`, atau `docker compose ps` |
| Sinyal aktif/terakhir | `/signal` |
| Outlook 24 jam | `/outlook` |
| Kalender 48 jam | `/calendar` |
| Performa paper | `/stats` |
| Bisukan 🟡/🔵 | `/mute 1h`, `/mute off` (🔴 tetap masuk) |
| Kill switch | `/stop` (tidak ada sinyal baru), `/resume` |
| Log satu service | `docker compose logs -f --tail=200 analyzer` |
| Laporan manual | `docker compose run --rm evaluator report harian --date 2026-10-13` |
| Event study | `docker compose run --rm evaluator event-study --days 90` |
| Backtest | `docker compose run --rm evaluator backtest --days 30 --folds 4` |
| Usulan bobot | `docker compose run --rm evaluator calibrate` (butuh ≥ 100 sinyal tertutup) |

- **Mengubah config:** edit `config/settings.yaml` atau `config/sources.yaml`, lalu
  `docker compose restart <service>`. Folder config di-mount read-only, jadi tidak perlu build.
  Config divalidasi saat start. Kalau salah ketik, service menolak jalan dan log menyebut
  field-nya.
- **Update kode:** `git pull && docker compose build && docker compose up -d`. Migrasi jalan dulu
  lewat service `migrate`.
- **Log** berformat JSON, dirotasi 5 × 20 MB per container. Rahasia disaring `Redactor`
  sebelum ditulis.
- **Jam tenang** 00:00–05:00 WIB (`notifications.quiet_hours_wib`): notifikasi 🟡/🔵 ditahan
  lalu dikirim sebagai ringkasan. 🔴 tetap dikirim.

## 5. Alert dan tindakan

| Alert | Arti | Tindakan |
|---|---|---|
| 🩺 Sumber X bermasalah | ≥ 3 error beruntun, tidak ada poll sukses > 30 mnt, atau channel diam melewati batasnya | `docker compose logs collector \| grep X`. Cek situs/ToS sumber (URL pindah? diblokir?). Bila terus-menerus, set `enabled: false` dan cari pengganti |
| Sumber X normal kembali | Pulih | — |
| Service X tidak merespons | Heartbeat > 3 menit | `docker compose ps`, `docker compose logs X`, `docker compose restart X` |
| Antrian S menumpuk | Consumer tertinggal > 500 pesan | Cek service pembaca stream itu (CPU, LLM lambat, error berulang di log) |
| Jam sistem melenceng | \|offset\| > 100 ms | `chronyc tracking`, `chronyc sources -v`. Latensi yang tercatat selama itu tidak bisa dipercaya |
| Offset jam sistem tidak diketahui | File offset tidak ada atau lebih tua dari 10 menit | `systemctl status gold-clock-offset.timer` |
| Disk hampir penuh | Sisa < 10 % | `docker system df`, hapus image lama (`docker image prune`), pindahkan backup ke luar VPS, cek retensi (`watchdog.tick_retention_days`) |
| Data harga basi / pulih | Tidak ada tick > 20 dtk saat pasar buka | Cek feed utama (Binance/MT5 bridge). Fallback otomatis ke `market.fallbacks`, label sumber tampil di sinyal |
| Claude API bermasalah — mode rules-only | Circuit breaker LLM terbuka | Cek status Anthropic dan kuota. Sistem tetap jalan dengan aturan (lebih konservatif) |
| Budget LLM harian habis | Batas biaya WIB-harian (`budget.llm_daily_usd`) tercapai | Sengaja: sampai 00:00 WIB hanya berita dampak tinggi yang ke LLM, sisanya aturan. Naikkan batasnya bila memang perlu |
| Lonjakan harga / Spread melebar | Harga bergerak tanpa berita yang diketahui | Informasi. Collector diminta poll ulang untuk mencari penyebabnya |

Tidak ada alert sama sekali juga bisa berarti masalah: bila Redis atau notifier mati, alert
tidak bisa terkirim. Pasang pemantau dari luar VPS yang membuka dashboard lewat tunnel, atau
cek `/status` tiap pagi.

## 6. Uji alert (fire drill), wajib sebelum burn-in

Fase 7 baru selesai bila alert terbukti sampai. Versi otomatisnya ada di
`tests/test_fire_drill.py`: collector, watchdog, dan notifier sungguhan; feed dimatikan lalu
dihidupkan, kedua pesan harus sampai ke Telegram. Di VPS:

1. **Sumber mati.** Di `config/sources.yaml`, ubah URL satu sumber verified ke path yang tidak
   ada di host yang sama, lalu `docker compose restart collector`. Dalam beberapa poll harus
   muncul 🩺 "Sumber X bermasalah". Kembalikan URL, restart, tunggu "normal kembali".
2. **Service mati.** `docker compose stop signal`. Dalam ±4 menit harus muncul "Service signal
   tidak merespons". Lalu `docker compose start signal`.
3. **Harga mati.** Matikan feed utama (mis. hentikan mt5-bridge). Harus muncul "Data harga basi"
   dan sinyal berubah ke WAIT.
4. **Jam.** `sudo systemctl stop gold-clock-offset.timer`. Setelah 10 menit harus muncul "Offset
   jam sistem tidak diketahui". Nyalakan lagi.

Catat jam tiap langkah dan jam pesan masuk di Telegram.

## 7. Backup dan restore

- **Yang di-backup:** PostgreSQL (seluruh state), tiap malam 04:30 WIB, disimpan 14 hari di
  `backups/`. **Salin juga ke luar VPS** (rclone/restic ke object storage). Backup di disk yang
  sama bukan backup.
- **Tidak di-backup:** Redis (bus pesan sementara; AOF menjaganya saat restart, dan bila
  volumenya hilang hanya pesan yang belum diproses yang hilang, karena state ada di database),
  dan session Telethon (buat ulang dengan `telegram-login`; bila tetap ingin di-backup, enkripsi
  dulu, mis. `age`).
- **Uji restore** minimal sebulan sekali, idealnya tiap minggu: `scripts/restore_check.sh`
  memuat dump terbaru ke database sementara, menghitung baris tabel utama, lalu menghapusnya.
- **Restore sungguhan:**
  ```bash
  docker compose stop collector market calendar analyzer signal paper evaluator notifier dashboard watchdog
  docker compose exec -T postgres dropdb -U gold gold
  docker compose exec -T postgres createdb -U gold gold
  docker compose exec -T postgres pg_restore -U gold -d gold --no-owner < backups/gold-<stamp>.dump
  docker compose up -d
  ```

## 8. MT5 bridge (opsional)

Topologi B (ADR-011): core di VPS Linux, terminal MT5 + bridge di VPS Windows kecil, keduanya
dalam satu jaringan Tailscale/WireGuard.

1. VPS Linux: isi IP Tailscale di `docker-compose.mt5-bridge.yml`, lalu
   `docker compose -f docker-compose.yml -f docker-compose.mt5-bridge.yml up -d`. Redis hanya
   terbuka di IP Tailscale dan tetap memakai password.
2. Windows: pasang Python 3.12 + uv, clone repo, `uv sync --extra mt5`. Isi `.env` dengan
   `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, dan
   `REDIS_URL=redis://:<REDIS_PASSWORD>@<IP Tailscale VPS>:6379/0`. Jalankan
   `uv run gold-agent mt5-bridge` sebagai service (NSSM atau Task Scheduler "At startup").
3. Di `config/settings.yaml`: `market.primary: mt5_bridge`. Bila DXY sintetis dari MT5 dipakai,
   isi `market.usd_source: mt5_synthetic` dan simbol FX broker di `market.mt5_fx_symbols`.
4. Ukur hop jaringannya: bandingkan `received_at` tick dengan waktu server MT5 di dashboard.

## 9. Replay satu hari

```bash
docker compose run --rm market replay 2026-10-13 --from 19:00 --to 22:00
```

Memutar arsip berita + tick hari itu melewati pipeline yang sama (collector → analyzer → signal
→ paper → notifier) ke database scratch `data/replay/replay-<hari>.db`. Tidak ada yang terkirim
keluar dan LLM mati, kecuali diberi `--llm` (berbayar). Hasilnya **deterministik** dan **sama
pada `--speed` berapa pun**: jam replay berhenti selama pipeline bekerja, sehingga lag proses
tidak memicu penjaga umur (flash basi, berita lama). `--speed` hanya membatasi kecepatan
maksimum. Replay menunjukkan apa yang diputuskan sistem, bukan seberapa cepat. Latensi diukur
live.

## 10. Burn-in 72 jam (gerbang Fase 7)

- [ ] Semua container `healthy` 72 jam tanpa campur tangan. `docker inspect -f
      '{{.Name}} {{.RestartCount}}' $(docker compose ps -q)`: restart karena crash dicatat dan
      dicari sebabnya.
- [ ] Fire drill §6 selesai, keempat alert sampai di Telegram (catat jamnya).
- [ ] Latensi per sumber tercatat (p50/p95), dan tier 1 sesuai harapan.
- [ ] Offset jam < 100 ms sepanjang 72 jam.
- [ ] Tiga backup malam ada, dan `restore_check.sh` lulus.
- [ ] Pertumbuhan disk diukur (tick/hari ±9 MB pada 1 tick/dtk); retensi disesuaikan bila perlu.
- [ ] Biaya LLM per hari sesuai anggaran (`/stats` atau tabel `llm_usage`).
- [ ] Tidak ada rahasia di log: `docker compose logs | grep -iE 'sk-ant|bot[0-9]+:'` kosong.
- [ ] Replay satu hari dari arsip burn-in berjalan, dan ringkasannya masuk akal.

## 11. Kill switch dan gerbang Fase 8

- `/stop` menghentikan sinyal baru (disimpan di database, tetap berlaku setelah restart).
  `/resume` melanjutkan.
- `execution_enabled` bertipe `Literal[False]` di kode: config tidak bisa menyalakan eksekusi.
- Syarat sebelum Fase 8 dibicarakan: ≥ 2–4 minggu paper trading tercatat, laporan mingguan
  ditinjau (termasuk bila hasilnya jelek), ≥ 100 sinyal tertutup untuk kalibrasi, akun **demo**
  dulu, dan persetujuan eksplisit darimu.

## 12. Catatan

- **TimescaleDB** opsional. PostgreSQL biasa + retensi malam (tick 30 hari, snapshot konteks
  180 hari, candle disimpan) cukup untuk ±9 MB tick/hari. Kompresi Timescale baru berguna bila
  laju tick jauh lebih tinggi.
- **Tanpa Docker:** `deploy/systemd/gold-news-agent.service` menjalankan semua service dalam
  satu proses (`settings.infra.bus: memory`). Di mode ini watchdog membaca `chronyc` langsung.
- **Regulasi:** untuk pemakaian pribadi aman. Sebelum membagikan sinyal ke orang lain, cek aturan
  Bappebti/OJK.
