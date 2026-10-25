"""
PramaIA-PrivacyManager - Entry point di produzione (compilato con PyInstaller).

In sviluppo il backend si avvia con `python -m uvicorn backend.main:app --reload`;
questo file è invece il punto d'ingresso dell'eseguibile distribuito, pensato per
girare come servizio Windows (via NSSM, vedi installer/). Configura il logging su
file PRIMA di importare backend.main (che tenta a sua volta di configurarlo: la
prima chiamata a logging.basicConfig vince, le successive sono no-op), così i log
finiscono in una cartella persistente reale invece che nella cartella temporanea
di estrazione di PyInstaller.

Stesso schema di C:\\PramaIA-SysAdmin2\\run_service.py.
"""
import os
import logging


def get_log_dir() -> str:
    """Cartella logs accanto all'eseguibile; fallback su AppData se non scrivibile
    (es. installazione in Program Files senza privilegi elevati per il processo)."""
    logs_dir = os.path.join(os.getcwd(), "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
        test_file = os.path.join(logs_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("")
        os.remove(test_file)
        return logs_dir
    except (PermissionError, OSError):
        appdata = os.getenv("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        logs_dir = os.path.join(appdata, "PramaIA", "PrivacyManager", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        return logs_dir


logs_dir = get_log_dir()
log_file = os.path.join(logs_dir, "privacymanager_service.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logging.getLogger("PramaIA-PrivacyManager-Service").info(f"Logging su file: {log_file}")


if __name__ == "__main__":
    from backend.main import app
    import uvicorn

    host = os.getenv("SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8003"))

    uvicorn.run(app, host=host, port=port, log_level="info")
