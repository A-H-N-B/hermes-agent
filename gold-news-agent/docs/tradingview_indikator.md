# Indikator TradingView "Gold News Agent"

Sinyal dari alat berita bisa digambar di grafik TradingView milikmu: kotak zona entry, garis SL,
TP1, TP2, TP3, sampai jam berlakunya habis, plus warna sesi London/New York, jeda harian
17:00–18:00 New York, dan garis rilis data AS 08:30 New York (sama dengan aturan alat ini).

## Menu Live: indikator lengkap dan "Kapan klik BUY / SELL"

- **Jam WIB real-time** di kanan atas (detik berjalan).
- **Kapan klik BUY / SELL**: saat ada sinyal tertulis *BUY/SELL SEKARANG* dengan zona, SL, TP
  dan hitung mundur sisa waktunya. Saat menunggu: alasannya, **jam larangan** di sekitar rilis
  data penting (15 menit sebelum sampai konfirmasi setelah rilis, sama dengan mesin sinyal),
  **jadwal peluang berikutnya** dari Outlook (jam WIB, arah, confidence, hitung mundur), dan
  **konfirmasi harga** (BUY bila tembus ke atas level X, SELL bila tembus ke bawah level Y).
- **Grafik** dengan EMA 20/50/200, Bollinger (20, 2), VWAP, support/resistance, RSI 14 di
  panel bawah, plus garis entry/SL/TP sinyal aktif; tiap indikator bisa dimatikan lewat centang.
- **Ringkasan indikator** per timeframe (M5, M15, H1, H4): BUY/SELL/NETRAL dari tren, posisi
  harga terhadap EMA, dan RSI. Ini hanya membaca grafik; angka yang dipakai tetap dari sinyal.

## Langsung di dashboard (tanpa salin-tempel)

Menu **TradingView** di dashboard (`http://127.0.0.1:8787/#tradingview`) menampilkan:

1. **Rencana trading**: angka persis untuk sinyal aktif. Arah (BUY/SELL), zona masuk, cara
   masuk (harga pasar / Limit / Stop), stop loss (= 1R), TP1 (ambil 50% lalu SL ke entry),
   TP2, TP3, lot, berita yang menjadi dasar, dan kapan rencana batal. Saat tidak ada sinyal
   tertulis **TUNGGU** beserta alasannya dan skenario 3 jam ke depan dari Outlook.
2. **Grafik indikator Gold News Agent**: grafik yang sama dengan menu Live (TradingView
   Lightweight Charts) dengan indikator digambar langsung: garis BUY/SELL, SL, TP1–TP3 berikut
   angkanya, warna sesi, tanda *Data AS* 08:30 New York, serta penanda berita dan sinyal.
   Di kanan candle terakhir ada **Proyeksi 24 jam**: ramalan alat berita yang digambar *di depan*
   harga. Arah tiap jam = bias Outlook (hijau naik, merah turun, abu-abu netral/TUNGGU DATA)
   dikali confidence; lebar pita = perkiraan rentang gerak jam itu (melebar di jam rilis data);
   selama ada sinyal aktif garisnya menuju TP1 sampai sinyal kedaluwarsa. Digambar ulang dari
   harga terakhir setiap candle baru dan setiap Outlook diperbarui. Ini perkiraan, bukan harga:
   menu Performa mencatat seberapa sering ramalan itu benar.
3. Di bawahnya grafik XAUUSD dari TradingView (widget resmi) dan tombol ke akun TradingView-mu.

Widget TradingView yang ditanam di website tidak bisa memuat indikator buatan sendiri (aturan
TradingView: hanya indikator bawaan). Karena itu indikator kita digambar di grafik nomor 2, dan
untuk aplikasi TradingView-mu sendiri dipakai script Pine di bawah.

## Batasnya (jujur)

Pine Script (bahasa indikator TradingView) **tidak bisa mengambil data dari luar TradingView**.
Jadi sinyal tidak bisa masuk sendiri: setiap ada sinyal, kamu menyalin satu baris **kode** lalu
menempelkannya di pengaturan indikator. Untuk sinyal yang masuk otomatis ke grafik, pakai
MetaTrader 5 (lihat bawah).

## Pasang (sekali)

1. Dashboard (`http://127.0.0.1:8787`) → menu **TradingView** → **Salin script indikator**.
   Script yang sama ada di `src/gold_agent/dashboard/static/gold_news_agent.pine`.
2. TradingView → **Pine Editor** (tab di bawah grafik) → **Open → New indicator** → hapus isinya
   → tempel (Ctrl+V) → **Save** (nama: Gold News Agent) → **Add to chart**.

## Setiap ada sinyal

1. Salin **Kode TradingView** dari pesan Telegram (ketuk baris kodenya) atau dari menu **Live**
   (tombol **Salin**). Bentuknya `GNA1|BUY|...`.
2. Di grafik: ikon roda gigi indikator Gold News Agent → **Kode sinyal** → tempel → **OK**.

## Pengaturan yang bisa diubah

| Pengaturan | Gunanya |
|---|---|
| Geser level ke harga chart ini | Sinyal dihitung dari harga PAXG (proxy). Selisih harga saat sinyal dibuat ditambahkan ke semua level supaya pas di XAUUSD broker. Matikan kalau sinyal sudah dari harga broker yang sama |
| Perpanjang garis setelah kedaluwarsa | Garis tetap terlihat beberapa menit setelah masa berlaku habis |
| Warna BUY/SELL/SL/TP, warna sesi | Selera tampilan |
| Sesi, jeda harian, garis data AS | Bisa dimatikan satu per satu |

Indikator ini hanya menggambar, tidak pernah membuka order. Bukan saran keuangan.
