# Pasang di Komputer Sendiri — Langkah demi Langkah

> Sistem pendukung keputusan, **bukan jaminan profit**. Mode `SIGNAL_ONLY`: tidak ada order
> yang dikirim ke broker.

Di komputer sendiri cukup **satu proses** (`gold-agent run all`) dengan database **SQLite**.
Tidak perlu Docker, PostgreSQL, atau Redis. Panduan ini untuk Windows 10/11 (paling umum,
dan satu-satunya yang bisa membaca harga langsung dari MetaTrader 5). Perbedaan untuk macOS
dan Linux ditandai **[macOS]** / **[Linux]**.

Jujur soal batasnya: PC rumah bisa tidur, mati listrik, internet putus, dan jaraknya ke sumber
berita lebih jauh dibanding VPS. Untuk mencoba, mengumpulkan data, dan paper trading, PC sudah
cukup. Untuk 24/7 yang serius, pindahkan ke VPS (`docs/runbook.md`). Data dan config bisa
ikut dipindah.

Perkiraan waktu: ±45 menit, ditambah 2 jam menunggu pengukuran sumber di langkah 7.

---

## Langkah 0 — Siapkan akun dan key

Panduan lengkap langkah demi langkah (BotFather, chat ID, API Claude + saldo + batas biaya,
Telethon, FRED, MT5) ada di **`docs/siapkan_akun.md`**. Ringkasannya:

| Apa | Wajib? | Cara dapat |
|---|---|---|
| Bot Telegram | wajib (untuk notifikasi) | Di Telegram buka **@BotFather** → `/newbot` → ikuti petunjuk → salin **token**. Pakai bot khusus untuk sistem ini |
| Chat ID + user ID kamu | wajib | Kirim `/start` ke bot barumu, lalu buka di browser `https://api.telegram.org/bot<TOKEN>/getUpdates`. Angka di `"chat":{"id":...}` adalah chat ID, dan untuk chat pribadi sekaligus user ID kamu |
| Anthropic API key | disarankan | console.anthropic.com → API Keys. Tanpa key, analisis memakai aturan saja |
| `TELEGRAM_API_ID` / `HASH` | opsional | my.telegram.org → API development tools. Hanya untuk membaca channel berita Telegram; sebaiknya pakai akun Telegram khusus |
| FRED API key | opsional | fred.stlouisfed.org → My Account → API Keys (yield obligasi AS) |
| Akun MT5 | opsional, Windows | Terminal MetaTrader 5 broker kamu, login (pakai akun **demo** dulu) |

Simpan semuanya sementara di catatan. Jangan dikirim ke siapa pun.

## Langkah 1 — Pasang alat

**Windows**: buka **PowerShell** (bukan sebagai Administrator), lalu:

