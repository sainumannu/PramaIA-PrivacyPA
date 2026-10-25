"""
Setup Router - Wizard di configurazione iniziale app

Gestisce la configurazione iniziale dell'app tramite wizard guidato:
- Verifica stato configurazione
- Setup manuale (user fornisce JWT secret)
- Setup automatico (fetch JWT secret da Portal)
- Generazione secret sicuri
- Riallineamento del JWT secret quando il Portal lo cambia (/resync)

Le scritture sono accessibili solo da localhost e, per il setup manuale, solo se l'app
non è configurata. /resync invece risponde a chiunque: non accetta valori dal chiamante
(vedi backend/services/portal_secret_sync.py).
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
import asyncio
import os
import secrets
import httpx
from pathlib import Path
import logging
from typing import Literal

from backend.services import portal_secret_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["Setup"])

# ==================== MODELS ====================

class SetupCheckResponse(BaseModel):
    """Risposta check configurazione."""
    is_configured: bool
    missing_configs: list[str] = []
    # PORTAL_URL + APP_ID + APP_CLIENT_SECRET presenti: /resync può funzionare senza altri dati
    has_service_credentials: bool = False


class SetupDataManual(BaseModel):
    """Setup manuale: user fornisce JWT secret."""
    jwt_secret: str = Field(..., min_length=32)
    portal_url: str = Field(..., pattern=r"^https?://.*")
    database_url: str = "sqlite:///./PramaIA-PrivacyManager.db"
    
    @field_validator('jwt_secret')
    @classmethod
    def validate_secret_strength(cls, v):
        if len(v) < 32:
            raise ValueError("JWT secret deve essere almeno 32 caratteri")
        weak_patterns = ['password', 'secret', '123456', 'admin', 'token', 'test']
        if any(pattern in v.lower() for pattern in weak_patterns):
            raise ValueError("JWT secret troppo debole - evita parole comuni")
        return v


class SetupDataFetch(BaseModel):
    """Setup fetch: app recupera JWT da Portal."""
    app_id: str = Field(default="PramaIA-PrivacyManager", min_length=3)
    app_secret: str = Field(..., min_length=32)
    portal_url: str = Field(..., pattern=r"^https?://.*")
    database_url: str = "sqlite:///./PramaIA-PrivacyManager.db"


class SetupResponse(BaseModel):
    """Risposta operazione setup."""
    success: bool
    message: str
    restart_required: bool = True


class ResyncResponse(BaseModel):
    """Risposta riallineamento JWT secret con il Portal."""
    success: bool
    changed: bool
    message: str


# ==================== HELPERS ====================

def _require_localhost(request: Request) -> None:
    """OP01 (RICHIESTE_PLATFORM.md): finché l'app non ha un segreto, questi
    endpoint non devono rispondere a chi non chiama dalla macchina stessa —
    altrimenti chiunque raggiunga la porta potrebbe imporre un segreto a sua
    scelta e firmarsi token con qualsiasi ruolo. Non c'è un reverse proxy
    davanti a queste app: request.client.host è l'indirizzo reale del chiamante."""
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Questo endpoint risponde solo a richieste dalla stessa macchina.",
        )


def is_app_configured() -> tuple[bool, list[str]]:
    """
    Verifica se l'app è già configurata.
    
    Returns:
        Tupla (is_configured, missing_vars)
    """
    # Rilegge .env: load_dotenv() all'avvio non aggiorna os.environ dopo il wizard.
    load_dotenv(override=True)
    required_vars = ["PRAMAIA_JWT_SECRET", "PORTAL_URL"]
    missing = [var for var in required_vars if not (os.getenv(var) or "").strip()]
    return (len(missing) == 0, missing)


def derive_portal_api_url(portal_url: str) -> str:
    """UI Portal :3080 → API backend :8091 (porta reale di questa piattaforma)."""
    url = portal_url.rstrip("/")
    if ":3080" in url:
        return url.replace(":3080", ":8091")
    return url


def apply_runtime_config(data: dict[str, str]) -> None:
    """Rende la config appena scritta visibile al processo in esecuzione."""
    for key, value in data.items():
        os.environ[key] = value


