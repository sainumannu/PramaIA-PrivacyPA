# ============================================================================
# PramaIA-PrivacyManager - Build Release Package
# ============================================================================
# Stesso schema di C:\PramaIA-SysAdmin2\installer\build-release.ps1 (PyInstaller +
# NSSM + Inno Setup). Crea un pacchetto di distribuzione on-premise con:
#   - Frontend React compilato (npm run build) servito dal backend stesso
#   - Backend compilato in un unico eseguibile (PyInstaller)
#   - Script di gestione servizio Windows (NSSM)
#   - Installer .exe (Inno Setup)
#
# Prerequisiti a runtime: NESSUNO oltre a Windows stesso. A differenza di
# PramaIA-SysAdmin2 non serve PostgreSQL: il database è SQLite locale
# (services/backup_service.py supporta solo SQLite; vedi RUNBOOK_RIPRISTINO.md).
# Il servizio PramaIA-Portal deve essere raggiungibile per login e licenza
# (PORTAL_URL), configurabile dal Setup Wizard al primo avvio.
# ============================================================================

param(
    [string]$OutputDir = "..\release",
    [string]$Version = "1.0.0",
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$SkipInstaller,
    [string]$InnoSetupPath = ""
)

$ErrorActionPreference = "Stop"
$INSTALLER_DIR = $PSScriptRoot
$ROOT = Split-Path $INSTALLER_DIR -Parent

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  PramaIA-PrivacyManager - Build Release v$Version" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --------------------------------------------------------------------------
# 0. Prerequisiti
# --------------------------------------------------------------------------
Write-Host "[0/6] Verifica prerequisiti..." -ForegroundColor Yellow

$pythonVersion = & "$ROOT\.venv\Scripts\python.exe" --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERRORE: venv Python non trovato in $ROOT\.venv" -ForegroundColor Red
    exit 1
}
Write-Host "  Python (venv): $pythonVersion" -ForegroundColor Green

$npmVersion = npm --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERRORE: npm non trovato nel PATH (serve per compilare il frontend)" -ForegroundColor Red
    exit 1
}
Write-Host "  npm: $npmVersion" -ForegroundColor Green

$pyiCheck = & "$ROOT\.venv\Scripts\pip.exe" show pyinstaller 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Installo PyInstaller nel venv..." -ForegroundColor Yellow
    & "$ROOT\.venv\Scripts\pip.exe" install pyinstaller --quiet
}
Write-Host "  PyInstaller: OK" -ForegroundColor Green

Write-Host ""

# --------------------------------------------------------------------------
# 1. Pulizia
# --------------------------------------------------------------------------
$releaseDir = Join-Path $INSTALLER_DIR $OutputDir
Write-Host "[1/6] Pulizia directory di output..." -ForegroundColor Yellow

if (($SkipFrontend -or $SkipBackend) -and (Test-Path $releaseDir)) {
    # Con -SkipFrontend/-SkipBackend il passo corrispondente non rigenera l'eseguibile o il build React:
    # una pulizia completa qui li cancellerebbe senza ricrearli, producendo un installer senza payload.
    Write-Host "  -SkipFrontend/-SkipBackend attivo: NON cancello $releaseDir (conservo l'eseguibile/build già presenti)" -ForegroundColor Yellow
} elseif (Test-Path $releaseDir) {
    Remove-Item -Recurse -Force $releaseDir
}
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
New-Item -ItemType Directory -Force -Path "$releaseDir\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$releaseDir\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$releaseDir\backups" | Out-Null
Write-Host "  Directory $releaseDir creata" -ForegroundColor Green

Write-Host ""

# --------------------------------------------------------------------------
# 2. Build Frontend (React)
# --------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host "[2/6] Build frontend (npm run build)..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    try {
        # Nessun REACT_APP_BACKEND_URL: il build va servito dallo stesso backend/porta
        # (apiClient.ts usa '' come base, cioè chiamate relative allo stesso host).
        npm run build 2>&1 | Tee-Object -Variable buildLog | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERRORE: build frontend fallita" -ForegroundColor Red
            Write-Host $buildLog
            exit 1
        }
        Write-Host "  Frontend compilato in frontend\build" -ForegroundColor Green
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[2/6] Frontend skippato (flag -SkipFrontend)" -ForegroundColor Gray
}

