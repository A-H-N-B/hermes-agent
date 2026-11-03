# Panduan Setup — dari file sampai game live

Waktu yang dibutuhkan: ±45–60 menit (sebagian besar untuk membuat produk di Creator Hub).

---

## Langkah 1 — Buka game di Roblox Studio
1. Install & login **Roblox Studio** (gratis): https://create.roblox.com
2. Di Studio: **File → Open from File…** → pilih `build/SlimeSnatchers.rbxlx`.
3. Tekan **Play (F5)**. Map, base, conveyor, UI akan dibuat otomatis oleh script.
   - Muncul pesan *"Studio test mode: data is NOT saved"*? Normal sebelum langkah 3.
   - Di Studio kamu otomatis **admin** (tombol 🛠️ Admin kiri atas) untuk mencoba event & spawn slime langka.

> Tidak bisa membuka `.rbxlx`? Lihat **Alternatif: copy-paste manual** di bawah.

## Langkah 2 — Publish
1. **File → Publish to Roblox As…** → *Create new experience* → nama: `Slime Snatchers`.
2. Buka **Home → Game Settings**:
   - **Security** → *Enable Studio Access to API Services* = **ON** (agar save data bisa dites di Studio).
   - **Places** → place kamu → **Max Players = 8** ⚠️ wajib (map punya 8 base).
   - **Avatar** → **R15** (syarat kurs DevEx lebih tinggi untuk belanja pemain 18+).
   - **Monetization** → *Private Servers*: Paid, ±100 R$/bulan (pendapatan berulang, opsional).
3. Publish ulang setiap selesai mengubah sesuatu: **File → Publish to Roblox** (Alt+P).

## Langkah 3 — Buat Game Pass & Developer Product
Buka https://create.roblox.com → **Creations** → game kamu → **Monetization**.
- *Passes* → **Create a Pass** (upload ikon apa saja, misalnya `marketing/icon.png`), lalu set harga di tab *Sales*.
- *Developer Products* → **Create a Developer Product**.

Salin **nama** & **deskripsi** di bawah (dalam bahasa Inggris agar menjangkau pemain global),
lalu catat **ID** tiap item.

### Game Passes
| Key di Config | Nama | Harga | Deskripsi |
|---------------|------|-------|-----------|
| `DoubleCash` | 2x Cash | 399 | Earn 2x cash from every slime, forever! |
| `VIP` | VIP | 299 | +50% cash, golden VIP name & chat tag, and 6 hours of offline earnings. |
| `ExtraPads` | +6 Slime Pads | 249 | 6 extra pads in your base so you can hold more slimes. |
| `FastSteal` | Master Thief | 199 | Steal 2x faster and run at full speed while carrying a slime. |
| `AutoCollect` | Auto Collect | 149 | Cash goes straight into your wallet — no need to step on the plates. |
| `LongLock` | Mega Lock | 149 | Your base lock lasts twice as long. |
| `Speed` | Turbo Shoes | 99 | Walk 25% faster. |

### Developer Products
| Key di Config | Nama | Harga | Deskripsi |
|---------------|------|-------|-----------|
| `CashSmall` | Cash Pouch | 25 | Instantly get 15 minutes of your current income (at least $1,000). The exact amount is shown in the shop. |
| `CashMedium` | Cash Bag | 99 | Instantly get 75 minutes of your current income (at least $12K). Exact amount shown in the shop. |
| `CashLarge` | Cash Vault | 299 | Instantly get 300 minutes of your current income (at least $150K). Exact amount shown in the shop. |
| `CashMega` | Cash Mountain | 799 | Instantly get 1000 minutes of your current income (at least $2M). Exact amount shown in the shop. |
| `ServerLuck` | 2x Server Luck | 99 | 2x luck for EVERYONE in the server for 15 minutes (time stacks). Exact odds are shown in the Odds menu. |
| `ServerRain` | Slime Rain | 79 | Slimes spawn 2x faster for everyone for 10 minutes (time stacks). |
| `CashBoost` | 2x Cash Potion | 49 | 2x cash for 30 minutes of play time (time stacks). |
| `Shield` | Base Shield | 39 | Nobody can steal from your base for 10 minutes of play time. |
| `StarterPack` | Starter Pack | 99 | One time only: 30 minutes of income (at least $25K), 2x Cash for 1 hour and a Golden Neon Noodle (Epic). |
| `OfflineDouble` | 2x Offline Cash | 19 | Doubles the cash your slimes earned while you were away. |
| `InstantEpic` | Instant Buy: Epic | 49 | Instantly get the Epic slime you picked on the belt, protected from thieves for 10 min. If that slime is no longer available you get Neon Noodle. |
| `InstantLegendary` | Instant Buy: Legendary | 99 | Same as above for Legendary slimes (fallback: Sir Squish). |
| `InstantMythic` | Instant Buy: Mythic | 199 | Same as above for Mythic slimes (fallback: Crystal Queen). |
| `InstantCelestial` | Instant Buy: Celestial | 399 | Same as above for Celestial slimes (fallback: Angel Ooze). |
| `InstantSecret` | Instant Buy: Secret | 799 | Same as above for Secret slimes (fallback: King Gloopus). |
| `GiftDoubleCash` | Gift: 2x Cash | 399 | Gift the 2x Cash pass to another player. |
| `GiftVIP` | Gift: VIP | 299 | Gift VIP to another player. |
| `GiftAutoCollect` | Gift: Auto Collect | 149 | Gift Auto Collect to another player. |
| `AdReward` | Free Reward | 5 | Reward for watching a short video: 2x cash for 10 minutes. |

