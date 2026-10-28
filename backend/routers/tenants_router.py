"""
Tenant dell'utente per il selettore dell'header: elenco e cambio del tenant attivo.

Proxy same-origin verso il Portal (evita CORS). I tenant sono condivisi tra
tutte le app della piattaforma, quindi l'endpoint non è sotto /api/<app>.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.services.portal_tenants_client import fetch_my_tenants, switch_tenant
from backend.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/tenants", tags=["Tenant"])


@router.get("/me")
async def get_my_tenants(
    request: Request,
    user: TokenPayload = Depends(get_current_user),
):
    """Tenant accessibili all'utente corrente, letti dal Portal."""
    try:
        return await fetch_my_tenants(request.headers.get("authorization", ""))
    except httpx.HTTPStatusError as e:
        logger.error(f"Portal tenants error {e.response.status_code}: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Errore Portal API: {e.response.text}",
        )
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Impossibile contattare Portal: {e}",
        )


class SwitchTenantRequest(BaseModel):
    tenant_id: str


@router.post("/switch")
async def switch_my_tenant(
    data: SwitchTenantRequest,
    request: Request,
    user: TokenPayload = Depends(get_current_user),
):
    """
    Cambia il tenant attivo: restituisce un nuovo token emesso dal Portal.

    L'appartenenza al tenant è verificata dal Portal, non da questa app: il nuovo
    token porta i ruoli che l'utente ha in quel tenant.
    """
    try:
        return await switch_tenant(request.headers.get("authorization", ""), data.tenant_id)
    except httpx.HTTPStatusError as e:
        logger.warning(f"Portal switch-tenant {e.response.status_code}: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Errore Portal API: {e.response.text}",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Impossibile contattare Portal: {e}",
        )

