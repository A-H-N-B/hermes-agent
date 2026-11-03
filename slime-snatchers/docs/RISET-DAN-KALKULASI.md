# Riset Top 10 Game Roblox & Kalkulasi Pendapatan

Riset dilakukan **3 November 2026**. Angka pemain berubah tiap menit, jadi anggap ini sebagai
gambaran tren, bukan angka tetap. Sumber ada di bagian paling bawah.

---

## 1. Top 10 game Roblox (pemain bersamaan / CCU)

Snapshot RoWatcher, **1 November 2026 pukul 15:05 UTC** (hari Minggu). Saat itu total ada
**17,6 juta** orang bermain Roblox bersamaan di sekitar 20 ribu experience.

| # | Game | CCU | Genre | Cara utama game ini menghasilkan Robux |
|---|------|-----|-------|----------------------------------------|
| 1 | **Steal An Egg** | 1,1 jt | Steal-tycoon + menetaskan telur/pet | Game pass pengali permanen (2x pertumbuhan telur ±467 R$, 2x uang ±399 R$), skip timer per telur/per batch, video iklan berhadiah untuk speed. Rilis 25 Juli 2026. |
| 2 | Blox Fruits | 698 rb | Action RPG anime | Buah permanen, game pass 2x uang/mastery/drop, penyimpanan buah.* |
| 3 | Brookhaven RP | 628 rb | Roleplay sosial | Game pass rumah/kendaraan premium; dilaporkan dapat pemasukan iklan video berhadiah yang besar. |
| 4 | **Break and Steal an Egg** | 480 rb | Steal-tycoon | Pola sama dengan #1 (genre "steal").* |
| 5 | Murder Mystery 2 | 430 rb | Social deduction | Skin pisau/pistol, event musiman (Halloween 18 Okt–16 Nov 2026 mendorong lonjakan pemain).* |
| 6 | Adopt Me! | 280 rb | Koleksi pet + roleplay | Bucks, telur, potion, pass seperti Fly/Ride Pet (±195 R$), ekonomi trading. |
| 7 | Rivals | 273 rb | FPS kompetitif | Skin senjata, wrap, bundle. Termasuk game dengan pendapatan tertinggi 2026 (estimasi pihak ketiga ±$1,7–4,3 jt/bulan). |
| 8 | 99 Nights in the Forest | 270 rb | Survival horror co-op | Kelas/kit, mata uang premium, revive.* |
| 9 | DONT SPLASH | 154 rb | Horror obby (rilis Okt 2026) | Pack event (Halloween Pack 79 R$). |
| 10 | Jujutsu Shenanigans | 146 rb | Battlegrounds anime | Kosmetik/emote, server privat.* |

\* = pola monetisasi umum game tersebut yang sudah lama dikenal; detail harga bisa berubah.

### Kesimpulan tren
1. **Genre "Steal" mendominasi**: posisi #1 dan #4 adalah steal-tycoon (beli karakter → menghasilkan
   uang di base → orang lain bisa mencurinya). Genre ini viral karena ada drama sosial (mencuri,
   bertahan, balas dendam) yang enak dijadikan video TikTok/YouTube.
2. **Event musiman** (Halloween) membuat 8 dari 15 game teratas update nama "[🎃]" — update rutin =
   dorongan algoritma.
3. **Game besar tidak bergantung ke satu sumber**: game pass (sekali beli) + developer product
   (bisa dibeli berulang, sumber pendapatan terbesar) + iklan video berhadiah + server privat.

---

## 2. Apa yang membuat pemain mau belanja Robux (pola dari game teratas)

| Pemicu | Contoh di game top | Diterapkan di Slime Snatchers |
|--------|--------------------|-------------------------------|
| **Pengali permanen** (paling laku) | 2x Money, 2x egg growth | 2x Cash, VIP (+50%), Auto Collect, +6 Pads |
| **Kecepatan / skip** | Skip timer telur | Instant Buy slime langka yang terlihat di conveyor |
| **Status & pamer** | Tag VIP, kosmetik | Nametag & chat tag emas [VIP], pengumuman ke server |
| **Belanja sosial** (dibeli untuk semua) | Server luck, admin event | 2x Server Luck, Slime Rain — nama pembeli diumumkan |
| **Proteksi milik** (genre steal) | Kunci base | Base Shield, Mega Lock |
| **Hadiah ke teman** | Gifting pass | Gift 2x Cash / VIP / Auto Collect (diterima walau offline) |
| **Paket pemula bernilai tinggi** | Starter pack | Starter Pack 99 R$ (sekali per akun) |
| **Kembali tiap hari** | Daily reward | Login streak 7 hari, quest harian, offline earnings |
| **Iklan video berhadiah** | Speed gratis via video | Gratis 2x Cash 10 menit / gandakan offline cash |

