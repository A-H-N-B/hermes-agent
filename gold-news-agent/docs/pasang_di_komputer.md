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

## Cara cepat (Windows): satu perintah

Siapkan dulu token bot Telegram dan (opsional) API key Claude (`docs/siapkan_akun.md`). Buka
**Windows PowerShell biasa** (bukan Administrator), tempel, lalu Enter:

```powershell
powershell -NoExit -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/A-H-N-B/hermes-agent/claude/prompt-execution-repo-setup-7e735z/gold-news-agent/scripts/install_windows.ps1 | iex"
```

`scripts/install_windows.ps1` mengerjakan langkah 1–10 di bawah: memasang git dan uv, mengunduh
folder project ke `C:\hermes-agent\gold-news-agent`, meminta token dan key (input tersembunyi,
dicek ke Telegram/Anthropic sebelum disimpan), mencari Chat ID sendiri setelah kamu menekan
START di bot, mengaktifkan sumber publik yang lolos `verify-sources` dari komputer itu (yang
ditolak robots.txt tidak pernah diaktifkan), memasang nyala otomatis lewat folder Startup, lalu
membuka dashboard. Aman dijalankan ulang. Sumber lain (FinancialJuice, FXStreet, Kitco, ...)
tetap `candidate` sampai syarat penggunaannya kamu baca sendiri (langkah 7).

Installer juga membuat shortcut di Desktop: **Jalankan Gold News Agent** (menyalakan program
bila belum jalan, lalu membuka dashboard; tidak pernah menyalakan dua salinan), **Matikan Gold
News Agent**, **Perbarui Gold News Agent** (update aman, lihat "Update ke versi baru"), plus
**Dashboard XAUUSD** (langsung membuka `http://127.0.0.1:8787/`). Tiga yang pertama memanggil
`scripts\windows\jalankan.cmd` / `matikan.cmd` / `perbarui.cmd`. Pengaturan yang diisi installer
(email kontak, file log, sumber yang lolos uji) disimpan di `config\local.yaml` dan
`config\sources.local.yaml`: file milik komputer itu sendiri yang tidak ikut git, jadi tidak
pernah bentrok saat update.

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
uv sync                    # Windows: uv sync --extra mt5 (harga broker dari MetaTrader 5)
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

Untuk MT5 tidak ada isian: login di terminal MT5 seperti biasa. Agent hanya membaca akun yang
sedang terbuka dan tidak pernah login sendiri, jadi akunmu tidak pernah diganti atau dikeluarkan.

Tanpa MT5 sama sekali (harga emas spot dari Binance XAUUSDT, dolar dari EURUSDT): jalankan
sekali `.venv\Scripts\gold-agent.exe config set price_feed.primary binance_xau`, lalu klik
"Perbarui" (langkah 6 menguji sumber harga Binance dari komputer ini; komputer yang sebelumnya
memakai `binance_paxg` dipindah ke `binance_xau` otomatis begitu sumbernya lolos). XAUUSDT
adalah kontrak perpetual Binance yang mengikuti XAUUSD spot tiap detik, jadi harganya biasanya
hanya selisih beberapa sen sampai satu-dua dolar dari broker; sisanya bisa disamakan dengan
`/finex` (lihat langkah 9). PAXG (token emas, $5–20 di atas spot) hanya dipakai bila XAUUSDT
tidak terjangkau. Pasar terkait (perak, minyak, yield dari broker) hanya ada dengan MT5.
Kembali ke MT5: `config set price_feed.primary auto`.

## Langkah 5 — Sesuaikan config

`config\settings.yaml` berisi nilai bawaan dan ikut diperbarui. **Jangan ubah file itu**:
pengaturan milikmu masuk ke `config\local.yaml` (tidak ikut git, aman saat update) lewat:

```powershell
uv run gold-agent config set http.contact_email "emailmu@contoh.com"
uv run gold-agent config set risk.account_equity 1000
```

Perintah itu menolak nilai yang membuat program tidak bisa jalan dan menyebut field-nya.
`config\local.yaml` juga boleh dibuka dengan Notepad: isinya hanya kunci yang kamu ubah.

