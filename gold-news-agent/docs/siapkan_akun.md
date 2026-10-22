# Langkah 0 secara detail — Bot Telegram, API Claude, dan key lainnya

Bagian ini menjelaskan cara mendapatkan setiap akun dan key.

> **Tentang file `.env`:** file ini **belum ada** saat kamu mengerjakan langkah 0. Ia baru
> dibuat di langkah 4 panduan pasang, setelah kode program diunduh. Jadi sekarang **simpan dulu
> semua token/key di catatan sementara** (mis. Notepad, jangan di chat). Setiap kali di bawah
> tertulis "tulis di `.env`", artinya: catat dulu baris itu, nanti di langkah 4 dipindahkan ke
> file `.env`. Cara membuat dan mengisi `.env` dijelaskan di bagian akhir dokumen ini.

> **Aturan emas:** token bot dan API key setara password. Jangan kirim ke chat, grup, email,
> screenshot, atau AI mana pun (termasuk saya). Bila tidak sengaja bocor, segera buat yang baru
> dan hapus yang lama (caranya ada di tiap bagian di bawah).

Urutan yang disarankan: **A** (bot Telegram) → **B** (chat ID & user ID) → **C** (API Claude).
Bagian D–G opsional.

---

## A. Membuat bot Telegram (wajib, ±5 menit)

Bot inilah yang mengirim sinyal, flash rilis data, dan alert ke HP-mu, sekaligus menerima
perintah seperti `/status` dan `/stop`.

1. Buka aplikasi Telegram (HP atau desktop).
2. Di kolom pencarian ketik **`@BotFather`**. Pilih yang bercentang biru. Itu bot resmi
   Telegram; akun lain dengan nama mirip adalah penipuan.
3. Tekan **Start**, lalu kirim perintah:
   ```
   /newbot
   ```
4. BotFather bertanya **nama tampilan** bot. Bebas, mis.:
   ```
   Gold News Agent Saya
   ```
5. BotFather bertanya **username** bot. Harus unik dan diakhiri `bot`, mis.:
   ```
   goldnews_namakamu_bot
   ```
   Bila ditolak ("already taken"), coba variasi lain.
6. BotFather membalas pesan berisi **token**, bentuknya seperti:
   ```
   1234567890:AAH-contohTokenPanjangHurufDanAngka
   ```
   Salin seluruh token (angka, titik dua, dan teks sesudahnya).
7. **Uji token:** buka di browser (ganti `<TOKEN>` dengan token tadi):
   ```
   https://api.telegram.org/bot<TOKEN>/getMe
   ```
   Harus muncul `"ok":true` beserta username bot-mu. Bila `"ok":false` / `Unauthorized`,
   token salah salin.
