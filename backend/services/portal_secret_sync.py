"""Riallineamento di PRAMAIA_JWT_SECRET con il Portal, senza intervento manuale.

Se il Portal cambia il JWT secret (tipicamente: ripristino di un backup), l'app non
riconosce più i token e non si riesce a entrare nemmeno nelle sue impostazioni per
correggerlo. Qui l'app se lo riprende da sola da GET /api/platform/jwt-secret, con le
credenziali di servizio (APP_ID + APP_CLIENT_SECRET) che ha già nel .env: quelle non
cambiano quando cambia il JWT secret.

Nessun valore arriva dal chiamante: URL del Portal e credenziali sono solo quelli già
configurati, quindi chi innesca un riallineamento ottiene al massimo che l'app si allinei
al Portal legittimo. Per questo POST /api/setup/resync non richiede login né localhost
(OP01: il segreto non lo può scegliere chi chiama).

Punti di ingresso:
- POST /api/setup/resync (setup_router.py): pulsante "Riallinea JWT secret" del Portal,
  pulsante del Setup Wizard, oppure a mano dal server;
- trigger_background_resync(): da portal_jwt.py, quando arriva un token con firma non valida.
"""

import logging
import os
import threading
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Intervallo minimo tra due chiamate al Portal: più corto per una richiesta esplicita,
# più lungo per quella automatica (innescabile da chiunque mandi un token con firma errata).
EXPLICIT_MIN_INTERVAL_SECONDS = 10
BACKGROUND_MIN_INTERVAL_SECONDS = 60
PORTAL_TIMEOUT_SECONDS = 10.0

_lock = threading.Lock()
_last_attempt: Optional[float] = None


class ResyncError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _portal_api_url() -> str:
    return (os.getenv("PORTAL_API_URL") or os.getenv("PORTAL_URL") or "").rstrip("/")


def has_service_credentials() -> bool:
    return bool(_portal_api_url() and os.getenv("APP_ID") and os.getenv("APP_CLIENT_SECRET"))


def resync_jwt_secret(min_interval: float = EXPLICIT_MIN_INTERVAL_SECONDS) -> dict:
    """Riprende il JWT secret dal Portal e lo applica (.env + processo, senza riavvio).

    Sincrona (httpx.Client): dagli endpoint async va chiamata con asyncio.to_thread.
    Ritorna {"changed": bool}; solleva ResyncError con status HTTP e messaggio per l'utente.
    """
    global _last_attempt
    if not _lock.acquire(blocking=False):
        raise ResyncError(429, "in_progress", "Riallineamento già in corso, riprova tra qualche secondo")
    try:
        now = time.monotonic()
        if _last_attempt is not None and now - _last_attempt < min_interval:
            wait = int(min_interval - (now - _last_attempt)) + 1
            raise ResyncError(429, "too_soon", f"Riallineamento appena tentato, riprova tra {wait} secondi")
        _last_attempt = now
        return _fetch_and_apply()
    finally:
        _lock.release()


def _fetch_and_apply() -> dict:
    portal_url = _portal_api_url()
    app_id = os.getenv("APP_ID")
    client_secret = os.getenv("APP_CLIENT_SECRET")
    if not portal_url:
        raise ResyncError(409, "not_configured", "PORTAL_URL non configurato: completa il Setup Wizard")
    if not app_id or not client_secret:
        raise ResyncError(
            409, "no_credentials",
            "APP_ID o APP_CLIENT_SECRET mancanti nel .env: inserisci il secret API dell'app "
            "dal Setup Wizard (/setup, aperto dal server stesso)",
        )

    try:
        response = httpx.get(
            f"{portal_url}/api/platform/jwt-secret",
            headers={"X-App-ID": app_id, "X-App-Secret": client_secret},
            timeout=PORTAL_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ResyncError(503, "portal_unreachable", f"Portal non raggiungibile su {portal_url} ({type(e).__name__})")

    if response.status_code == 401:
        raise ResyncError(
            502, "portal_rejected",
            "Il Portal ha rifiutato il secret API dell'app: rigeneralo nel Portal e inseriscilo "
            "dal Setup Wizard (/setup, aperto dal server stesso)",
        )
    if response.status_code != 200:
        detail = None
        if response.headers.get("content-type", "").startswith("application/json"):
            detail = response.json().get("detail")
        raise ResyncError(502, "portal_error", f"Il Portal ha risposto {response.status_code}: {detail or 'errore'}")

    jwt_secret = response.json().get("jwt_secret")
    if not jwt_secret:
        raise ResyncError(502, "portal_error", "Il Portal non ha restituito un JWT secret")

    if jwt_secret == os.getenv("PRAMAIA_JWT_SECRET"):
        return {"changed": False}

    # Import ritardato: setup_router importa questo modulo.
    from backend.routers.setup_router import save_to_env

    if not save_to_env({"PRAMAIA_JWT_SECRET": jwt_secret}):
        raise ResyncError(500, "save_failed", "Impossibile scrivere il .env dell'app")
    logger.warning("PRAMAIA_JWT_SECRET riallineato al valore del Portal")
    return {"changed": True}


def trigger_background_resync() -> None:
    """Da portal_jwt.py: una firma non valida può voler dire che il Portal ha cambiato
    segreto. Non blocca la richiesta (che riceve comunque 401): il riallineamento gira in
    un thread, al massimo uno ogni BACKGROUND_MIN_INTERVAL_SECONDS. Il login successivo,
    con un token nuovo del Portal, passa."""
    if not has_service_credentials():
        return
    if _last_attempt is not None and time.monotonic() - _last_attempt < BACKGROUND_MIN_INTERVAL_SECONDS:
        return
    threading.Thread(target=_background_worker, name="jwt-secret-resync", daemon=True).start()


def _background_worker() -> None:
    try:
        result = resync_jwt_secret(min_interval=BACKGROUND_MIN_INTERVAL_SECONDS)
        if not result["changed"]:
            logger.info("Token con firma non valida, ma il JWT secret è già allineato al Portal")
    except ResyncError as e:
        if e.code not in ("in_progress", "too_soon"):
            logger.warning(f"Riallineamento automatico del JWT secret non riuscito: {e.message}")
    except Exception:
        logger.exception("Errore inatteso nel riallineamento automatico del JWT secret")
