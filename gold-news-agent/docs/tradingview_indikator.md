# Indikator TradingView "Gold News Agent"

Sinyal dari alat berita bisa digambar di grafik TradingView milikmu: kotak zona entry, garis SL,
TP1, TP2, TP3, sampai jam berlakunya habis, plus warna sesi London/New York, jeda harian
17:00–18:00 New York, dan garis rilis data AS 08:30 New York (sama dengan aturan alat ini).

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
