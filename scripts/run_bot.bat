@echo off
setlocal
cd /d "%~dp0\.."

echo Checking Python environment...
if exist ".venv" goto :start_bot

echo Creating virtual environment .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH! Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

echo Installing dependencies from requirements.txt ...
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

:start_bot
echo Starting MaguraBot Sniper Engine on port 8000...
echo Open http://127.0.0.1:8000 in your browser (or http://localhost:8000)
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
