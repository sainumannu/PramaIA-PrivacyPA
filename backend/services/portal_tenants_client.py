"""
Client Portal per l'elenco dei tenant accessibili all'utente.

GET /api/tenants/me/tenants sul Portal restituisce i tenant dell'utente che
presenta il token: si inoltra quindi il JWT dell'utente (non il service token).
"""
import os
from typing import Any

import httpx

from backend.utils.logging import get_logger

logger = get_logger(__name__)


def _portal_api_url() -> str:
    return (
        os.getenv("PORTAL_API_URL")
        or os.getenv("PORTAL_URL")
        or "http://localhost:8091"
    ).rstrip("/")


def _normalize_tenant(item: Any) -> dict[str, str] | None:
    """Il Portal può rispondere con oggetti (id/name o tenant_id/tenant_name) o con semplici id."""
    if isinstance(item, str):
        tenant_id, name = item, item
    elif isinstance(item, dict):
        tenant_id = str(item.get("tenant_id") or item.get("id") or "").strip()
        name = str(item.get("tenant_name") or item.get("name") or tenant_id).strip()
    else:
        return None
    if not tenant_id:
        return None
    return {"id": tenant_id, "name": name or tenant_id}


async def fetch_my_tenants(authorization: str) -> list[dict[str, str]]:
    """Tenant accessibili all'utente, come lista di {id, name}."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{_portal_api_url()}/api/tenants/me/tenants",
            headers={"Authorization": authorization},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json() or []

    tenants = [_normalize_tenant(item) for item in data]
    return [t for t in tenants if t]


async def switch_tenant(authorization: str, tenant_id: str) -> dict[str, str]:
    """
    Chiede al Portal un nuovo token per il tenant indicato.

    Il Portal verifica l'appartenenza dell'utente al tenant e riemette il JWT con
    `tenant_id`, `tenant_role` e `app_admin_for` relativi a quel tenant: i ruoli
    del token restano quindi corretti anche dopo il cambio. Del refresh token del
    Portal non si restituisce nulla: l'app non lo usa.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_portal_api_url()}/api/tenants/switch-tenant",
            headers={"Authorization": authorization},
            json={"tenant_id": tenant_id},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json() or {}

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Portal did not return access_token")
    return {
        "access_token": access_token,
        "tenant_id": str(data.get("tenant_id") or tenant_id),
        "tenant_name": str(data.get("tenant_name") or tenant_id),
    }

