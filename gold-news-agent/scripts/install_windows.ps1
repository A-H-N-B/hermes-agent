# Gold News Agent - one-command installer for a fresh Windows laptop.
#
# Run in a normal (not Administrator) Windows PowerShell:
#   irm https://raw.githubusercontent.com/A-H-N-B/hermes-agent/claude/prompt-execution-repo-setup-7e735z/gold-news-agent/scripts/install_windows.ps1 | iex
#
# Installs git + uv when missing, downloads only the gold-news-agent folder, asks for the
# Telegram bot token and Claude API key (hidden input, checked against both services before
# saving), finds the Telegram chat ID itself, activates the public sources that pass
# verify-sources from this machine, then starts the program on every Windows login.
# Kept ASCII-only: Windows PowerShell 5.1 reads `irm | iex` text without an encoding hint.

function Install-GoldNewsAgent {
    $ErrorActionPreference = 'Stop'
    $Repo = 'https://github.com/A-H-N-B/hermes-agent.git'
    $Branch = 'claude/prompt-execution-repo-setup-7e735z'
    $Base = 'C:\hermes-agent'
    $Root = Join-Path $Base 'gold-news-agent'
    $Sources = @(
        'binance_paxg_ws', 'binance_paxg_rest', 'binance_eurusdt_ws', 'ff_calendar_week',
        'fed_press_all', 'fed_press_monetary', 'fed_speeches', 'bls_latest', 'bea_releases',
        'ecb_press', 'boj_whatsnew', 'google_news_gold', 'google_news_fed', 'cnbc_economy',
        'gdelt_gold', 'cftc_cot_gold', 'bls_schedule_ics', 'treasury_auctions_upcoming',
        'fomc_calendar_page'
    )

    function Step($n, $text) { Write-Host ""; Write-Host "=== LANGKAH $n/9: $text ===" -ForegroundColor Cyan }
    function Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
    function Warn($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
    function Fail($text) { Write-Host "  XX  $text" -ForegroundColor Red }
    function Read-Secret($prompt) {
        $secure = Read-Host $prompt -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }
    function Set-EnvLine([string]$text, [string]$name, [string]$value) {
        return [regex]::Replace($text, "(?m)^$name=[^\r\n]*", { param($m) "$name=$value" })
    }

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    Step 1 'Pasang git'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail 'winget tidak ada. Pasang Git manual dari https://git-scm.com/download/win lalu jalankan ulang.'
            return
        }
        winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
        $env:Path = "$env:ProgramFiles\Git\cmd;$env:Path"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git belum terbaca. Tutup PowerShell, buka lagi, jalankan ulang.'; return }
    Ok (git --version)

    Step 2 'Pasang uv (pengelola Python)'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail 'uv belum terbaca. Tutup PowerShell, buka lagi, jalankan ulang.'; return }
    Ok (uv --version)

    Step 3 'Unduh program'
    Get-Process gold-agent -ErrorAction SilentlyContinue | Stop-Process -Force
    if (Test-Path (Join-Path $Root 'pyproject.toml')) {
        Ok "Sudah ada di $Root (tidak diunduh ulang)"
    } else {
        if (Test-Path $Base) { Fail "$Base sudah ada tapi bukan program ini. Ganti nama/hapus folder itu dulu."; return }
        git clone --filter=blob:none --sparse --branch $Branch $Repo $Base
        if ($LASTEXITCODE -ne 0) { Fail 'Gagal mengunduh. Cek internet lalu jalankan ulang.'; return }
        git -C $Base sparse-checkout set gold-news-agent
        if ($LASTEXITCODE -ne 0) { Fail 'Gagal mengambil folder program.'; return }
        Ok "Diunduh ke $Root"
    }
    Set-Location $Root

    Step 4 'Pasang dependensi (2-5 menit)'
    uv sync
    if ($LASTEXITCODE -ne 0) { Fail 'uv sync gagal. Kirim tulisan merah di atas ke yang membantu Anda.'; return }
    $Exe = Join-Path $Root '.venv\Scripts\gold-agent.exe'
    Ok 'Dependensi terpasang'

    Step 5 'Token bot Telegram, Chat ID, API key Claude'
    $EnvPath = Join-Path $Root '.env'
    $fill = $true
    if (Test-Path $EnvPath) {
        $fill = (Read-Host '  .env sudah ada. Isi ulang token dan key? (y/N)') -match '^[yY]'
    } else {
        Copy-Item (Join-Path $Root '.env.example') $EnvPath
    }
    if ($fill) {
        $text = [IO.File]::ReadAllText($EnvPath)
        Write-Host '  Token bot: buka @BotFather > /mybots > pilih bot > API Token > salin.' -ForegroundColor Gray
        Write-Host '  Tempel dengan KLIK KANAN. Yang terlihat hanya bintang (*), itu normal.' -ForegroundColor Gray
        while ($true) {
            $token = Read-Secret '  Tempel token bot'
            if ($token -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') { Fail "Format salah (panjang $($token.Length), harus +-46 karakter angka:huruf). Salin ulang token LENGKAP."; continue }
            try { $me = (Invoke-RestMethod "https://api.telegram.org/bot$token/getMe").result; break }
            catch { Fail 'Telegram menolak token ini (sudah di-revoke / salah). Ambil lagi di BotFather.' }
        }
        Ok "Token diterima. Bot: @$($me.username)"
        $botId = $token.Split(':')[0]

        $chat = $null
        try { Invoke-RestMethod "https://api.telegram.org/bot$token/deleteWebhook" | Out-Null } catch { }
        Write-Host ''
        Write-Host "  Di HP buka https://t.me/$($me.username) lalu tekan START (atau kirim /start)." -ForegroundColor Yellow
        Write-Host '  Jangan ketik apa pun di sini, tunggu saja...' -ForegroundColor Yellow
        foreach ($try in 1..3) {
            try { $updates = (Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates?timeout=50" -TimeoutSec 65).result }
            catch { Warn 'Bot ini sedang dipakai program lain (komputer lain?). Matikan program itu.'; break }
            $msg = $updates | Where-Object { $_.message -and $_.message.chat.type -eq 'private' } | Select-Object -Last 1
            if ($msg) { $chat = [string]$msg.message.from.id; break }
            Warn "Belum ada pesan masuk ($try/3). Pastikan /start dikirim ke @$($me.username)."
        }
        while (-not $chat) {
            $typed = (Read-Host '  Ketik angka Id dari @userinfobot (buka @userinfobot > Start)').Trim()
            if ($typed -notmatch '^\d+$') { Fail 'Harus angka saja.'; continue }
            if ($typed -eq $botId) { Fail 'Itu ID BOT, bukan ID akun Anda.'; continue }
            $chat = $typed
        }
        Ok "Chat ID: $chat"

        Write-Host ''
        Write-Host '  API key Claude: console.anthropic.com > API Keys > Create Key > salin (sk-ant-...).' -ForegroundColor Gray
        Write-Host '  Kosongkan lalu Enter kalau belum punya (analisis memakai aturan saja).' -ForegroundColor Gray
        while ($true) {
            $key = Read-Secret '  Tempel API key Claude'
            if (-not $key) { Warn 'Tanpa API key Claude.'; break }
            try {
                Invoke-RestMethod 'https://api.anthropic.com/v1/models' -Headers @{ 'x-api-key' = $key; 'anthropic-version' = '2023-06-01' } | Out-Null
                Ok 'API key Claude diterima'; break
            } catch {
                $code = $null
                if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
                if ($code -eq 401 -or $code -eq 403) { Fail 'API key ditolak. Salin ulang dari console.anthropic.com.'; continue }
                Warn 'Tidak bisa mengecek API key sekarang; tetap disimpan.'; break
            }
        }
        $text = Set-EnvLine $text 'TELEGRAM_BOT_TOKEN' $token
        $text = Set-EnvLine $text 'TELEGRAM_CHAT_ID' $chat
        $text = Set-EnvLine $text 'TELEGRAM_ADMIN_USER_IDS' $chat
        $text = Set-EnvLine $text 'ANTHROPIC_API_KEY' $key
        [IO.File]::WriteAllText($EnvPath, $text)
        Ok '.env tersimpan'
    }

    Step 6 'Email kontak dan file log'
    $SettingsPath = Join-Path $Root 'config\settings.yaml'
    $settings = [IO.File]::ReadAllText($SettingsPath)
    if ($settings -match '(?m)^[ \t]*contact_email:[ \t]*null') {
        do { $email = (Read-Host '  Ketik email kontak (untuk situs data resmi AS)').Trim() } while ($email -notmatch '^[^@\s"]+@[^@\s"]+\.[^@\s"]+$')
        $settings = [regex]::Replace($settings, '(?m)^[ \t]*contact_email:[^\r\n]*', { param($m) "  contact_email: `"$email`"" })
    }
    $settings = [regex]::Replace($settings, '(?m)^[ \t]*file: null[^\r\n]*', '  file: data/logs/gold-agent.log')
    [IO.File]::WriteAllText($SettingsPath, $settings)
    & (Join-Path $Root '.venv\Scripts\python.exe') -c 'from gold_agent.config import load_settings; load_settings()'
    if ($LASTEXITCODE -ne 0) { Fail 'config\settings.yaml tidak valid (lihat tulisan merah di atas).'; return }
    Ok 'Config tersimpan'

    Step 7 'Tes dan aktifkan sumber data (1-3 menit, layar diam itu normal)'
    & $Exe verify-sources --only ($Sources -join ',') --note 'install'
    $report = Get-Content (Join-Path $Root 'docs\source_report.json') -Raw | ConvertFrom-Json
    $passed = @($report.results | Where-Object { $_.status -in 'ok', 'warn' } | ForEach-Object { $_.source_id })
    $SourcesPath = Join-Path $Root 'config\sources.yaml'
    $yaml = [IO.File]::ReadAllText($SourcesPath)
    foreach ($id in $passed) {
        $yaml = [regex]::Replace($yaml, "(?s)(- id: $id\r?\n(?:(?!- id:).)*?status: )candidate", '${1}verified', 1)
    }
    [IO.File]::WriteAllText($SourcesPath, $yaml)
    Ok "Diaktifkan $($passed.Count) sumber: $($passed -join ', ')"
    if ($passed -notcontains 'binance_paxg_ws' -and $passed -notcontains 'binance_paxg_rest') {
        Warn 'Sumber harga Binance gagal dari jaringan ini: harga akan "tidak tersedia".'
    }

    Step 8 'Laptop tidak tidur, jam tepat, nyala otomatis'
    try { powercfg /change standby-timeout-ac 0 | Out-Null; powercfg /change hibernate-timeout-ac 0 | Out-Null } catch { }
    try {
        Write-Host '  Klik YES pada jendela izin (untuk menyetel jam).' -ForegroundColor Gray
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', 'w32tm /config /manualpeerlist:pool.ntp.org /syncfromflags:manual /update; net stop w32time; net start w32time; w32tm /resync'
        Ok 'Jam disinkronkan'
    } catch { Warn 'Jam tidak disetel (izin ditolak). Tidak fatal.' }
    $Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'GoldNewsAgent.cmd'
    $cmd = "@echo off`r`ntitle Gold News Agent - JANGAN DITUTUP (minimize saja)`r`ncd /d $Root`r`n:loop`r`n.venv\Scripts\gold-agent.exe run all`r`necho Program berhenti, menyala lagi dalam 60 detik...`r`ntimeout /t 60`r`ngoto loop`r`n"
    [IO.File]::WriteAllText($Startup, $cmd, [Text.Encoding]::ASCII)
    Ok "Nyala otomatis saat login: $Startup"

    Step 9 'Menyalakan program dan membuka dashboard'
    Start-Process $Startup -WindowStyle Minimized
    $up = $false
    foreach ($i in 1..45) {
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest 'http://127.0.0.1:8787/healthz' -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { }
    }
    if (-not $up) { Fail 'Dashboard belum menyala. Buka jendela "Gold News Agent" di taskbar dan lihat tulisan merahnya.'; return }
    Start-Process 'http://127.0.0.1:8787'
    Write-Host ''
    Write-Host '==================== SELESAI ====================' -ForegroundColor Green
    Write-Host ' Dashboard : http://127.0.0.1:8787 (hanya di laptop ini)'
    if ($me) { Write-Host " Telegram  : kirim /status ke @$($me.username)" }
    Write-Host ' Program jalan di jendela "Gold News Agent" (taskbar): minimize, JANGAN ditutup.'
    Write-Host ' Mode SIGNAL_ONLY: program tidak pernah membuka order sendiri.'
    Write-Host '=================================================' -ForegroundColor Green
}

Install-GoldNewsAgent
