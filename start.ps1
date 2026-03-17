# SupoClip Local Startup Script for Windows
# This script opens 3 separate PowerShell windows for API, Worker, and Frontend.

Write-Host "============================================" -ForegroundColor Green
Write-Host "  SupoClip - Starting Local Development" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

# 1. Start Backend API
Write-Host "[1/3] Starting Backend API on http://localhost:8000..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; .\.venv\Scripts\Activate.ps1; uvicorn src.main_refactored:app --reload --host 0.0.0.0 --port 8000"

# 2. Start Worker
Write-Host "[2/3] Starting ARQ Worker..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; .\.venv\Scripts\Activate.ps1; arq src.workers.tasks.WorkerSettings"

# 3. Start Frontend
Write-Host "[3/3] Starting Frontend on http://localhost:3000..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; npm run dev"

Write-Host ""
Write-Host "All services are starting in separate windows." -ForegroundColor Yellow
Write-Host "Please ensure Redis and PostgreSQL are running locally." -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Green
