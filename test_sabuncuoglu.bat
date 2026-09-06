@echo off
chcp 65001 >nul
title Sabuncuoglu Serefeddin Sahne Testi
echo ============================================================
echo   SABUNCUOGLU SEREFEDDIN SAHNE VE ANIMASYON TESTI BASLATILIYOR
echo ============================================================
echo.
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe test_sabuncuoglu.py
) else (
    python test_sabuncuoglu.py
)
pause