1. `http.contact_email`: email kontakmu (boleh alamat khusus). Sebagian situs data AS (BLS)
   menolak bot tanpa alamat kontak.
2. `risk.account_equity`: modal akun (angka). Opsional, tapi tanpa ini ukuran lot tidak
   ditampilkan. Sistem tidak pernah menebak.
3. Sumber harga (`price_feed.primary`, bawaan `auto`):
   - **Windows + MT5** (paling akurat, harga broker-mu sendiri): cukup buka terminal MT5 dan
     login. `auto` memakai MT5 bila terminalnya jalan dan mencari sendiri simbol emas broker
     (`XAUUSD`, `XAUUSDm`, `XAUUSD.r`, `GOLD`, ...; yang ada di Market Watch didahulukan).
     Bila MT5 ditutup, program pindah ke spot XAUUSDT (Binance) dan kembali ke MT5 paling lambat
     10 menit setelah MT5 dibuka lagi; riwayat candle dimuat ulang dari feed baru supaya dua harga tidak tercampur.
     Di MT5, tombol **Algo Trading** harus hijau dan terminal tetap terbuka serta login; tanpa itu
     harga diambil dari sumber berikutnya. Program tidak pernah membuka MT5 sendiri. Dari terminal yang sama program juga membaca **pasar
     penggerak emas**: indeks dolar dari pasangan FX broker (EURUSD wajib; USDJPY, GBPUSD,
     USDCAD, USDSEK, USDCHF bila ada), perak (XAGUSD), minyak (XTIUSD/USOIL), dan bila broker
     menyediakannya: yield/obligasi AS 10 tahun, S&P 500, dan VIX. Nama simbol dicari sendiri
     (akhiran seperti `m`, `.r`, `.cash` dikenali). Bila satu tidak ketemu, sebutkan namanya,
     mis. `uv run gold-agent config set market.mt5_symbols "{US500: SPX500.cash}"`
     (kunci: `DXY`, `US10Y`, `USDJPY`, `XAGUSD`, `WTI`, `US500`, `VIX`, atau pasangan FX seperti
     `EURUSD`).
   - **Tanpa MT5:** `auto` memakai Binance XAUUSDT (kontrak yang mengikuti XAUUSD spot, selisih
     ke broker biasanya di bawah $1–2), lalu token PAXG bila XAUUSDT tidak terjangkau. Keduanya
     diberi label *proxy* di setiap sinyal dan bisa disamakan ke harga brokermu.
4. **[Windows]** Agar log tersimpan saat berjalan otomatis (langkah 10), isi `logging.file`
   dengan `data/logs/gold-agent.log` (dirotasi otomatis 10 MB × 5).
5. Timeframe (`trading.timeframe`, bawaan `auto`). Sinyal jalan sepanjang hari, dan chart yang
   dipakai untuk entry, SL/TP, timing, dan masa berlaku dipilih otomatis setiap saat:
   - bawaan per sesi: pagi sampai sore (sesi Asia dan London) **M5**, mulai pembukaan New York
     (19:00 WIB musim panas, 20:00 WIB musim dingin) **M15**;
   - setelah ada data, timeframe yang terbukti paling akurat di sesi itu (akurasi prediksi
     30 hari, batas bawah 95%-nya di atas 50%) menggantikan bawaan;
   - timeframe yang spread-nya terlalu besar dibanding SL (`max_cost_ratio`) dilewati;
   - **AI memutuskan akhir** setiap sinyal: setuju (BUY/SELL) atau TUNGGU, dan memilih
     chart-nya dari timeframe yang layak. Tiga jawaban **Claude Opus 5.5** terpisah dan
     bersamaan (`llm.desk_votes`, effort low, selesai dalam hitungan detik); trade lolos bila
     mayoritas setuju. Jumlah suara dan alasan suara yang menahan tampil di sinyal. Angka harga,
     SL, dan TP tetap dihitung sistem. TUNGGU dari AI ditanya ulang setelah 3 menit, atau
     langsung bila ada impuls baru;
   - **impuls momentum** (`signal_engine.impulse`): bila harga bergerak luar biasa cepat (gerak
     1–10 menit ≥ 3σ) dan menembus high/low 30 menit terakhir, sistem langsung membuat kandidat
     BUY/SELL di chart M5 tanpa menunggu berita, asal skor berita/makro tidak melawan dan harga
     belum terlalu jauh (≤ 1,5 ATR) dari awal gerak. Ini yang mencegah gerak besar terlewat.
   Timeframe tetap juga bisa: `uv run gold-agent config set trading.timeframe.select M5`.