> Tips: tidak harus membuat semuanya sekaligus. Item dengan `id = 0` otomatis tidak muncul di
> toko. Prioritas pertama: **DoubleCash, VIP, CashSmall, CashMedium, ServerLuck, StarterPack,
> InstantLegendary, InstantMythic, InstantSecret**.

## Langkah 4 — Tempel ID ke Config
Di Studio: **Explorer → ReplicatedStorage → Shared → Config** (double-click). Ganti `id = 0`
dengan ID dari Creator Hub, contoh:
```lua
DoubleCash = {
    id = 1234567890,   -- ← ID game pass kamu
    price = 399,
    ...
```
Atur juga (opsional): `Config.GroupId` (ID grup Roblox kamu), `Config.AdminUserIds`,
`Config.Codes`, `Config.SubscriptionId`. Lalu **Publish**.

## Langkah 5 — Opsional tapi menambah pemasukan
- **Rewarded Video Ads**: aktifkan di Creator Hub (menu Monetization/Ads) bila akun & game kamu
  eligible, lalu buat developer product `AdReward`. Tombol 📺 hanya muncul jika iklan tersedia.
- **Experience Subscription** (`Slime Club`): buat di Monetization → Subscriptions (butuh
  verifikasi), tempel ID-nya (format `EXP-…`) ke `Config.SubscriptionId`.
- **Grup Roblox**: buat grup, isi `Config.GroupId`. Anggota dapat +10% cash, dan tombol Join
  Group muncul di menu 🔑 Codes. Grup juga jadi tempat mengumumkan kode & admin event.

## Langkah 6 — Halaman game & kepatuhan
Di Creator Hub → game kamu:
1. **Basic Info**: judul, deskripsi (template di [LAUNCH-DAN-MARKETING.md](LAUNCH-DAN-MARKETING.md)),
   genre (*Simulation* / *Tycoon*).
2. **Places → Icon & Thumbnails**: upload `marketing/icon.png` & `marketing/thumbnail.png`.
3. **Maturity & Compliance questionnaire**: wajib diisi jujur (game ini: kartun, ada pukulan
   palu tanpa darah/cedera).
4. **Permissions → Public** ketika siap rilis.

## Langkah 7 — Tes sebelum rilis (checklist)
Studio → tab **Test** → *Clients and Servers* → **2 Players** → Start.
- [ ] Beli slime di conveyor (E / tap), cash berkurang, slime muncul di base.
- [ ] Injak pelat hijau → cash masuk + teks "+$" melayang.
- [ ] Injak tombol merah → base terkunci (pemain lain terpental keluar).
- [ ] Player 2 mencuri slime Player 1 → bawa ke base sendiri → slime pindah.
- [ ] Player 1 memukul pencuri dengan Bonk Hammer → slime kembali.
- [ ] Rebirth (pakai Admin `+$1M`), fuse 3 slime sama, klaim daily/gift/quest, redeem kode `RELEASE`.
- [ ] Keluar lalu masuk lagi → data tersimpan + popup "Welcome back" (butuh API Services ON).
- [ ] Pembelian test: di Studio pembelian disimulasikan (gratis) → cek hadiah diterima.

---

## Alternatif: copy-paste manual (tanpa file .rbxlx)
Buat struktur ini di Explorer Studio, lalu copy isi file dari folder `src/`:

| Di Studio | Tipe objek | File sumber |
|-----------|------------|-------------|
| `ReplicatedStorage/Shared` | Folder | — |
| `ReplicatedStorage/Shared/<Nama>` | ModuleScript | `src/ReplicatedStorage/Shared/<Nama>.luau` (10 file) |
| `ServerScriptService/Main` | Script | `src/ServerScriptService/Main.server.luau` |
| `ServerScriptService/Services` | Folder | — |
| `ServerScriptService/Services/<Nama>` | ModuleScript | `src/ServerScriptService/Services/<Nama>.luau` (14 file) |
| `StarterPlayer/StarterPlayerScripts/Client` | **LocalScript** | `src/StarterPlayerScripts/Client/init.client.luau` |
| `…/Client/<Nama>` (anak dari LocalScript Client) | ModuleScript | `src/StarterPlayerScripts/Client/<Nama>.luau` (11 file) |
| `…/Client/Panels` | Folder (anak Client) | — |
| `…/Client/Panels/<Nama>` | ModuleScript | `src/StarterPlayerScripts/Client/Panels/<Nama>.luau` (5 file) |

Nama objek = nama file tanpa `.luau` / `.server.luau`. Lalu di **Workspace** set
`StreamingEnabled = false` dan di **Players** set `CharacterAutoLoads = false`.

Atau pakai **Rojo** (untuk developer): `rojo serve default.project.json` + plugin Rojo di Studio.

---

## Troubleshooting
| Masalah | Solusi |
|---------|--------|
| Pemain tidak muncul / spawn | Pastikan `Players.CharacterAutoLoads = false` dan Script `Main` ada di ServerScriptService. |
| Data tidak tersimpan di Studio | Game Settings → Security → Enable Studio Access to API Services. |
| Toko kosong | ID di Config masih 0, atau belum Publish setelah mengubah Config. |
| Output: "no handler for product …" | ID developer product itu belum ditempel di `Config.Products`. |
| Server penuh tapi ada pemain ke-9 | Max Players harus 8. |
| Mengubah keseimbangan game | Semua angka ada di `Config.Tuning`, `Slimes.luau`, `Rarities.luau`. |

## Untuk developer: verifikasi kode
```bash
python3 tools/luau_check.py src     # syntax + scope + validasi API Roblox
python3 tools/xref_check.py src     # cek pemanggilan antar-modul
python3 tools/build_place.py default.project.json -o build/SlimeSnatchers.rbxlx
```
Detail di [tools/README.md](../tools/README.md).
