@echo off
rem Desktop shortcut "Matikan Gold News Agent": stop the restart loop and the agent.
title Mematikan Gold News Agent
taskkill /F /T /FI "WINDOWTITLE eq Gold News Agent*" >nul 2>&1
taskkill /F /IM gold-agent.exe >nul 2>&1
echo Gold News Agent sudah dimatikan.
timeout /t 3 >nul
