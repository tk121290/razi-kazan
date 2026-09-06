@echo off
chcp 65001 >nul
title Tabîb Ekmeleddin'in Kazanı - İnternet Yayını (Cloudflare Tunnel)
cd /d "%~dp0"

echo ======================================================================
echo   ✦ TABÎB EKMELEDDİN'İN KAZANI - İNTERNET YAYIN BAŞLATICI ✦
echo   (Oyuncuların aynı WiFi ağına bağlanma zorunluluğu yoktur)
echo ======================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_tunnel.py
) else (
    python run_tunnel.py
)

if %errorlevel% neq 0 (
    echo.
    echo Bir sorun oluştu. Detayları yukarıdaki mesajda görebilirsiniz.
    pause
)
