@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -m venv .venv
    if errorlevel 1 ( echo Python 3.10+ is required. & pause & exit /b 1 )
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 ( echo Package installation failed. Check your internet or existing package cache. & pause & exit /b 1 )
.venv\Scripts\python.exe -m streamlit run app.py
pause
