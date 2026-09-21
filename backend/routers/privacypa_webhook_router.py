"""Webhook (§70): sottoscrizioni degli endpoint esterni, consegne, prova e invio manuale. Solo amministratori."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_confidentiality import require_admin_step_up
from backend.auth.privacypa_permissions import require_permission
from backend.db.database import get_db
from backend.models.privacypa_webhooks import DeliveryStatus
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound
from backend.services.privacypa_webhook_service import EVENTS, WebhookService, dispatch_due
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(prefix="/api/privacypa/webhooks", tags=["PrivacyPA - Webhook"])


class WebhookIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=500)
    events: list[str] = Field(..., min_length=1, max_length=len(EVENTS))
    active: bool = True


def _http(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


@router.get("/events")
async def list_events(user: TokenPayload = Depends(require_permission("webhook.view"))):
    """Eventi a cui ci si può iscrivere. Il payload è minimale: tipo e id dell'elemento, stato e date utili."""
    return [{"name": e.name, "description": e.description} for e in EVENTS.values()]


@router.get("")
async def list_subscriptions(
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(require_permission("webhook.view")), db: AsyncSession = Depends(get_db),
):
    svc = WebhookService(db)
    counts = await svc.counts(tenant_id)
    return [{**s.to_dict(), "deliveries": counts.get(s.id, {})} for s in await svc.search(tenant_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: WebhookIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Crea l'endpoint. Il segreto di firma compare **solo** in questa risposta: va conservato dal chiamante."""
    await require_admin_step_up(db, tenant_id, user)
    try:
        sub, secret = await WebhookService(db).create(tenant_id, _uid(user), body.name, body.url, body.events, body.active)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(sub)
    return {**sub.to_dict(), "secret": secret}


@router.get("/{sub_id}")
async def get_subscription(
    sub_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.view")), db: AsyncSession = Depends(get_db),
):
    sub = await WebhookService(db).get(tenant_id, sub_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint non trovato")
    response.headers["ETag"] = etag_of(sub)
    return sub.to_dict()


@router.put("/{sub_id}")
async def update_subscription(
    sub_id: str, body: WebhookIn, response: Response, tenant_id: str = Depends(require_tenant), version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    await require_admin_step_up(db, tenant_id, user)
    svc = WebhookService(db)
    current = await svc.get(tenant_id, sub_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint non trovato")
    assert_version(current, version)
    try:
        sub = await svc.update(tenant_id, sub_id, _uid(user), body.name, body.url, body.events, body.active)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(sub)
    return sub.to_dict()


@router.delete("/deliveries")
async def purge_deliveries(
    older_than_days: int = Query(30, ge=1, le=3650), tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Elimina lo storico delle consegne concluse (consegnate o fallite) più vecchie di `older_than_days` giorni."""
    return {"removed": await WebhookService(db).purge(tenant_id, datetime.utcnow() - timedelta(days=older_than_days))}


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    sub_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    await require_admin_step_up(db, tenant_id, user)
    try:
        await WebhookService(db).delete(tenant_id, sub_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@router.post("/{sub_id}/rotate-secret")
async def rotate_secret(
    sub_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Genera un nuovo segreto (subito operativo: il vecchio smette di valere). Compare solo in questa risposta."""
    await require_admin_step_up(db, tenant_id, user)
    try:
        sub, secret = await WebhookService(db).rotate_secret(tenant_id, sub_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return {**sub.to_dict(), "secret": secret}


@router.post("/{sub_id}/test")
async def send_test(
    sub_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Invia subito un evento `webhook.ping` firmato e restituisce l'esito (nessun ritentativo, l'endpoint non cambia stato)."""
    try:
        delivery = await WebhookService(db).ping(tenant_id, sub_id)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return delivery.to_dict(with_payload=True)


@router.get("/{sub_id}/deliveries")
async def list_deliveries(
    sub_id: str, delivery_status: DeliveryStatus | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200),
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(require_permission("webhook.view")), db: AsyncSession = Depends(get_db),
):
    try:
        return [d.to_dict() for d in await WebhookService(db).deliveries(tenant_id, sub_id, delivery_status, limit)]
    except ComplianceNotFound as e:
        raise _http(e)


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Rimette in coda una consegna fallita definitivamente (l'invio avviene al prossimo ciclo del worker)."""
    try:
        return (await WebhookService(db).retry_delivery(tenant_id, delivery_id, _uid(user))).to_dict()
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@router.post("/dispatch")
async def dispatch(
    limit: int = Query(50, ge=1, le=200), tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("webhook.administer")), db: AsyncSession = Depends(get_db),
):
    """Invia ora le consegne dovute dell'ente. Utile se il worker in background è disattivato o per un controllo manuale."""
    return await dispatch_due(db, tenant_id=tenant_id, limit=limit)
