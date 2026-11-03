# Tools

Semua tool ditulis khusus untuk proyek ini (Python 3, tanpa dependensi kecuali Pillow untuk gambar).

| Tool | Fungsi |
|------|--------|
| `luau_check.py` | Parser Luau lengkap (termasuk type annotation, string interpolation, compound assignment) + analisis scope (global tak dikenal, variabel tak terpakai) + validasi API Roblox: nama class di `Instance.new`, nama service di `GetService`, item `Enum.X.Y`, properti/method pada variabel yang class-nya diketahui, key properti di helper `New("Class", {...})`, dan peringatan API yang sudah deprecated. |
| `xref_check.py` | Memastikan setiap `Modul.fungsi` / `S.Service.fungsi` yang dipanggil benar-benar didefinisikan di modul tujuannya. |
| `build_place.py` | Membuat file place `.rbxlx` dari `default.project.json` (format proyek Rojo). |
| `make_art.py` | Membuat `marketing/icon.png` (512×512) dan `marketing/thumbnail.png` (1920×1080). |

## Pemakaian
```bash
python3 tools/luau_check.py src
python3 tools/xref_check.py src
python3 tools/build_place.py default.project.json -o build/SlimeSnatchers.rbxlx
python3 tools/make_art.py
```

`luau_check.py` membutuhkan file definisi tipe Roblox `tools/roblox-types.d.luau` (tidak ikut
di-commit karena ukurannya ±0,9 MB). Unduh dari proyek luau-lsp (lisensi MIT):
```bash
curl -L -o tools/roblox-types.d.luau https://raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.d.luau
```
Tanpa file itu, cek sintaks & scope tetap jalan; hanya validasi API Roblox yang dimatikan.

Proyek ini juga kompatibel dengan toolchain standar Roblox: `rojo build default.project.json -o game.rbxlx`,
`selene`, `stylua`, dan `luau-lsp`.