---

## 3. Aturan Roblox yang wajib dipatuhi (agar game tidak kena moderasi)

- **Paid random items** (gacha/loot box yang dibeli langsung atau lewat mata uang berbayar): wajib
  menampilkan semua hasil dan peluangnya dalam persen yang totalnya 100%, termasuk efek item
  penambah luck. Ada `PolicyService` untuk pemain di wilayah yang membatasinya.
  → **Slime Snatchers tidak menjual item acak sama sekali.** Pemain selalu melihat slime sebelum
  membeli. Hadiah acak hanya yang gratis (daily/gift). Peluang spawn tetap ditampilkan lengkap di
  menu 🎲 Odds (termasuk efek Server Luck), sesuai aturan untuk "probability modifiers".
- **Countdown palsu dilarang**: jangan pakai timer "deal of the day" yang tidak akurat atau selalu
  ter-reset. → Game ini hanya memakai timer yang benar (durasi boost/lock).
- **Teks dari pemain wajib difilter** (TextService) → pengumuman admin difilter.
- **Iklan video berhadiah** hanya tampil untuk pemain 13+, dan Roblox mengatur ketersediaannya.

Mematuhi ini bukan cuma soal etika (mayoritas pemain anak-anak); game yang melanggar bisa dihapus,
dan pendapatannya jadi nol.

---

## 4. Ekonomi Roblox untuk developer (per November 2026)

| Komponen | Angka |
|----------|-------|
| Bagian developer dari game pass & developer product | **70%** (Roblox memotong 30%) |
| Kurs DevEx standar (Robux → USD) | **$0,0038 / Robux** (naik dari $0,0035 sejak 5 Sep 2025) |
| Kurs DevEx khusus | ±**$0,0054 / Robux** (+42%) untuk belanja dari pemain AS 18+ yang terverifikasi umur, di game R15, berlaku 8 Juni 2026 |
| Harga Robux untuk pemain (AS) | 400 R$ = $4,99 · 800 = $9,99 · 1.700 = $19,99 · 4.500 = $49,99 · 10.000 = $99,99 |
| Creator Rewards – Daily Engagement | 5 Robux/hari per *Active Spender* jika game kamu termasuk 3 game pertama yang ia mainkan hari itu ≥10 menit |
| Creator Rewards – Audience Expansion | 35% dari $100 pertama pembelian Robux oleh pemain baru/kembali dalam 2 bulan pertama (main ≥10 menit di game kamu) |
| Holding period Creator Rewards | 60 hari |
| Rewarded Video Ads | Roblox menargetkan awalnya hingga ~3% pendapatan, jangka panjang 5–10% |
| Roblox Plus ($4,99/bln, sejak 30 Apr 2026) | Pengganti Premium untuk pendaftar baru; game ini memberi bonus +10% untuk member |

---

## 5. Kalkulasi: berapa yang dibutuhkan untuk **$1.000/bulan**?

### Langkah 1 — Robux yang dibutuhkan
```
$1.000 ÷ $0,0038       = 263.158 Robux bersih (earned) per bulan
263.158 ÷ 70%          = 375.940 Robux kotor yang harus dibelanjakan pemain per bulan
375.940 ÷ 30 hari      = ± 12.531 Robux kotor per hari
```
Pemain secara total membayar sekitar **$3.760–$4.690 uang asli per bulan** ke Roblox agar kamu
menerima $1.000 (tergantung paket Robux yang mereka beli).

Jika ±15% belanja berasal dari pemain AS 18+ terverifikasi (kurs $0,0054), kurs campuran jadi
±$0,00404 → kebutuhan turun menjadi ±353.600 Robux kotor/bulan.

### Langkah 2 — Berapa pemain yang dibutuhkan
Rumus: `pendapatan/hari = DAU × konversi × ARPPU × 70% × kurs DevEx`

| Skenario | Konversi pembayar/hari | Belanja rata-rata per pembayar/hari | **DAU dibutuhkan** | CCU rata-rata* | CCU puncak (±2×) |
|----------|-----------------------|-------------------------------------|-------------------|----------------|------------------|
| Konservatif | 1,0% | 120 R$ | **10.443** | 218 | ±435 |
| **Realistis** | **2,0%** | **150 R$** | **4.177** | **87** | **±174** |
| Bagus | 3,0% | 200 R$ | **2.089** | 44 | ±87 |

