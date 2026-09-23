@echo off
REM ============================================================
REM  Start NapCat (the QQ protocol backend).
REM
REM    start-napcat.bat              QR code login
REM    start-napcat.bat 3104685327   quick login with that QQ
REM
REM  Keep this window open while the bot is running.
REM
REM  About the QR code:
REM    The character-art QR printed in this console usually can NOT
REM    be scanned (a known NapCat issue). The real QR is written to
REM    NapCat.Shell\cache\qrcode.png, and a background watcher opens
REM    that image for you automatically - scan THAT one.
REM ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0NapCat.Shell"

if not exist "launcher-user.bat" (
    echo [ERROR] NapCat.Shell\launcher-user.bat not found.
    echo         Make sure NapCat.Shell sits next to start-napcat.bat.
    pause
    exit /b 1
)

REM Drop last run's QR so the watcher never pops up a stale image
if exist "cache\qrcode.png" del /q "cache\qrcode.png" >nul 2>&1

REM Background watcher: pops up the QR image as soon as it appears
start "NapCatQR" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\qr-watch.ps1" -QrPath "%~dp0NapCat.Shell\cache\qrcode.png" -LogFile "%~dp0logs\qr-watch.log"

echo.
echo [NapCat] The QR code will pop up as an image - scan that one.
echo [NapCat] (The character-art QR in this console usually will not scan.)
echo [NapCat] If no image shows up, just double-click this file instead:
echo [NapCat]   NapCat.Shell\cache\qrcode.png
echo.

if "%~1"=="" (
    echo [NapCat] Login mode: QR code
    echo.
    call launcher-user.bat
) else (
    echo [NapCat] Login mode: quick login, QQ = %~1
    echo.
    call launcher-user.bat %~1
)
