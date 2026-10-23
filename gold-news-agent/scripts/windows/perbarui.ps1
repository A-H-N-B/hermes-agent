# Gold News Agent - update to the newest version without losing your settings.
#
# Desktop shortcut "Perbarui Gold News Agent", or the first time (before that shortcut exists),
# in a normal Windows PowerShell:
#   powershell -NoExit -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/A-H-N-B/hermes-agent/claude/prompt-execution-repo-setup-7e735z/gold-news-agent/scripts/windows/perbarui.ps1 | iex"
#
# Stops the agent, moves any edit made to config\settings.yaml or config\sources.yaml into this
# computer's own config\local.yaml / config\sources.local.yaml (the full diff is kept in
# data\backups), pulls the new version, installs its dependencies, checks that the config still
# loads, tests the official data sources the new version reads and switches on the ones that
# answer from this computer, then starts the agent again. .env, data\ and the local config files
# are never touched. On any failure before the new version is in place, the old version is
# started again.
# Kept ASCII-only: Windows PowerShell 5.1 reads `irm | iex` text without an encoding hint.

function Update-GoldNewsAgent {
    $ErrorActionPreference = 'Continue'
    $Branch = 'claude/prompt-execution-repo-setup-7e735z'
    $Base = 'C:\hermes-agent'
    $Root = Join-Path $Base 'gold-news-agent'
    $Win = Join-Path $Root 'scripts\windows'
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
    $Exe = Join-Path $Root '.venv\Scripts\gold-agent.exe'
    # US government data behind the daily fundamentals (public domain, no key). A source that
    # does not answer from this computer stays off and is tried again on the next update.
    $Official = @('treasury_yield_curve', 'treasury_real_yield_curve', 'cftc_cot_gold')
    # The price without MT5 (price_feed.primary binance_paxg, and the fallback with MT5): gold
    # as PAXG and the dollar as EURUSDT from Binance's public market-data hosts.
    $Market = @('binance_paxg_ws', 'binance_paxg_rest', 'binance_eurusdt_ws')
    $Tested = $Official + $Market

    function Step($n, $text) { Write-Host ""; Write-Host "=== LANGKAH $n/7: $text ===" -ForegroundColor Cyan }
    function Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
    function Warn($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
    function Fail($text) { Write-Host "  XX  $text" -ForegroundColor Red }
    function Start-Agent { & (Join-Path $Win 'jalankan.cmd') }

    if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
        Fail "Program tidak ditemukan di $Root. Pasang dulu dengan perintah instalasi."
        return
    }
    $env:Path = "$env:ProgramFiles\Git\cmd;$env:USERPROFILE\.local\bin;$env:Path"

    Step 1 'Mematikan program'
    cmd /c 'taskkill /F /T /FI "WINDOWTITLE eq Gold News Agent*" >nul 2>&1'
    Get-Process gold-agent -ErrorAction SilentlyContinue | Stop-Process -Force
    Ok 'Program berhenti'

    Step 2 'Mengambil versi terbaru'
    git -C $Base fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { Fail 'Gagal mengambil update. Cek internet lalu coba lagi.'; Start-Agent; return }
    $behind = git -C $Base rev-list --count "HEAD..origin/$Branch"
    Ok "$behind perubahan baru"

    Step 3 'Mengamankan pengaturan Anda'
    # The NEW version of the tool, run before switching to it.
    $Tool = Join-Path $env:TEMP 'gna_local_config.py'
    cmd /c "git -C `"$Base`" show origin/${Branch}:gold-news-agent/src/gold_agent/local_config.py > `"$Tool`""
    if ($LASTEXITCODE -ne 0) { Fail 'Alat update tidak bisa diambil.'; Start-Agent; return }
    & $Python -I $Tool --config-dir (Join-Path $Root 'config') migrate --backups (Join-Path $Root 'data\backups')
    if ($LASTEXITCODE -ne 0) { Fail 'Pengaturan tidak bisa diamankan, update dibatalkan (program lama tetap dipakai).'; Start-Agent; return }
    Ok 'Pengaturan Anda ada di config\local.yaml dan config\sources.local.yaml'

    Step 4 'Memasang versi terbaru (1-3 menit)'
    if (git -C $Base status --porcelain --untracked-files=no) {
        git -C $Base stash push -m "perbarui $(Get-Date -Format s)" | Out-Null
        Warn 'File program lain yang diubah disimpan di git stash (tidak hilang).'
    }
    git -C $Base merge --ff-only "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { Fail 'Versi baru tidak bisa dipasang (riwayat git berbeda).'; Start-Agent; return }
    Set-Location $Root
    uv sync --extra mt5
    if ($LASTEXITCODE -ne 0) {
        Warn 'Paket MetaTrader 5 tidak tersedia di laptop ini (mis. Windows ARM): harga memakai PAXG.'
        uv sync
    }
    if ($LASTEXITCODE -ne 0) { Fail 'uv sync gagal. Kirim tulisan merah di atas ke yang membantu Anda.'; return }
    Ok 'Versi terbaru terpasang'

    Step 5 'Memeriksa pengaturan'
    # Settings this version no longer has are dropped from config\local.yaml (and listed).
    & $Exe config repair
    if ($LASTEXITCODE -ne 0) {
        Fail 'Pengaturan tidak valid untuk versi baru (lihat tulisan merah: nama field-nya disebut).'
        Fail 'Perbaiki atau hapus baris itu di config\local.yaml, lalu klik "Jalankan Gold News Agent".'
        return
    }
    Ok 'Pengaturan valid'

    Step 6 'Menguji sumber data (Treasury AS, CFTC, harga Binance)'
    # The report goes to data\ so git sees no changed file on the next update.
    $Report = Join-Path $Root 'data\source_report_update.json'
    Remove-Item $Report -ErrorAction SilentlyContinue
    & $Exe verify-sources --only ($Tested -join ',') --note 'update' --out-md (Join-Path $Root 'data\source_report_update.md') --out-json $Report
    $passed = @()
    if (Test-Path $Report) {
        $passed = @((Get-Content $Report -Raw | ConvertFrom-Json).results | Where-Object { $_.status -in 'ok', 'warn' } | ForEach-Object { $_.source_id })
    }
    if ($passed.Count) {
        & $Exe config verified @passed
        if ($LASTEXITCODE -eq 0) { Ok "Aktif: $($passed -join ', ')" }
        else { Warn 'Sumber tidak bisa diaktifkan (lihat tulisan merah di atas).' }
    }
    $failed = @($Tested | Where-Object { $passed -notcontains $_ })
    if ($failed.Count) { Warn "Belum terjangkau dari jaringan ini (dicoba lagi saat update berikutnya): $($failed -join ', ')" }

    Step 7 'Menyalakan program'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Perbarui Gold News Agent.lnk'))
    $link.TargetPath = Join-Path $Win 'perbarui.cmd'
    $link.WorkingDirectory = $Root
    $link.Save()
    Start-Agent
    Write-Host ''
    Write-Host '==================== UPDATE SELESAI ====================' -ForegroundColor Green
    Write-Host ' Update berikutnya: klik shortcut "Perbarui Gold News Agent" di Desktop.'
    Write-Host '========================================================' -ForegroundColor Green
}

Update-GoldNewsAgent
