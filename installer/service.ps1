# ============================================================================
# PramaIA-PrivacyManager - Gestione servizio Windows (via NSSM)
# Uso: .\service.ps1 install|uninstall|start|stop|status
# ============================================================================
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("install", "uninstall", "start", "stop", "status")]
    [string]$Action
)

$ROOT = $PSScriptRoot
$ServiceName = "PramaIA-PrivacyManager"
$ExePath = Join-Path $ROOT "PramaIA-PrivacyManager.exe"
$NssmPath = Join-Path $ROOT "nssm.exe"

switch ($Action) {
    "install" {
        Write-Host "Installazione servizio $ServiceName..." -ForegroundColor Cyan
        & $NssmPath install $ServiceName $ExePath
        & $NssmPath set $ServiceName AppDirectory $ROOT
        & $NssmPath set $ServiceName AppStdout (Join-Path $ROOT "logs\service.log")
        & $NssmPath set $ServiceName AppStderr (Join-Path $ROOT "logs\service-error.log")
        Write-Host "Servizio installato. Esegui '.\service.ps1 start' per avviarlo." -ForegroundColor Green
    }
    "uninstall" {
        Write-Host "Disinstallazione servizio $ServiceName..." -ForegroundColor Cyan
        & $NssmPath stop $ServiceName
        & $NssmPath remove $ServiceName confirm
        Write-Host "Servizio rimosso." -ForegroundColor Green
    }
    "start" {
        & $NssmPath start $ServiceName
    }
    "stop" {
        & $NssmPath stop $ServiceName
    }
    "status" {
        & $NssmPath status $ServiceName
    }
}