```powershell
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Tutup PowerShell, lalu buka lagi supaya perintah `git` dan `uv` dikenali. Cek dengan
`git --version` dan `uv --version`. Python tidak perlu dipasang terpisah: `uv` mengunduh
Python 3.12 sendiri di langkah 3.

**[macOS]** Di Terminal: `xcode-select --install` (git), lalu
`curl -LsSf https://astral.sh/uv/install.sh | sh`.
**[Linux]** `sudo apt install git chrony`, lalu `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## Langkah 2 — Ambil kode

Pilih **folder pendek di luar OneDrive/Dropbox**, mis. `C:\gold-news-agent`. Sinkronisasi
cloud bisa merusak database yang sedang ditulis.

**Pilihan B (disarankan): repo private milikmu.** Sekarang kodenya ada di fork publik
`A-H-N-B/hermes-agent`. Begitu kamu mengisi equity, email kontak, atau chat ID di config,
config itu jangan sampai ter-push ke repo publik. Buat dulu repo **private** kosong bernama
`gold-news-agent` di github.com/new (tanpa README), lalu:

```powershell
cd C:\
git clone --branch claude/prompt-execution-repo-setup-7e735z --single-branch https://github.com/A-H-N-B/hermes-agent.git hermes-tmp
cd hermes-tmp
git subtree split --prefix=gold-news-agent -b gold-main
git push https://github.com/A-H-N-B/gold-news-agent.git gold-main:main
cd C:\
git clone https://github.com/A-H-N-B/gold-news-agent.git
Remove-Item -Recurse -Force C:\hermes-tmp
cd C:\gold-news-agent
```

**Pilihan A (cepat, hanya untuk mencoba):** ambil folder project saja dari fork publik.

```powershell
cd C:\
git clone --filter=blob:none --sparse --branch claude/prompt-execution-repo-setup-7e735z https://github.com/A-H-N-B/hermes-agent.git
cd hermes-agent
git sparse-checkout set gold-news-agent
cd gold-news-agent
```

Semua perintah berikutnya dijalankan **di folder project** ini.

## Langkah 3 — Pasang dependensi

```powershell
uv sync                    # Windows + MetaTrader 5: uv sync --extra mt5
uv run gold-agent --help   # harus muncul daftar perintah
```

## Langkah 4 — Isi rahasia (`.env`)

```powershell
Copy-Item .env.example .env
notepad .env
```

**[macOS/Linux]** `cp .env.example .env && chmod 600 .env`, lalu buka dengan editor.

Isi minimal: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_USER_IDS` (user ID
kamu), dan `ANTHROPIC_API_KEY` bila ada. **Biarkan `DATABASE_URL` dan `REDIS_URL` kosong**:
kosong berarti SQLite di `data\gold.db`. `DASHBOARD_PASSWORD` tidak perlu selama dashboard
hanya dibuka di komputer ini. `.env` tidak pernah masuk git.