6. Biaya AI (`budget`). `llm_daily_usd` (bawaan **$20/hari**) adalah batas keras: tidak ada
   panggilan Claude yang dimulai bila bisa melewatinya. Berita biasa, outlook, dan laporan
   berhenti di 75 % (`priority_reserve_pct: 25`, jadi $15); sisa $5 khusus untuk keputusan sinyal
   dan berita berdampak tinggi. Satu review AI sekitar $0,15 dan dibatasi
   `llm.desk_reviews_per_day` (60) review per hari; satu pertanyaan Tanya AI sekitar $0,05,
   paling banyak `llm.chat_per_day` (60) per hari (setelah itu dijawab program tanpa AI). Contoh menurunkan batas ke $10:
   `uv run gold-agent config set budget.llm_daily_usd 10`.
7. Fundamental harian (`market.fundamentals_refresh_minutes`, bawaan 60). Bias harian emas
   dibangun dari: yield riil AS 10 tahun dan yield 2 tahun (Treasury AS), inflasi ekspektasi
   (breakeven), tren dolar, posisi spekulan besar di futures emas COMEX (CFTC COT), arus ETF
   emas GLD, indeks kejutan data AS (dari rilis yang dicatat program sendiri), dan panasnya
   berita geopolitik. Treasury dan CFTC adalah data publik pemerintah AS; installer dan updater
   mengujinya dari komputermu dan mengaktifkan yang lolos. GLD (situs SPDR) **tidak** diaktifkan
   otomatis karena syarat situsnya membatasi penyalinan. Bila sudah membaca
   `https://www.spdrgoldshares.com/usa/terms` dan boleh, aktifkan:
   `uv run gold-agent verify-sources --only spdr_gld_holdings` lalu
   `uv run gold-agent config verified spdr_gld_holdings`. Faktor yang datanya belum ada
   dihitung netral dan disebut di dashboard.
8. Ekonofisika dan pembelajaran mesin berjalan sendiri, tanpa pengaturan. Kartu "Ekonofisika
   pasar" di halaman Live terisi setelah ada data menit beberapa hari: dengan MT5 langsung dari
   riwayat broker, tanpa MT5 setelah program berjalan ±2 hari. Model pembelajaran mesin butuh
   minimal 30 setup selesai di sebuah timeframe, dan angkanya baru ikut menghitung peluang
   setelah lolos uji walk-forward; sebelum itu ditampilkan sebagai "belum terbukti, tidak
   dipakai". Saat start, program memuat ±300 hari candle H1 (sekali, beberapa detik) untuk
   analisis harian.

Jam tenang (00:00–05:00 WIB), ambang sinyal, dan batas rugi harian juga ada di
`config\settings.yaml` dan bisa ditimpa dengan cara yang sama.

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
2. Bila boleh dipakai: `uv run gold-agent config verified <id_sumber>` (bisa beberapa id
   sekaligus). Tandanya disimpan di `config\sources.local.yaml`, jadi aman saat update.

Sumber harga Binance (`binance_xau_ws`, `binance_xau_rest`, cadangannya `binance_paxg_ws`,
`binance_paxg_rest`) juga harus verified bila dipakai.
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

Yang baru di halaman Live dan bot:

- **Tanya AI.** Di dashboard, kartu "Tanya AI": tulis "kapan aku harus buy?" lalu Enter. Di
  Telegram: `/tanya kapan aku harus sell?`, atau ketik pesan biasa tanpa `/`. Claude Opus 5.5
  menjawab dari data alat ini saat itu (harga, sinyal aktif, impuls, jam rawan rilis, berita):
  BUY, SELL, atau TUNGGU dulu, lalu yang harus dipantau. Bukan saran keuangan.
