@echo off
cd /d "%~dp0"

echo.
echo ======================================================================
echo  PramaIA-PrivacyManager
echo ======================================================================
echo.

if not exist ".env" (
    echo Nessun file .env: al primo avvio si apre il Setup Wizard nel browser,
    echo che lo crea da solo. In alternativa copia .env.example in .env e compilalo.
    echo.
)

if not exist "logs" mkdir logs
if not exist "data" mkdir data
if not exist "backups" mkdir backups

echo Avvio del servizio...
echo.
PramaIA-PrivacyManager.exe

echo.
echo Servizio terminato.
pause
