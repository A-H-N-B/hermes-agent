# Launch & Marketing — rencana menuju $1.000/bulan

Target dari [kalkulasi](RISET-DAN-KALKULASI.md): **±3.000–4.500 pemain harian** (±70–90 CCU
rata-rata). Algoritma Roblox merekomendasikan game yang (1) sering diklik dan (2) membuat pemain
bertahan lama & kembali. Rencana ini fokus ke dua hal itu.

---

## 1. Halaman game (copy-paste)

**Judul** (pendek, ada kata kunci genre; ganti tag saat update):
```
[🆕] Slime Snatchers 🟢 Steal & Collect!
```
Saat update: `[UPD 2] Slime Snatchers 🟢` · saat event: `[🎃 HALLOWEEN] Slime Snatchers`.

**Deskripsi:**
```
🟢 Buy slimes from the Slime Pipe, grow them in your base and get RICH!
😈 Sneak into other bases and STEAL their best slimes!
🔨 Bonk thieves with your hammer to get your slimes back.
🔒 Lock your base, ✨ fuse 3 slimes into rare mutations, ⭐ rebirth for huge multipliers!

✨ 24 slimes · 8 rarities (up to SECRET) · 5 mutations (Golden, Diamond, Rainbow, Galaxy!)
🍀 Server events: Lucky Hour, Golden Hour, Slime Rain, Goo Moon
🎁 Daily rewards, playtime gifts & daily quests

🔑 CODES: RELEASE · SLIMY · SNATCH
👥 Join the group for +10% cash!
👍 Like & ⭐ favorite for new codes!
```

**Ikon & thumbnail:** `marketing/icon.png` dan `marketing/thumbnail.png` (dibuat dengan
`tools/make_art.py`). Setelah game jalan, ganti/tambahkan screenshot asli dari Studio:
slime Secret/Galaxy di tengah + teks besar. Uji 2–3 versi ikon (Creator Hub punya A/B test
thumbnail) dan pertahankan yang CTR-nya paling tinggi.

## 2. Checklist sebelum Public
- [ ] Semua tes di [PANDUAN-SETUP.md](PANDUAN-SETUP.md) lolos dengan 2 pemain.
- [ ] Minimal 6 produk utama punya ID (2x Cash, VIP, Cash Pouch, Cash Bag, Server Luck, Starter Pack).
- [ ] Max Players = 8, Avatar R15, questionnaire maturity terisi.
- [ ] Grup Roblox dibuat + `Config.GroupId` diisi.
- [ ] Main di HP (Studio → Test → Device emulator) — semua tombol bisa ditekan.

## 3. 30 hari pertama
| Kapan | Aksi |
|-------|------|
| Hari 0 | Rilis Public, ajak teman main bareng (bonus friend +5%), cek Output/Analytics untuk error. |
| Hari 1–7 | **Roblox Ads Manager**: iklan Sponsored kecil ($5–20/hari) untuk data awal. Pantau biaya per kunjungan & retensi. Matikan iklan yang mahal, perbesar yang murah. |
| Setiap hari | 1–3 video pendek (TikTok/YouTube Shorts/Reels): "Mencuri slime SECRET!", "Aku dapat Galaxy slime", "Admin abuse event". Konten genre steal sangat mudah viral. |
| Setiap Sabtu | **Admin event** live (panel 🛠️: Luck x10, spawn Secret, Golden). Umumkan jamnya di grup & deskripsi (mis. 19:00 WIB dan 19:00 waktu AS). Ini menaikkan CCU drastis di game steal. |
| Tiap 1–2 minggu | **Update**: 3 slime baru / 1 mutasi / event musiman + kode baru. Ganti tag judul `[UPD x]`. |
| Hari 14 | Evaluasi Analytics (lihat KPI). Perbaiki langkah onboarding yang paling banyak ditinggal. |
| Hari 30 | Jika CCU stabil > 50: naikkan budget iklan; tambahkan rarity baru di atas Secret. |

## 4. KPI yang dipantau (Creator Hub → Analytics)
| Metrik | Target sehat | Jika di bawah target |
|--------|--------------|----------------------|
| CTR ikon (home recommendations) | > 4% | Ganti ikon/thumbnail, judul lebih jelas |
| Retensi D1 | > 20% | Perbaiki tutorial; cek funnel onboarding (event `Step1…Step5` sudah dikirim otomatis) |
| Retensi D7 | > 5% | Tambah konten, event mingguan, quest |
| Rata-rata sesi | > 15 menit | Tambah tujuan jangka menengah (index, fuse, rebirth) |
| Konversi pembayar | > 1,5% | Cek Starter Pack & harga; aktifkan Price Optimization |
| ARPPU | > 120 R$ | Tambah produk bernilai besar (rarity baru, bundle event) |

Game ini sudah mengirim event analytics otomatis: economy (cash masuk/keluar per sumber),
onboarding funnel, progression (rebirth), dan custom event `PromptPass_*`, `PromptProduct_*`,
`Purchase_*` sehingga kamu bisa melihat produk mana yang paling sering dilihat vs dibeli.

## 5. Optimasi harga (setelah ±2 minggu data)
- Aktifkan **Price Optimization** dan **regional pricing** di Creator Hub untuk developer
  product (Roblox akan menguji harga otomatis).
- Jangan menurunkan harga game pass yang sudah dibeli banyak orang secara drastis (pembeli lama
  kecewa); lebih baik buat bundle/event baru.

## 6. Ide update berikutnya (urut prioritas)
1. Rarity baru di atas Secret (mis. "Omega") + 3 slime → memperpanjang endgame.
2. Event musiman (Halloween/Natal): slime edisi event yang hanya muncul selama event (jujur
   tentang tanggal berakhirnya — tidak memakai countdown palsu).
3. Trading antar pemain (cek `PolicyService` untuk pemain yang dibatasi).
4. Leaderboard mingguan dengan hadiah kosmetik.
5. Lebih banyak gamepass kosmetik (efek jejak, warna nametag) — status sosial laku keras.

## 7. Hal yang sebaiknya dihindari
- Menjual item acak (gacha) dengan Robux — rumit secara aturan dan berisiko di banyak negara.
- Timer/diskon palsu, harga menyesatkan, atau tekanan "beli sekarang atau rugi".
- Membeli bot/fake visit — melanggar aturan dan bisa membuat game dihapus.
Pendekatan yang jujur membuat pemain (dan orang tua mereka) percaya, dan itu yang membuat game
bertahan lama.
