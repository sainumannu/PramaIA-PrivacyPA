param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ROOT = $PSScriptRoot

function Start-Backend {
    Write-Host "[PrivacyManager] Starting backend on port 8003..." -ForegroundColor Cyan
    if (-not (Test-Path "$ROOT\.venv")) {
        Write-Host "Creating virtual environment..." -ForegroundColor Yellow
        python -m venv "$ROOT\.venv"
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", `
        "cd '$ROOT'; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt --quiet; python -m uvicorn backend.main:app --reload --port 8003" `
        -WindowStyle Normal
}

function Start-Frontend {
    Write-Host "[PrivacyManager] Starting frontend on port 3003..." -ForegroundColor Cyan
    $fe = Join-Path $ROOT "frontend"
    if (-not (Test-Path "$fe\node_modules")) {
        Write-Host "Installing npm packages..." -ForegroundColor Yellow
        npm install --prefix $fe
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$fe'; npm start" -WindowStyle Normal
}

if (-not $FrontendOnly) { Start-Backend }
Start-Sleep -Seconds 3
if (-not $BackendOnly) { Start-Frontend }

Write-Host ""
Write-Host "PrivacyManager is starting:" -ForegroundColor Green
Write-Host "  Backend  → http://localhost:8003" -ForegroundColor Green
Write-Host "  Frontend → http://localhost:3003" -ForegroundColor Green
Write-Host "  API Docs → http://localhost:8003/docs" -ForegroundColor Green
