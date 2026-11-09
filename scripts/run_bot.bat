@echo off
cd /d "%~dp0\.."

echo Checking Python environment...
if not exist ".venv" (
    echo Creating virtual environment (.venv)...
    python -m venv .venv
    echo Installing dependencies...
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo Starting MaguraBot Sniper Engine on port 8000...
echo Open http://localhost:8000 in your browser
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
