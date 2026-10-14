# Vendored front-end libraries

Served locally so the dashboard never loads code from a third-party CDN. The app itself is plain
JavaScript (no framework, no `eval`) so the page runs under a strict Content-Security-Policy.

| File | Package | License | npm integrity (tarball) |
|---|---|---|---|
| `lightweight-charts.standalone.production.js` | `lightweight-charts@5.2.1` (`dist/`) | Apache-2.0, see `LICENSE-lightweight-charts.txt` + `NOTICE-lightweight-charts.txt` | `sha512-IVwoK1RLFiLPubaKIjNbtjWLnpPMqiABSrTay6whmNa8L1+19292VtHJ+BWyPUuLCwF0tcQlhEWd1CLB2a1nsQ==` |

`SHA256SUMS` covers the copied files. The library's license asks for its attribution notice and a
link to https://www.tradingview.com/ on the page: the dashboard footer carries both. The in-chart
logo stays off because it injects an inline `<style>` that the page's strict CSP blocks.

Update: `npm pack <pkg>@<version>`, check the integrity against `npm view <pkg>@<version>
dist.integrity`, copy the same `dist/` file, refresh `SHA256SUMS` and this table.