Untuk MT5: isi `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (persis seperti di jendela login MT5).

## Langkah 5 — Sesuaikan config

Buka `config\settings.yaml` (mis. `notepad config\settings.yaml`):

1. `http.contact_email`: email kontakmu (boleh alamat khusus). Sebagian situs data AS (BLS)
   menolak bot tanpa alamat kontak.
2. `risk.account_equity`: modal akun (angka). Opsional, tapi tanpa ini ukuran lot tidak
   ditampilkan. Sistem tidak pernah menebak.
3. Sumber harga di `price_feed`:
   - **Windows + MT5** (paling akurat, harga broker-mu sendiri): `primary: mt5` dan
     `mt5_symbol:` sesuai nama simbol emas di broker (`XAUUSD`, `XAUUSDm`, `GOLD`, ...).
     Terminal MT5 harus **terbuka dan login** selama sistem jalan.
   - **Tanpa MT5:** biarkan `primary: binance_paxg`. PAXG adalah token emas yang
     diperdagangkan 24/7 dan diberi label *proxy* di setiap sinyal.
4. **[Windows]** Agar log tersimpan saat berjalan otomatis (langkah 10), isi `logging.file:
   data/logs/gold-agent.log` (dirotasi otomatis 10 MB × 5).

Jam tenang (00:00–05:00 WIB), ambang sinyal, dan batas rugi harian juga ada di file ini.
Config yang salah ketik membuat program menolak jalan dan menyebut field-nya.

## Langkah 6 — Jam komputer harus tepat

Latensi berita diukur dalam milidetik, jadi jam yang meleset membuat angkanya bohong.

- **Windows:** Settings → Time & language → Date & time → nyalakan *Set time automatically*,
  lalu klik **Sync now**. Sinkronisasi bawaan Windows jarang (bisa seminggu sekali). Agar lebih
  sering, buka PowerShell **sebagai Administrator**:
  ```powershell
  w32tm /config /manualpeerlist:"pool.ntp.org" /syncfromflags:manual /update
  w32tm /resync
  ```
- **[macOS]** otomatis (System Settings → General → Date & Time).
- **[Linux]** `chronyc tracking` (offset harus jauh di bawah 100 ms).

Watchdog mengecek offset jam (lewat chrony, atau lewat server NTP `pool.ntp.org` tiap 15 menit
bila chrony tidak ada) dan memberi alert bila melenceng > 100 ms.

## Langkah 7 — Verifikasi sumber berita (wajib, sekali)

Program hanya memakai sumber yang sudah kamu nyatakan **verified**. Ukur dulu dari komputer ini:

```powershell
uv run gold-agent verify-sources --note "PC rumah"                       # cek cepat (1 menit)
uv run gold-agent verify-sources --watch-minutes 120 --note "PC rumah"   # latensi, 2 jam
```

Idealnya putaran 2 jam mencakup rilis data AS (19:30 atau 20:30 WIB). Hasilnya ada di
`docs\source_report.md`. Untuk tiap sumber berstatus ✅ OK:

1. Buka `tos_url` sumber itu di `config\sources.yaml` dan baca syarat penggunaannya.
2. Bila boleh dipakai, ubah `status: candidate` menjadi `status: verified`.

Sumber harga Binance (`binance_paxg_ws`, `binance_paxg_rest`) juga harus verified bila dipakai.
MT5 tidak perlu, karena terminalnya ada di komputermu sendiri.

## Langkah 8 — (Opsional) Login untuk membaca channel Telegram

```powershell
uv run gold-agent telegram-login
```

Masukkan nomor HP dan kode dari Telegram. File `data\telegram\gold_agent.session` setara
password akun itu: jangan disalin atau dibagikan. Channel yang dibaca diatur di
`config\sources.yaml` (`telegram_squawk` → `channels`).

## Langkah 9 — Jalankan

```powershell
uv run gold-agent run all
```

Saat start, database dibuat/dimigrasi otomatis. Lalu cek:

- **Dashboard:** buka http://127.0.0.1:8787 di browser komputer ini.
- **Telegram:** kirim `/status` ke bot. Jawabannya berisi harga, sesi, status sinyal, dan event
  berikutnya.
- **Kesehatan:** di PowerShell lain, `uv run gold-agent healthcheck signal` harus menulis
  `heartbeat ...s ago`.

Berhenti: tekan **Ctrl+C**. Perintah bot yang berguna: `/signal`, `/outlook`, `/calendar`,
`/stats`, `/mute 1h`, dan **`/stop`** (kill switch, tidak ada sinyal baru) / `/resume`.

## Langkah 10 — Jalan otomatis dan tidak tidur

**Windows: komputer jangan tidur** (PowerShell Administrator):

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

**Windows: jalan otomatis saat login.** Ganti `C:\gold-news-agent` bila foldermu lain:

```powershell
$dir = "C:\gold-news-agent"
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\gold-agent.exe" -Argument "run all" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "GoldNewsAgent" -Action $action -Trigger $trigger -Settings $settings
Start-ScheduledTask -TaskName "GoldNewsAgent"
```

Dipicu saat *login* (bukan saat boot) karena terminal MT5 butuh sesi Windows yang aktif.
Taruh shortcut MT5 di folder Startup (`Win+R` → `shell:startup`) supaya ikut terbuka. Saat login
muncul jendela konsol gold-agent: **minimize saja, jangan ditutup**, karena menutupnya
menghentikan program. Service yang crash di-restart oleh program itu sendiri. Log ada di
`data\logs\gold-agent.log` (langkah 5.4). Menghentikan: `Stop-ScheduledTask -TaskName
"GoldNewsAgent"`. Menghapus: `Unregister-ScheduledTask -TaskName "GoldNewsAgent"`.

**[macOS]** Buat `~/Library/LaunchAgents/com.gold-news-agent.plist` (ganti `NAMA` dengan nama
user dan sesuaikan folder):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.gold-news-agent</string>
  <key>ProgramArguments</key>
  <array><string>/Users/NAMA/gold-news-agent/.venv/bin/gold-agent</string><string>run</string><string>all</string></array>
  <key>WorkingDirectory</key><string>/Users/NAMA/gold-news-agent</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

Isi `logging.file` di config, aktifkan dengan
`launchctl load -w ~/Library/LaunchAgents/com.gold-news-agent.plist`, dan cegah Mac tidur
(System Settings → Energy/Battery → *Prevent automatic sleeping*).

**[Linux]** Pakai `deploy/systemd/gold-news-agent.service`: sesuaikan path dan user, salin ke
`/etc/systemd/system/`, lalu `sudo systemctl enable --now gold-news-agent`. Log:
`journalctl -u gold-news-agent -f`.

## Langkah 11 — Backup

```powershell
uv run gold-agent db backup     # salinan data\gold.db ke backups\, menyimpan 14 terbaru
```

Aman dijalankan saat program sedang jalan. Agar otomatis tiap malam 04:30 WIB (Windows):

```powershell
$dir = "C:\gold-news-agent"
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\gold-agent.exe" -Argument "db backup" -WorkingDirectory $dir
Register-ScheduledTask -TaskName "GoldNewsAgentBackup" -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At "04:30")
```

Jam pemicu memakai zona waktu Windows. Bila Windows-mu tidak di WIB, sesuaikan. Salin juga
folder `backups\` ke tempat lain (flashdisk/cloud) sesekali: backup di disk yang sama tidak
melindungi dari disk rusak. Yang perlu disimpan: `backups\`, `.env`, dan `config\`. Session
Telethon bisa dibuat ulang dengan `telegram-login`.

## Langkah 12 — Uji alert (sekali, 10 menit)

1. Putuskan internet ±2 menit, lalu sambungkan lagi. Telegram harus menerima 🩺 "Sumber ...
   bermasalah" dan "Data harga basi", lalu kabar pulih. Pesan 🔴 yang gagal terkirim saat
   offline dikirim ulang begitu internet kembali, yang lain masuk satu ringkasan.
2. Tutup terminal MT5 (bila dipakai). Harus muncul "Data harga basi", dan sinyal menjadi WAIT.
   Buka lagi MT5.

## Update ke versi baru

```powershell
Stop-ScheduledTask -TaskName "GoldNewsAgent"      # atau Ctrl+C
git pull
uv sync                                           # + --extra mt5 bila dipakai
Start-ScheduledTask -TaskName "GoldNewsAgent"
```

## Bila ada masalah

| Gejala | Solusi |
|---|---|
| `uv` / `git` tidak dikenali | Tutup dan buka lagi PowerShell setelah instalasi |
| Semua sumber ❌ di laporan | Cek internet/firewall/antivirus. Di jaringan kantor, proxy bisa memblokir |
| Tidak ada berita masuk | Belum ada sumber `status: verified` (langkah 7) |
| `feed.none_available` / harga "tidak tersedia" | Sumber harga belum verified, atau MT5 tertutup / belum login / `mt5_symbol` salah |
| `ModuleNotFoundError: MetaTrader5` | `uv sync --extra mt5` (hanya Windows) |
| Port 8787 sudah dipakai | Program lain memakainya, atau gold-agent sudah jalan dua kali (cek Task Manager). Ganti `dashboard.port` bila perlu |
| Alert "Offset jam sistem tidak diketahui" | Firewall memblokir NTP (UDP 123). Izinkan, atau ganti `watchdog.ntp_server` (mis. `time.windows.com`) |
| Alert "Jam sistem melenceng" | Ulangi langkah 6 (`w32tm /resync`) |
| Bot diam saat diberi perintah | `TELEGRAM_ADMIN_USER_IDS` harus berisi user ID-mu; token dipakai program lain (mis. bot lain/Hermes) |
| Ingin dibuka dari HP | Pasang Tailscale di PC dan HP, isi `DASHBOARD_PASSWORD`, lalu ubah `dashboard.host` ke IP Tailscale PC. Jangan buka port ke internet |

Pindah ke VPS nanti: lihat `docs/runbook.md`. `config\` dan `.env` bisa dipakai lagi.
