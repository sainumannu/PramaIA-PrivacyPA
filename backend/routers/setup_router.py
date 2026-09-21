"""
Setup Router - Wizard di configurazione iniziale app

Gestisce la configurazione iniziale dell'app tramite wizard guidato:
- Verifica stato configurazione
- Setup manuale (user fornisce JWT secret)
- Setup automatico (fetch JWT secret da Portal)
- Generazione secret sicuri

Endpoint accessibili SOLO se app non configurata.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
import os
import secrets
import httpx
from pathlib import Path
import logging
from typing import Literal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["Setup"])

# ==================== MODELS ====================

class SetupCheckResponse(BaseModel):
    """Risposta check configurazione."""
    is_configured: bool
    missing_configs: list[str] = []


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


# ==================== HELPERS ====================

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
        missing_configs=missing
    )


@router.post("/initialize", response_model=SetupResponse)
async def initialize_setup(data: SetupDataManual):
    """
    Setup MANUALE: User fornisce JWT secret.
    
    Security: Endpoint accessibile solo se app NON configurata.
    """
    is_configured, _ = is_app_configured()
    if is_configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="App già configurata. Per riconfigurare, elimina il file .env"
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
async def fetch_jwt_from_portal(data: SetupDataFetch):
    """
    Setup FETCH: Recupera JWT secret da Portal automaticamente.
    
    Flow:
    1. Valida credenziali app (app_id + app_secret)
    2. Chiama Portal GET /api/platform/jwt-secret
    3. Salva JWT secret in .env
    4. Salva anche portal_url, app_secret
    
    Security: Endpoint accessibile solo se app NON configurata.
    """
    is_configured, _ = is_app_configured()
    if is_configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="App già configurata. Per riconfigurare, elimina il file .env"
        )
    
    # Chiamata a Portal per recuperare JWT secret
    portal_endpoint = f"{data.portal_url.rstrip('/')}/api/platform/jwt-secret"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(f"📡 Fetch JWT secret da Portal: {portal_endpoint}")
            
            response = await client.get(
                portal_endpoint,
                headers={
                    "X-App-ID": data.app_id,
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
            
            # Salva configurazione in .env
            env_data = {
                "PRAMAIA_JWT_SECRET": jwt_secret,
                "PORTAL_URL": data.portal_url,
                "PORTAL_API_URL": derive_portal_api_url(data.portal_url),
                "DATABASE_URL": data.database_url,
                "APP_ID": data.app_id,
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
            detail=f"Impossibile connettersi a Portal su {data.portal_url}. "
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
async def generate_jwt_secret():
    """
    Genera un JWT secret random sicuro (64 caratteri).
    Usato dal frontend per pre-popolare i campi secret.
    """
    secret = secrets.token_urlsafe(48)  # 64 char base64url
    return {"jwt_secret": secret}
