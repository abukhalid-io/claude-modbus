@echo off
title Claude Modbus
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Pasang Python 3.10+ dulu dari python.org & pause & exit /b 1)
python -c "import pymodbus" >nul 2>&1 || python -m pip install -r requirements.txt
python -m gui.app
if errorlevel 1 pause
