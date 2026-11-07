import os
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import logging

from backend.auth.portal_token_verifier import PORTAL_ISSUER, PortalTokenError, verify_portal_token
from backend.services import portal_secret_sync
from backend.utils.request_context import bind_active_role

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)



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
    """Valida un JWT emesso da PramaIA Portal (HS256 o RS256) o, se `OIDC_ISSUER` è configurato, dall'IdP dell'ente."""
    from backend.auth.oidc import decode_oidc_token, is_oidc_token

    # I token RS256 del Portal hanno lo stesso algoritmo di quelli dell'IdP: si distinguono dall'emittente.
    # Leggerlo senza verificare la firma è sicuro: decide solo quale verifica applicare, e un token che
    # si dichiara del Portal deve comunque essere firmato con una chiave del Portal.
    if _unverified_issuer(token) != PORTAL_ISSUER and is_oidc_token(token):
        return decode_oidc_token(token)

    try:
        # `aud` si verifica sotto: i token senza destinatario (utenti, servizi verso il Portal) restano validi.
        payload = verify_portal_token(token, verify_aud=False)
    except PortalTokenError as e:
        if e.reason == "not_configured":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="App non configurata. Completa il Setup Wizard per configurare l'autenticazione.",
            )
        if e.reason == "signature":
            # Firma HS256 non valida: può voler dire che il Portal ha cambiato JWT secret.
            # L'app se lo riprende in background (portal_secret_sync.py) e il prossimo login passa.
            portal_secret_sync.trigger_background_resync()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.message)

    # Token di servizio emesso per chiamare un'app precisa (collegamento tra moduli del Portal): deve essere questa.
    audience = payload.get("aud")
    if audience is not None:
        allowed = audience if isinstance(audience, list) else [audience]
        if own_app_id() not in allowed:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token issued for another application")

    return TokenPayload(payload)


def _unverified_issuer(token: str) -> Optional[str]:
    try:
        return jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError:
        return None


def own_app_id() -> str:
    """`app_id` con cui l'app è registrata nel Portal (destinatario dei token con `aud`)."""
    return os.getenv("APP_ID") or "PramaIA-PrivacyManager"


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
    user = decode_portal_token(credentials.credentials)
    bind_active_role(user.sub or user.email, user.tenant_role in ("global_admin", "admin"))
    return user


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
