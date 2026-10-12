@echo off
chcp 65001 >nul
cd /d %~dp0
set PYTHONUTF8=1
title Zalo Tool
if not exist .venv\Scripts\python.exe (
  echo Chua cai dat. Hay chay cai-dat.bat truoc.
  pause
  exit /b
)
rem Cap nhat bang MySQL (neu co thay doi); loi thuong do MySQL trong XAMPP chua bat
.venv\Scripts\python web\manage.py migrate --noinput >nul
if errorlevel 1 (
  echo.
  echo Khong ket noi duoc MySQL. Mo XAMPP Control Panel va bam Start o dong MySQL, roi chay lai.
  pause
  exit /b
)
rem Bat Zalo worker (cua so thu nho), roi bat trang web
start "Zalo worker" /min node src\worker.js
.venv\Scripts\python web\run.py
rem Tat trang web thi tat luon worker
taskkill /fi "WINDOWTITLE eq Zalo worker*" /t /f >nul 2>&1
pause
