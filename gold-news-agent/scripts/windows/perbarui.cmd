@echo off
rem Desktop shortcut "Perbarui Gold News Agent": update to the newest version safely
rem (your settings move to config\local.yaml first; .env and data\ are never touched).
rem It runs the NEW version's updater (fetched first), so a change to the update steps applies
rem on the same update; without internet it runs this copy, which then stops at the fetch.
rem "perbarui.cmd otomatis" is the daily scheduled run: no window to answer, output to
rem data\logs\perbarui.log, and nothing is touched when there is no new version.
title Memperbarui Gold News Agent
set "BRANCH=claude/prompt-execution-repo-setup-7e735z"
set "BASE=%~dp0..\..\.."
set "GIT=git"
if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
set "UPDATER=%TEMP%\gna_perbarui.ps1"
"%GIT%" -C "%BASE%" fetch -q origin %BRANCH% && "%GIT%" -C "%BASE%" show origin/%BRANCH%:gold-news-agent/scripts/windows/perbarui.ps1 > "%UPDATER%" || copy /y "%~dp0perbarui.ps1" "%UPDATER%" >nul
if /I "%~1"=="otomatis" goto auto
powershell -NoProfile -ExecutionPolicy Bypass -File "%UPDATER%"
pause
exit /b
:auto
set "LOGS=%BASE%\gold-news-agent\data\logs"
if not exist "%LOGS%" mkdir "%LOGS%"
echo ===== %DATE% %TIME% >> "%LOGS%\perbarui.log"
powershell -NoProfile -ExecutionPolicy Bypass -File "%UPDATER%" otomatis >> "%LOGS%\perbarui.log" 2>&1
