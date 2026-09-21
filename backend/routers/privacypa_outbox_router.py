"""
PrivacyPA — API di consumo dell'outbox (§130): lettura degli eventi non consegnati e conferma.

Pensata per un consumatore esterno (es. un prodotto di reminder/notifica). Consegna "almeno una volta":
il consumatore deve trattare l'id evento come chiave di idempotenza e confermare con `ack`.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_permission
from backend.db.database import get_db
from backend.services.privacypa_outbox_service import MAX_BATCH, OutboxService

router = APIRouter(prefix="/api/privacypa/outbox", tags=["PrivacyPA - Outbox"])


class AckIn(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=MAX_BATCH)


@router.get("")
async def list_events(
    processed: bool = False,
    event_type: str | None = Query(None, max_length=100, description="prefisso del tipo evento, es. `finding.`"),
    limit: int = Query(100, ge=1, le=MAX_BATCH),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("outbox.view")),
    db: AsyncSession = Depends(get_db),
):
    events = await OutboxService(db).list_events(tenant_id, processed=processed, limit=limit, event_type=event_type)
    return [e.to_dict() for e in events]


@router.post("/ack")
async def ack_events(
    body: AckIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("outbox.administer")),
    db: AsyncSession = Depends(get_db),
):
    return {"acked": await OutboxService(db).ack(tenant_id, body.ids)}


@router.post("/{event_id}/retry")
async def report_failure(
    event_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("outbox.administer")),
    db: AsyncSession = Depends(get_db),
):
    """Il consumatore segnala che la consegna è fallita: incrementa `retry_count`, l'evento resta in coda."""
    event = await OutboxService(db).mark_failed(tenant_id, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento non trovato o già confermato")
    return event.to_dict()


@router.delete("/processed")
async def purge_processed(
    older_than_days: int = Query(30, ge=1, le=3650),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("outbox.administer")),
    db: AsyncSession = Depends(get_db),
):
    """Elimina gli eventi già confermati da almeno `older_than_days` giorni."""
    removed = await OutboxService(db).purge_processed(tenant_id, datetime.utcnow() - timedelta(days=older_than_days))
    return {"removed": removed}
