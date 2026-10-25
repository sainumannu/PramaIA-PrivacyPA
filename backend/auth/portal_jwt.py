import os
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import logging

from backend.services import portal_secret_sync

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

PORTAL_ISSUER = "pramaia-portal"


def get_jwt_secret() -> Optional[str]:
    """Legge il secret a ogni chiamata: dopo il Setup Wizard non serve riavviare il processo."""
    return os.getenv("PRAMAIA_JWT_SECRET") or None


# Permetti avvio anche senza JWT secret per consentire Setup Wizard
if not get_jwt_secret():
    logger.warning(
        "⚠️  PRAMAIA_JWT_SECRET non configurato. "
        "Setup Wizard disponibile su /api/setup. "
        "Endpoint protetti non funzioneranno finché non configurato."
    )


class TokenPayload:
    def __init__(self, data: dict):
        self.sub: str = data.get("sub", "")
        self.email: str = data.get("email", "")
        self.display_name: str = data.get("display_name", "")
        self.roles: list[str] = data.get("roles", [])
        self.apps: list[str] = data.get("apps", [])
        self.is_admin: bool = "admin" in self.roles
        self.tenant_id: Optional[str] = data.get("tenant_id")
        self.tenant_role: Optional[str] = data.get("tenant_role")
        self.app_admin_for: list[str] = data.get("app_admin_for", [])
        self.preferred_language: Optional[str] = data.get("preferred_language")
        self.raw = data

    def get(self, key: str, default=None):
        """Accesso dict-like per compatibilità con codice che si aspetta un claims dict grezzo."""
        return self.raw.get(key, default)


def decode_portal_token(token: str) -> TokenPayload:
    """Valida un JWT emesso da PramaIA Portal (HS256) o, se `OIDC_ISSUER` è configurato, dall'IdP dell'ente."""
    from backend.auth.oidc import decode_oidc_token, is_oidc_token

    if is_oidc_token(token):
        return decode_oidc_token(token)
    jwt_secret = get_jwt_secret()
    if not jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App non configurata. Completa il Setup Wizard per configurare l'autenticazione."
        )
    
    try:
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer")
    except jwt.InvalidSignatureError as e:
        # Può voler dire che il Portal ha cambiato JWT secret: l'app se lo riprende in
        # background (portal_secret_sync.py) e il prossimo login passa.
        portal_secret_sync.trigger_background_resync()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    if payload.get("iss") != PORTAL_ISSUER:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token not issued by PramaIA Portal")

    return TokenPayload(payload)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> TokenPayload:
    """Dipendenza FastAPI: utente autenticato obbligatorio."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please login via PramaIA Portal.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_portal_token(credentials.credentials)


def require_admin(user: TokenPayload = Security(get_current_user)) -> TokenPayload:
    """Dipendenza FastAPI: richiede ruolo admin."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[TokenPayload]:
    """Dipendenza FastAPI: utente opzionale (endpoint pubblici)."""
    if not credentials:
        return None
    try:
        return decode_portal_token(credentials.credentials)
    except HTTPException:
        return None
