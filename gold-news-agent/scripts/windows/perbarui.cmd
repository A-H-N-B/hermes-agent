@echo off
rem Desktop shortcut "Perbarui Gold News Agent": update to the newest version safely
rem (your settings move to config\local.yaml first; .env and data\ are never touched).
title Memperbarui Gold News Agent
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0perbarui.ps1"
pause