- **Notifikasi.** Sinyal BUY/SELL baru otomatis masuk Telegram. Di dashboard klik tombol
  "🔕 Notifikasi" di atas sekali dan izinkan di browser: setiap sinyal baru muncul sebagai
  notifikasi Windows dengan bunyi, selama tab dashboard terbuka.
- **Harga beda dengan Finex.** Tanpa MT5, harga diambil dari Binance XAUUSDT (spot emas) yang
  biasanya hanya selisih beberapa sen sampai satu-dua dolar dari brokermu (PAXG, cadangannya,
  bisa $5–20). Lihat harga **Bid** XAUUSD di aplikasi Finex, lalu ketik
  di halaman Live bagian "Harga beda dengan broker (Finex)? Samakan di sini", atau kirim
  `/finex 2662.40` ke bot. Semua harga, candle, entry, SL dan TP di alat ini ikut bergeser ke
  harga Finex. Samakan ulang sesekali (selisihnya bisa bergeser pelan); kembali ke harga asli:
  `/finex reset`. Tidak bisa saat ada sinyal aktif. Dengan MT5 (terminal Finex terbuka dan
  sudah login) harga sudah tepat dan langkah ini tidak perlu.
- **Banner impuls.** Saat ada gerak cepat, halaman Live menampilkan "⚡ IMPULS NAIK/TURUN KUAT"
  dengan besar gerak dan level yang ditembus. Semua angka diperbarui tiap detik.

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

Bila `Get-ScheduledTaskInfo -TaskName "GoldNewsAgent"` menunjukkan `LastTaskResult` `267011`
(tugas tidak pernah jalan, terjadi di sebagian laptop), hapus tugas itu dan pakai folder Startup
seperti installer cepat: file `GoldNewsAgent.cmd` di `shell:startup` yang menjalankan
`.venv\Scripts\gold-agent.exe run all` dalam loop (menyala lagi 60 detik setelah berhenti).
Menyalakan: dobel-klik file itu. Mematikan nyala otomatis: hapus file itu.

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

**Windows:** klik shortcut **Perbarui Gold News Agent** di Desktop. Bila shortcut itu belum ada
(instalasi sebelum 21 Okt 2026), tempel sekali di Windows PowerShell biasa:

```powershell
powershell -NoExit -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/A-H-N-B/hermes-agent/claude/prompt-execution-repo-setup-7e735z/gold-news-agent/scripts/windows/perbarui.ps1 | iex"
```

Updater mematikan program, memindahkan setiap ubahan di `config\settings.yaml` /
`config\sources.yaml` ke `config\local.yaml` / `config\sources.local.yaml` (salinan lengkapnya
disimpan di `data\backups\config-sebelum-update-*.patch`), mengambil versi baru, memasang
dependensinya, memeriksa config masih valid, lalu menyalakan program lagi dan membuat shortcut
Perbarui. `.env` dan `data\` tidak disentuh. Bila gagal sebelum versi baru terpasang, versi lama
dinyalakan lagi.

**macOS/Linux** (tanpa updater):

```bash
uv run gold-agent config migrate   # ubahan config pindah ke config/local.yaml
git pull --ff-only
uv sync
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
| Tanya AI menjawab "tanpa AI" | `ANTHROPIC_API_KEY` belum diisi, budget harian habis, atau batas `llm.chat_per_day` tercapai: program menjawab dengan ringkasannya sendiri |
| Harga beda dengan Finex | Samakan di halaman Live atau `/finex <bid>` (lihat langkah 9), atau pakai MT5 |
| Notifikasi browser tidak muncul | Klik "🔕 Notifikasi" lalu izinkan; cek izin notifikasi situs di browser dan Focus Assist Windows |
| Ingin dibuka dari HP | Pasang Tailscale di PC dan HP, isi `DASHBOARD_PASSWORD`, lalu ubah `dashboard.host` ke IP Tailscale PC. Jangan buka port ke internet |

Pindah ke VPS nanti: lihat `docs/runbook.md`. `config\` dan `.env` bisa dipakai lagi.