def save_to_env(data: dict[str, str]) -> bool:
    """
    Salva configurazione nel file .env.
    
    Args:
        data: Dizionario variabili da salvare
        
    Returns:
        True se successo, False altrimenti
    """
    env_path = Path(".env")
    
    try:
        # Leggi contenuto esistente
        existing_lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()
        
        # Rimuovi variabili da aggiornare
        updated_lines = [
            line for line in existing_lines
            if not any(line.startswith(f"{key}=") for key in data.keys())
        ]
        
        # Aggiungi nuove variabili
        for key, value in data.items():
            updated_lines.append(f"{key}={value}\n")
        
        # Scrivi file
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(updated_lines)

        apply_runtime_config(data)
        
        logger.info(f"✅ File .env aggiornato con {len(data)} variabili")
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore scrittura .env: {e}")
        return False


# ==================== ENDPOINTS ====================

@router.get("/check", response_model=SetupCheckResponse)
async def check_setup_status():
    """
    Verifica se l'app è configurata.
    Chiamato all'avvio per decidere se mostrare setup wizard.
    """
    is_configured, missing = is_app_configured()
    return SetupCheckResponse(
        is_configured=is_configured,
        missing_configs=missing,
        has_service_credentials=portal_secret_sync.has_service_credentials(),
    )


@router.post("/initialize", response_model=SetupResponse)
async def initialize_setup(data: SetupDataManual, request: Request):
    """
    Setup MANUALE: User fornisce JWT secret.

    Security: Endpoint accessibile solo da localhost e solo se app NON configurata.
    """
    _require_localhost(request)
    is_configured, _ = is_app_configured()
    if is_configured:
        # Un segreto scelto a mano su un'app già in uso no: chiunque abbia accesso alla
        # macchina (anche senza diritti sul .env) potrebbe firmarsi token admin.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "App già configurata. Per riallineare il JWT secret al Portal usa la "
                "configurazione automatica o 'Riallinea con il Portal'; per sceglierlo a mano "
                "modifica il .env sul server."
            )
        )
    
    # Salva in .env
    env_data = {
        "PRAMAIA_JWT_SECRET": data.jwt_secret,
        "PORTAL_URL": data.portal_url,
        "PORTAL_API_URL": derive_portal_api_url(data.portal_url),
        "DATABASE_URL": data.database_url,
        "APP_ID": "PramaIA-PrivacyManager"
    }
    
    if not save_to_env(env_data):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Errore salvataggio configurazione"
        )
    
    logger.info("✅ Setup manuale completato")
    
    return SetupResponse(
        success=True,
        message="Setup completato! Riavvia l'app per applicare configurazione.",
        restart_required=True
    )


