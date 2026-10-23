@echo off
rem Desktop shortcut "Jalankan Gold News Agent": start the agent unless it already runs, then
rem open the dashboard once it answers. Never starts a second copy (one Telegram bot, one port).
title Membuka Gold News Agent
tasklist /FI "IMAGENAME eq gold-agent.exe" 2>nul | find /I "gold-agent.exe" >nul && goto open
tasklist /V /FI "WINDOWTITLE eq Gold News Agent*" 2>nul | find /I "cmd.exe" >nul && goto open
start "Gold News Agent" /min "%~dp0gold_agent_loop.cmd"
:open
echo Menyalakan Gold News Agent, tunggu sebentar...
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($i in 1..60) { try { Invoke-WebRequest http://127.0.0.1:8787/healthz -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep 2 } }; exit 1"
if errorlevel 1 goto failed
start "" http://127.0.0.1:8787
exit /b 0
:failed
echo Dashboard belum menyala setelah 2 menit.
echo Buka jendela "Gold News Agent" di taskbar dan lihat tulisan merahnya.
pause
exit /b 1
