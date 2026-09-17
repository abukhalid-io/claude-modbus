@echo off
title Claude Modbus
cd /d "%~dp0"

rem pakai .venv kalau ada (dibuat oleh install.py), kalau tidak pakai python sistem
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m gui.app
  exit /b 0
)

python --version >nul 2>&1 || (echo Jalankan INSTALL.bat dulu & pause & exit /b 1)
python -c "import pymodbus, webview" >nul 2>&1 || (
  echo Dependensi belum lengkap. Menjalankan pemasang...
  python install.py --no-mcp
)
python -m gui.app
if errorlevel 1 pause