\* asumsi rata-rata pemain bermain 30 menit/hari (CCU = DAU × 30 ÷ 1440).

Acuan konversi: di level platform, Roblox pernah melaporkan ±725 ribu pembayar unik harian dari
56 juta DAU (±1,3%). Genre simulator/tycoon umumnya di atas rata-rata; 2% adalah target yang wajar
untuk game dengan monetisasi rapi seperti ini.

### Langkah 3 — Tambahan (upside, belum dihitung di atas)
| Sumber | Perkiraan tambahan/bulan |
|--------|-------------------------|
| Creator Rewards (DAU 4.200, 20–30% active spender, 40–60% memenuhi syarat "3 game pertama") | ±$190–430 |
| Rewarded video ads | ±$30–100 |
| Server privat berbayar, Roblox Plus, subscription "Slime Club" | tergantung |

**Kesimpulan:** target realistis untuk $1.000/bulan adalah **±3.000–4.500 pemain harian
(±70–90 CCU rata-rata, ±150–180 CCU di jam ramai)**. Dengan Creator Rewards dan iklan, angka
DAU yang dibutuhkan bisa lebih kecil.

> ⚠️ **Jujur saja:** tidak ada kode yang bisa *menjamin* $1.000/bulan. Kode ini memberi "mesin"
> monetisasi yang lengkap dan patuh aturan. Yang menentukan hasil adalah **jumlah pemain**
> (ikon/thumbnail yang diklik, retensi, update rutin, promosi). Lihat
> [LAUNCH-DAN-MARKETING.md](LAUNCH-DAN-MARKETING.md) untuk rencana mendapatkan trafik tersebut.

### Simulasi pacing (dari `tools/`, model sederhana)
| | Pemain gratis | Pemilik 2x Cash |
|-|---------------|-----------------|
| Rebirth 1 | ±12 menit | ±8 menit |
| Rebirth 3 | ±32 menit | ±23 menit |
| Rebirth 5 | ±82 menit | ±55 menit |
Rebirth pertama tercapai di sesi pertama (bagus untuk retensi hari-1), dan game pass terasa
manfaatnya (±30% lebih cepat) tanpa membuat pemain gratis frustrasi.

---

## Sumber
- RoWatcher – live charts & player count: https://rowatcher.com/charts ,
  https://rowatcher.com/roblox-player-count
- RoWatcher – highest-earning games 2026:
  https://rowatcher.com/news/the-10-highest-earning-roblox-games-in-2026-and-what-they-mean-for-the-platform
- Steal An Egg (game pass & rilis): https://allthings.how/steal-an-egg-how-to-get-gamepasses-in-roblox/ ,
  https://www.player.one/robloxs-steal-egg-beating-some-platforms-biggest-games-164086
- DONT SPLASH: https://axeetech.com/?p=114563 , https://rowatcher.com/games/10771421858/dont-splash
- Rivals revenue estimates: https://profitable.app/roblox/games/rivals
- DevEx rates: https://axeetech.com/roblox-devex-rates/ ,
  https://www.netinfluencer.com/roblox-to-raise-developer-exchange-rate-for-games-aimed-at-18-plus-players-as-adult-cohort-surges/
- Creator Rewards: https://create.roblox.com/docs/creator-rewards ,
  https://www.mediapost.com/publications/article/406941/roblox-to-reward-creators-for-attracting-players.html
- Roblox Plus: https://about.roblox.com/en-au/newsroom/2026/04/introducing-roblox-plus-subscription ,
  https://devforum.roblox.com/t/introducing-roblox-plus/4567894/1
- Rewarded Video Ads: https://devforum.roblox.com/t/more-creators-can-now-use-rewarded-video-ads/3838678
- Paid random items policy: https://create.roblox.com/docs/production/monetization/paid-random-items
- Monetization guidance (timer palsu, price optimization): https://create.roblox.com/docs/production/monetization
- Harga Robux: https://rowatcher.com/news/robux-prices-bundles-2026-guide
- Benchmark konversi/ARPPU: https://www.sec.gov/Archives/edgar/data/1315098/000131509823000077/filename1.htm ,
  https://gamedevreports.substack.com/p/gameanalytics-key-roblox-and-roblox
