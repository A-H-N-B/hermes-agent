@echo off
rem Runs the whole agent and restarts it 60 s after it stops. The window title is how
rem jalankan.cmd / matikan.cmd find it, so keep it starting with "Gold News Agent".
title Gold News Agent - JANGAN DITUTUP (minimize saja)
cd /d "%~dp0..\.."
:loop
.venv\Scripts\gold-agent.exe run all
echo Program berhenti, menyala lagi dalam 60 detik...
timeout /t 60
goto loop
