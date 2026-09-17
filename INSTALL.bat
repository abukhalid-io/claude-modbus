@echo off
title Pasang Claude Modbus
cd /d "%~dp0"
python --version >nul 2>&1 || (
  echo Python belum terpasang. Unduh dari https://python.org
  echo Saat memasang, centang "Add Python to PATH".
  pause & exit /b 1
)
python install.py
pause