8. Tulis di `.env`:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:AAH-contohTokenPanjangHurufDanAngka
   ```

Pengaturan tambahan yang disarankan (masih di BotFather):

- `/setdescription` → isi mis. "Bot pribadi, bukan untuk umum".
- `/setjoingroups` → pilih bot-mu → **Disable**, supaya orang lain tidak bisa memasukkan bot ke
  grup mereka. Lewati langkah ini bila kamu ingin notifikasi masuk ke grup (lihat B2).

Satu bot untuk satu sistem. Jangan pakai token yang sama di program lain (mis. Hermes Agent):
dua program yang membaca satu token akan saling berebut pesan.

**Bila token bocor:** BotFather → `/revoke` → pilih bot → token lama mati dan kamu mendapat
token baru. Ganti di `.env`, lalu restart program.

## B. Chat ID dan user ID (wajib, ±3 menit)

Program perlu tahu ke mana notifikasi dikirim (**chat ID**) dan siapa yang boleh memberi
perintah (**user ID admin**).

### B1. Notifikasi ke chat pribadi (paling sederhana)

1. Di Telegram buka bot barumu (cari username-nya), lalu tekan **Start** atau kirim `/start`.
   Langkah ini wajib: bot tidak bisa mengirim pesan ke orang yang belum pernah menyapanya.
2. Buka di browser:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Cari bagian seperti ini:
   ```json
   "chat":{"id":987654321,"first_name":"Nama","type":"private"}
   ```
   Angka `987654321` adalah **chat ID** sekaligus **user ID**-mu (di chat pribadi keduanya sama).
   Bila hasilnya `"result":[]` (kosong), kirim satu pesan lagi ke bot lalu muat ulang halaman.
   Bila program sudah berjalan, hentikan dulu: program yang sedang jalan "mengambil" pesan itu
   sehingga `getUpdates` kosong.
4. Tulis di `.env`:
   ```
   TELEGRAM_CHAT_ID=987654321
   TELEGRAM_ADMIN_USER_IDS=987654321
   ```

Cara lain mengetahui user ID: kirim pesan ke **@userinfobot**. Ia membalas dengan ID-mu.

### B2. Notifikasi ke grup (opsional, mis. dibaca berdua)

1. Buat grup Telegram, lalu tambahkan bot-mu sebagai anggota.
2. Kirim satu pesan di grup, mis. `/status@goldnews_namakamu_bot`.
3. Buka `getUpdates` seperti di B1. Chat ID grup diawali minus, biasanya `-100...`:
   ```json
   "chat":{"id":-1001234567890,"title":"Nama Grup","type":"supergroup"}
   ```
4. Tulis `TELEGRAM_CHAT_ID=-1001234567890`. `TELEGRAM_ADMIN_USER_IDS` tetap user ID pribadi
   orang yang boleh memberi perintah, pisahkan dengan koma: `987654321,123123123`.

Perintah bot hanya dilayani bila pengirimnya ada di `TELEGRAM_ADMIN_USER_IDS`. Anggota grup
lain bisa membaca notifikasi, tetapi tidak bisa menjalankan `/stop` atau `/mute`.

> Membagikan sinyal ke orang lain (grup, berbayar) bisa terkena aturan Bappebti/OJK. Untuk
> pemakaian pribadi tidak masalah.

## C. API key Claude (Anthropic) — sangat disarankan (±10 menit)

Claude dipakai untuk membaca dan menilai berita (triage), analisis mendalam berita penting,
dan narasi outlook. Tanpa key, sistem tetap jalan dengan aturan kata kunci saja: lebih kasar,
tetapi rilis data (CPI, NFP, dll.) tetap dibaca lewat jalur cepat tanpa AI.

**Penting:** langganan Claude.ai (Pro/Max) **terpisah** dari API. API dibayar per pemakaian
lewat Claude Console, dengan saldo (credit) sendiri.

1. Buka **https://console.anthropic.com** dan daftar (email atau akun Google). Ikuti
   verifikasi email (dan nomor HP bila diminta).
2. **Isi saldo:** menu **Settings → Billing** → *Add credits* / *Buy credits*. Masukkan kartu
   (Visa/Mastercard; kartu debit dengan fitur transaksi internasional biasanya bisa). Mulai
   kecil, mis. **US$5–10**, cukup untuk beberapa minggu pemakaian normal.
3. **Pasang batas biaya** supaya tagihan tidak pernah kebablasan: **Settings → Limits** → atur
   *spend limit* bulanan, mis. US$600 (= 30 hari × US$20). Selain itu program punya batas
   kerasnya sendiri: `budget.llm_daily_usd` (bawaan **US$20/hari**; ubah dengan mis.
   `uv run gold-agent config set budget.llm_daily_usd 10`). Berita biasa, outlook, dan laporan
   berhenti di 75 % batas itu; sisanya khusus untuk keputusan akhir sinyal dan berita berdampak
   tinggi. Setelah batas penuh tercapai, semua analisis memakai aturan dan sinyal keluar dengan
   tanda "Tanpa review AI" sampai 00:00 WIB.
4. **Buat key:** menu **API Keys** → **Create Key** → beri nama, mis. `gold-news-agent` →
   **Create**.
5. Key langsung tampil, diawali `sk-ant-`. **Salin sekarang**: key hanya ditampilkan sekali.
   Bila terlewat, hapus key itu dan buat yang baru.
6. Tulis di `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ```
7. **Uji key (opsional, Windows PowerShell)**: ganti `ISI_KEY_DI_SINI`, jalankan, lalu tutup
   PowerShell supaya key tidak tertinggal di layar:
   ```powershell
   $h = @{ "x-api-key" = "ISI_KEY_DI_SINI"; "anthropic-version" = "2023-06-01" }
   (Invoke-RestMethod https://api.anthropic.com/v1/models -Headers $h).data | Select-Object -First 3 id
   ```
   Bila muncul daftar nama model, key berfungsi. `401` artinya key salah. `400` dengan pesan
   soal billing/credit artinya saldo belum diisi.

Model yang dipakai sudah diatur di `config/settings.yaml` (bagian `llm`): triage tiap berita,
analisis mendalam, outlook per jam, dan ringkasan harian/mingguan memakai **Claude Opus 5.5**,
dengan tingkat `effort` berbeda per tugas. Keputusan akhir setiap sinyal dibuat komite tiga
suara: **Claude Fable 5.1** (model Anthropic paling mampu, harganya 2,5× Opus, jadi hanya dipakai
di sini; suaranya dihitung 2×) dan dua Opus 5.5. Biayanya ±$0,02 per berita biasa dan ±$0,45
per keputusan sinyal. Di hari yang sangat ramai biaya tanpa rem bisa ±$23, jadi
`budget.llm_daily_usd` (bawaan $20/hari) menahannya: setelah $15 berita biasa memakai aturan
sampai pukul 00:00 WIB, sedangkan keputusan sinyal dan berita berdampak tinggi tetap memakai AI
sampai $20. Biaya nyata tercatat di tabel `llm_usage` dan terlihat di header dashboard, laporan
harian, dan `/stats`.

Fable 5.1 mewajibkan Anthropic menyimpan data permintaan 30 hari (aturan standar akun biasa;
hanya organisasi dengan *zero data retention* yang ditolak). Isi permintaan program ini hanya
berita publik dan angka pasar, tanpa data akun atau rahasia. Bila Fable tidak bisa dipakai
akunmu, keputusan tetap jalan dengan dua Opus yang harus sepakat.

**Memantau pemakaian:** Console → **Usage** menampilkan biaya per hari.
**Bila key bocor:** Console → API Keys → klik key itu → **Delete** (atau *Disable*), buat yang
baru, ganti di `.env`, lalu restart program.

## D. Membaca channel berita Telegram (opsional)

Banyak berita tercepat (squawk) beredar di channel Telegram. Membacanya butuh akun Telegram
biasa (bukan bot) melalui Telethon.

1. Sebaiknya pakai **akun Telegram khusus** (nomor kedua), bukan akun pribadi utama.
2. Buka **https://my.telegram.org**, login dengan nomor akun itu (kode dikirim ke aplikasi
   Telegram).
3. Pilih **API development tools**. Isi formulir: *App title* bebas (mis. `gold reader`),
   *Short name* bebas (mis. `goldreader`), *Platform* `Desktop`. Simpan.
4. Catat **App api_id** (angka) dan **App api_hash** (32 karakter), lalu tulis di `.env`:
   ```
   TELEGRAM_API_ID=1234567
   TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
   ```
5. Pilih channel di `config/sources.yaml` → sumber `telegram_squawk` → isi `channels`, mis.
   `channels: [namachannel1, namachannel2]` (username tanpa `@`). Akun khusus tadi harus
   sudah bergabung ke channel itu.
6. Login sekali dari folder project: `uv run gold-agent telegram-login`. Masukkan nomor
   (format `+62...`), lalu kode dari Telegram, dan password 2FA bila ada. Hasilnya file
   `data/telegram/gold_agent.session`, yang **setara akses penuh ke akun itu**: jangan
   dibagikan atau di-upload. Bila bocor: Telegram → Settings → Devices → hentikan sesi itu.

## E. FRED (yield obligasi AS) — opsional, gratis

1. Buka **https://fredaccount.stlouisfed.org**, buat akun.
2. Menu **My Account → API Keys → Request API Key**, isi alasan singkat (mis. "personal
   research"), setujui syaratnya.
3. Tulis di `.env`: `FRED_API_KEY=` + key 32 karakter.

Data FRED harian (bukan real-time). Sistem memakainya untuk konteks yield 10 tahun.

## F. Twelve Data (harga XAU/USD cadangan) — opsional

1. Daftar di **https://twelvedata.com** (paket gratis ada batas request per menit/hari).
2. Dashboard → **API Keys** → salin key.
3. Tulis di `.env`: `TWELVEDATA_API_KEY=...`. Sumbernya (`twelvedata_xau_quote`) tetap harus
   `verified` di `config/sources.yaml` (langkah 7 di panduan pasang).

## G. MetaTrader 5 (opsional, khusus Windows)

1. Pasang terminal MT5 dari broker-mu (versi 64-bit), lalu login. **Gunakan akun demo dulu**.
   Sistem ini tidak pernah mengirim order, tetapi tetap jangan mencampur eksperimen dengan
   akun uang sungguhan.
2. Catat tiga hal dari jendela login MT5: nomor login, password, dan nama server
   (mis. `NamaBroker-Demo`). Tulis di `.env`:
   ```
   MT5_LOGIN=12345678
   MT5_PASSWORD=passwordmu
   MT5_SERVER=NamaBroker-Demo
   ```
3. Tidak perlu mengisi nama simbol: sumber harga bawaan `auto` memakai MT5 bila terminalnya
   terbuka dan mencari sendiri simbol emas broker (`XAUUSD`, `XAUUSDm`, `GOLD`, ...; yang ada di
   Market Watch didahulukan). Bila yang terpilih salah, tetapkan sendiri:
   `uv run gold-agent config set price_feed.mt5_symbol XAUUSDm`.
4. Dependensinya (`uv sync --extra mt5`) sudah dipasang installer Windows. Biarkan terminal MT5
   tetap terbuka dan login selama sistem jalan; bila MT5 ditutup, harga sementara memakai PAXG.

---

## Membuat dan mengisi file `.env` (dikerjakan di langkah 4)

`.env` adalah file teks biasa bernama persis `.env` (diawali titik, tanpa nama di depannya),
letaknya di folder project, mis. `C:\gold-news-agent\.env`. Program membacanya saat start.
Isinya baris-baris `NAMA=nilai`. Di folder project sudah ada contohnya: `.env.example`.

1. Buka PowerShell, masuk ke folder project: `cd C:\gold-news-agent`
2. Salin contohnya menjadi `.env`: `Copy-Item .env.example .env`
3. Buka untuk diedit: `notepad .env`
4. Cari baris yang sesuai, tempel nilainya **setelah tanda `=`**, tanpa spasi dan tanpa tanda
   kutip. Contoh: baris `TELEGRAM_BOT_TOKEN=` menjadi `TELEGRAM_BOT_TOKEN=1234567890:AAH-...`
5. Simpan (Ctrl+S) lalu tutup Notepad.
6. Cek namanya benar: `Get-ChildItem -Force .env*` harus menampilkan `.env` dan
   `.env.example`. Bila yang muncul `.env.txt`, ganti namanya: `Rename-Item .env.txt .env`
   (Notepad kadang menambah `.txt` diam-diam bila file dibuat lewat menu Save As).

Baris yang diawali `#` hanya keterangan dan boleh dibiarkan. Baris yang nilainya kosong
berarti fitur itu tidak dipakai.

## Contoh `.env` yang sudah terisi (nilai palsu)

```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
TELEGRAM_ADMIN_USER_IDS=987654321
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
FRED_API_KEY=
TWELVEDATA_API_KEY=
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
DATABASE_URL=
REDIS_URL=
DASHBOARD_PASSWORD=
```

Baris yang dibiarkan kosong berarti fitur itu tidak dipakai. Tidak perlu tanda kutip, dan
jangan ada spasi di sekitar `=`.

## Cek akhir sebelum lanjut ke langkah 1

- [ ] `getMe` dengan token bot menjawab `"ok":true`.
- [ ] Kamu sudah mengirim `/start` ke bot, dan chat ID + user ID sudah ditulis.
- [ ] (Bila memakai Claude) saldo Console terisi, spend limit dipasang, key tersimpan di `.env`.
- [ ] Tidak ada key yang tersimpan di tempat lain selain `.env` (dan pengelola password-mu).

Setelah program berjalan (langkah 9), kirim `/status` ke bot. Bila dibalas, Telegram sudah
benar. Status analisis AI terlihat di halaman **Kesehatan** dashboard.
