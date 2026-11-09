# MaguraBot PowerShell Startup Script for Windows
$ErrorActionPreference = "Stop"

Set-Location -Path "$PSScriptRoot\.."

Write-Host "Checking Python environment..." -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

Write-Host "Starting MaguraBot Sniper Engine on port 8000..." -ForegroundColor Green
Write-Host "Open http://127.0.0.1:8000 in your browser (or http://localhost:8000)" -ForegroundColor Green

& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