@router.post("/fetch-from-portal", response_model=SetupResponse)
async def fetch_jwt_from_portal(data: SetupDataFetch, request: Request):
    """
    Setup FETCH: Recupera JWT secret da Portal automaticamente.

    Flow:
    1. Valida credenziali app (app_id + app_secret)
    2. Chiama Portal GET /api/platform/jwt-secret
    3. Salva JWT secret in .env
    4. Salva anche portal_url, app_secret

    Security: Endpoint accessibile solo da localhost. Su un'app già configurata serve a
    riprendere il segreto dal Portal quando è cambiato anche il secret API (altrimenti
    basta /resync): il segreto arriva solo dal Portal già configurato e con la stessa
    identità di app, non lo sceglie chi chiama. DATABASE_URL resta quello attuale.
    """
    _require_localhost(request)
    is_configured, _ = is_app_configured()
    effective_app_id = (os.getenv("APP_ID") if is_configured else None) or data.app_id

    # OP01: non ci si fida del portal_url del corpo della richiesta per la
    # chiamata vera e propria (un chiamante potrebbe puntare a un suo server
    # e farsi restituire un segreto di sua scelta) — si usa quello già
    # configurato (installer o un salvataggio precedente), se c'e' già.
    # Il valore nel corpo resta solo per il primo avvio in assoluto, quando
    # PORTAL_URL non e' ancora presente da nessuna parte.
    effective_portal_url = os.getenv("PORTAL_URL") or data.portal_url

    # Chiamata a Portal per recuperare JWT secret
    portal_endpoint = f"{effective_portal_url.rstrip('/')}/api/platform/jwt-secret"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(f"📡 Fetch JWT secret da Portal: {portal_endpoint}")
            
            response = await client.get(
                portal_endpoint,
                headers={
                    "X-App-ID": effective_app_id,
                    "X-App-Secret": data.app_secret
                }
            )
            
            # Gestione errori HTTP
            if response.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Credenziali app non valide. Verifica App ID e App Secret nel Portal."
                )
            
            if response.status_code == 404:
                # Il Portal risponde 404 sia se l'endpoint non esiste (versione
                # datata) sia se l'app non è ancora registrata: mostriamo il
                # dettaglio reale del Portal invece di un messaggio generico.
                portal_detail = None
                if response.headers.get("content-type", "").startswith("application/json"):
                    portal_detail = response.json().get("detail")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=portal_detail or (
                        "Endpoint /api/platform/jwt-secret non trovato. "
                        "Verifica che il Portal sia aggiornato."
                    )
                )
            
            if response.status_code != 200:
                error_detail = response.json().get("detail", "Errore sconosciuto") if response.headers.get("content-type", "").startswith("application/json") else "Errore sconosciuto"
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Errore Portal: {error_detail}"
                )
            
            # Parse risposta
            portal_data = response.json()
            jwt_secret = portal_data.get("jwt_secret")
            
            if not jwt_secret:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Portal non ha ritornato jwt_secret valido"
                )
            
            # Salva configurazione in .env (PORTAL_URL e' quello effettivamente
            # contattato sopra, non necessariamente quello del corpo della richiesta)
            if is_configured:
                # Riallineamento: solo i segreti. URL, APP_ID e soprattutto DATABASE_URL
                # restano quelli in uso (il default del wizard punterebbe a un DB vuoto).
                env_data = {
                    "PRAMAIA_JWT_SECRET": jwt_secret,
                    "APP_CLIENT_SECRET": data.app_secret
                }
            else:
                env_data = {
                    "PRAMAIA_JWT_SECRET": jwt_secret,
                    "PORTAL_URL": effective_portal_url,
                    "PORTAL_API_URL": derive_portal_api_url(effective_portal_url),
                    "DATABASE_URL": data.database_url,
                    "APP_ID": effective_app_id,
                    "APP_CLIENT_SECRET": data.app_secret
                }
            
            if not save_to_env(env_data):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Errore salvataggio configurazione"
                )
            
            logger.info(
                f"✅ JWT secret recuperato da Portal (version={portal_data.get('version', 'unknown')})"
            )
            
            return SetupResponse(
                success=True,
                message="Setup completato! JWT secret sincronizzato con Portal. "
                        "Riavvia l'app per applicare configurazione.",
                restart_required=True
            )
    
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timeout connessione a Portal. Verifica che Portal sia raggiungibile."
        )
    
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Impossibile connettersi a Portal su {effective_portal_url}. "
                   "Verifica URL e che Portal sia in esecuzione."
        )
    
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"❌ Errore fetch JWT da Portal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore inaspettato: {str(e)}"
        )


@router.post("/generate-secret")
async def generate_jwt_secret(request: Request):
    """
    Genera un JWT secret random sicuro (64 caratteri).
    Usato dal frontend per pre-popolare i campi secret.

    Security: nessun gate di is_app_configured() qui (serve anche a chi sta
    appena per prendere la decisione) — ma solo da localhost (OP01).
    """
    _require_localhost(request)
    secret = secrets.token_urlsafe(48)  # 64 char base64url
    return {"jwt_secret": secret}


@router.post("/resync", response_model=ResyncResponse)
async def resync_jwt_with_portal():
    """
    Riprende PRAMAIA_JWT_SECRET dal Portal con le credenziali di servizio già nel .env
    (APP_ID + APP_CLIENT_SECRET), da usare quando il Portal ha cambiato segreto e i login
    non funzionano più. Chiamato dal pulsante "Riallinea JWT secret" del Portal, dal
    Setup Wizard, o a mano dal server.

    Security: nessun login né localhost (con il segreto sbagliato il login non può
    funzionare, e il Portal chiama da un'altra macchina): non accetta dati dal chiamante,
    URL del Portal e credenziali sono solo quelli configurati. Limitato nel tempo.
    """
    try:
        result = await asyncio.to_thread(portal_secret_sync.resync_jwt_secret)
    except portal_secret_sync.ResyncError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    return ResyncResponse(
        success=True,
        changed=result["changed"],
        message="JWT secret aggiornato dal Portal" if result["changed"] else "JWT secret già allineato al Portal",
    )