Write-Host ""

# --------------------------------------------------------------------------
# 3. Build Backend con PyInstaller
# --------------------------------------------------------------------------
if (-not $SkipBackend) {
    Write-Host "[3/6] Build backend con PyInstaller..." -ForegroundColor Yellow

    Set-Location $ROOT

    & "$ROOT\.venv\Scripts\pyinstaller.exe" `
        --name "PramaIA-PrivacyManager" `
        --onedir `
        --console `
        --add-data "$ROOT\frontend\build;frontend\build" `
        --hidden-import=aiosqlite `
        --hidden-import=greenlet `
        --hidden-import=passlib.handlers.bcrypt `
        --hidden-import=bcrypt `
        --hidden-import=jwt `
        --hidden-import=cryptography `
        --hidden-import=openpyxl `
        --hidden-import=docx `
        --hidden-import=pypdf `
        --hidden-import=alembic `
        --hidden-import=alembic.context `
        --hidden-import=apscheduler.schedulers.asyncio `
        --hidden-import=apscheduler.triggers.cron `
        --hidden-import=uvicorn.logging `
        --hidden-import=uvicorn.loops.auto `
        --hidden-import=uvicorn.protocols.http.auto `
        --hidden-import=uvicorn.protocols.websockets.auto `
        --hidden-import=uvicorn.lifespan.on `
        --add-data "$ROOT\backend\migrations;backend\migrations" `
        --add-data "$ROOT\alembic.ini;." `
        --distpath "$releaseDir\dist" `
        --workpath "$ROOT\build" `
        --specpath "$ROOT" `
        "run_service.py" `
        2>&1 | Tee-Object -Variable buildLog

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERRORE: PyInstaller build fallita" -ForegroundColor Red
        exit 1
    }

    # PyInstaller --onedir crea release\dist\PramaIA-PrivacyManager\*: spostiamo il
    # contenuto direttamente in release\ per una struttura più pulita.
    $distSubDir = "$releaseDir\dist\PramaIA-PrivacyManager"
    if (Test-Path $distSubDir) {
        Get-ChildItem $distSubDir | Move-Item -Destination $releaseDir -Force
        Remove-Item -Recurse -Force "$releaseDir\dist"
    }

    $exePath = "$releaseDir\PramaIA-PrivacyManager.exe"
    if (Test-Path $exePath) {
        Write-Host "  Backend compilato: $exePath" -ForegroundColor Green
    } else {
        Write-Host "  ERRORE: Eseguibile non trovato" -ForegroundColor Red
        exit 1
    }

    Remove-Item -Recurse -Force "$ROOT\build" -ErrorAction SilentlyContinue
    Remove-Item "$ROOT\PramaIA-PrivacyManager.spec" -ErrorAction SilentlyContinue

    Set-Location $INSTALLER_DIR
} else {
    Write-Host "[3/6] Backend skippato (flag -SkipBackend)" -ForegroundColor Gray
    if (-not (Test-Path "$releaseDir\PramaIA-PrivacyManager.exe")) {
        Write-Host "  ERRORE: nessun eseguibile in $releaseDir (mai buildato prima?): rilancia senza -SkipBackend" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""

# --------------------------------------------------------------------------
# 4. Copia file di configurazione e script
# --------------------------------------------------------------------------
Write-Host "[4/6] Copia file di configurazione..." -ForegroundColor Yellow

# .env.template -> .env.example (mai un .env con segreti reali nell'installer)
Copy-Item "$ROOT\.env.template" "$releaseDir\.env.example" -Force

# version.txt: a differenza di .env (preservato negli aggiornamenti), questo file
# viene sempre sovrascritto a ogni installazione — backend/main.py lo legge per
# riportare la versione REALE in app.version, invece del letterale fisso nel codice.
Set-Content -Path "$releaseDir\version.txt" -Value $Version -NoNewline -Encoding utf8

foreach ($doc in @("README.md", "RUNBOOK_RIPRISTINO.md")) {
    if (Test-Path "$ROOT\$doc") {
        Copy-Item "$ROOT\$doc" "$releaseDir\$doc" -Force
    }
}
Copy-Item "$INSTALLER_DIR\INSTALLER_README.md" "$releaseDir\INSTALLER_README.md" -Force

Copy-Item "$INSTALLER_DIR\start-service.ps1" "$releaseDir\start-service.ps1" -Force
Copy-Item "$INSTALLER_DIR\start-service.cmd" "$releaseDir\start-service.cmd" -Force
Copy-Item "$INSTALLER_DIR\service.ps1" "$releaseDir\service.ps1" -Force

"" | Out-File "$releaseDir\logs\.gitkeep"
"" | Out-File "$releaseDir\data\.gitkeep"
"" | Out-File "$releaseDir\backups\.gitkeep"

Write-Host "  File di configurazione copiati" -ForegroundColor Green

Write-Host ""

# --------------------------------------------------------------------------
# 5. Copia NSSM
# --------------------------------------------------------------------------
Write-Host "[5/6] Preparazione servizio Windows (NSSM)..." -ForegroundColor Yellow

if (Test-Path "$INSTALLER_DIR\nssm.exe") {
    Write-Host "  NSSM presente in $INSTALLER_DIR (usato direttamente da installer.iss)" -ForegroundColor Green
} else {
    Write-Host "  AVVERTENZA: nssm.exe non trovato in $INSTALLER_DIR" -ForegroundColor Yellow
    Write-Host "  Scaricalo da https://nssm.cc/download ed estrai nssm.exe (64-bit) qui" -ForegroundColor Yellow
}

Write-Host ""

# --------------------------------------------------------------------------
# 6. Crea Installer con Inno Setup
# --------------------------------------------------------------------------
if (-not $SkipInstaller) {
    Write-Host "[6/6] Creazione installer con Inno Setup..." -ForegroundColor Yellow

    if (-not $InnoSetupPath) {
        $possiblePaths = @(
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )
        foreach ($path in $possiblePaths) {
            if (Test-Path $path) { $InnoSetupPath = $path; break }
        }
    }

    if (-not (Test-Path $InnoSetupPath)) {
        Write-Host "  ERRORE: Inno Setup non trovato" -ForegroundColor Red
        Write-Host "  Scaricalo da: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
        exit 1
    }

    $issContent = Get-Content "$INSTALLER_DIR\installer.iss" -Raw
    $issContent = $issContent -replace '#define MyAppVersion\s+"[^"]+"', "#define MyAppVersion `"$Version`""
    Set-Content -Path "$INSTALLER_DIR\installer.iss" -Value $issContent

    Write-Host "  Versione aggiornata a: $Version" -ForegroundColor Gray

    if (-not (Test-Path "$INSTALLER_DIR\installer_output")) {
        New-Item -ItemType Directory -Force -Path "$INSTALLER_DIR\installer_output" | Out-Null
    }

    Write-Host "  Esecuzione Inno Setup..." -ForegroundColor Gray
    & $InnoSetupPath "$INSTALLER_DIR\installer.iss"

    if ($LASTEXITCODE -eq 0) {
        $installerFile = "$INSTALLER_DIR\installer_output\PramaIA-PrivacyManager-Setup-$Version.exe"
        if (Test-Path $installerFile) {
            Write-Host "  Installer creato: $installerFile" -ForegroundColor Green
        } else {
            Write-Host "  ERRORE: Installer non trovato dopo build" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  ERRORE: Inno Setup fallito" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[6/6] Installer skippato (flag -SkipInstaller)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Build Release completato!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Release directory: $releaseDir" -ForegroundColor Cyan
Write-Host "  Installer: installer\installer_output\PramaIA-PrivacyManager-Setup-$Version.exe" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Prima di distribuire:" -ForegroundColor Yellow
Write-Host "  1. Nessun database esterno da preparare (SQLite locale, creato al primo avvio)" -ForegroundColor Gray
Write-Host "  2. Il Setup Wizard (al primo avvio, nel browser) configura PRAMAIA_JWT_SECRET/PORTAL_URL: nessun .env da compilare a mano" -ForegroundColor Gray
Write-Host "  3. Verifica che il Portal (PORTAL_URL) sia raggiungibile dalla macchina di destinazione" -ForegroundColor Gray
Write-Host ""
