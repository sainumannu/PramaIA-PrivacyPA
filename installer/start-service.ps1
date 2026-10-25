# ===========================================================================
# PramaIA-PrivacyManager - Avvio manuale (solo se NON installato come servizio
# Windows: con il servizio attivo su questa stessa porta, un secondo avvio
# fallisce con WinError 10048 — vedi INSTALLER_README.md)
# ===========================================================================

$ROOT = $PSScriptRoot

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  PramaIA-PrivacyManager - Avvio" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path "$ROOT\.env")) {
    Write-Host "Nessun file .env trovato: al primo avvio si apre il Setup Wizard nel browser," -ForegroundColor Yellow
    Write-Host "che lo crea da solo. In alternativa: copia .env.example in .env e compilalo a mano." -ForegroundColor Yellow
    Write-Host ""
}

if (-not (Test-Path "$ROOT\logs")) {
    New-Item -ItemType Directory -Force -Path "$ROOT\logs" | Out-Null
}
if (-not (Test-Path "$ROOT\data")) {
    New-Item -ItemType Directory -Force -Path "$ROOT\data" | Out-Null
}
if (-not (Test-Path "$ROOT\backups")) {
    New-Item -ItemType Directory -Force -Path "$ROOT\backups" | Out-Null
}

$exePath = Join-Path $ROOT "PramaIA-PrivacyManager.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "ERRORE: PramaIA-PrivacyManager.exe non trovato in $ROOT" -ForegroundColor Red
    exit 1
}

Write-Host "  Avvio PramaIA-PrivacyManager..." -ForegroundColor Green
Write-Host "  Premi Ctrl+C per fermare" -ForegroundColor Gray
Write-Host ""
Write-Host "  URL:   http://127.0.0.1:8003/" -ForegroundColor Green
Write-Host "  Docs:  http://127.0.0.1:8003/docs (solo se ENABLE_API_DOCS=true in .env)" -ForegroundColor Green
Write-Host ""

& $exePath

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Yellow
Write-Host "  Servizio terminato" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Yellow
