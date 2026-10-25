"""
PrivacyPA — Connettori verso sistemi esterni (VideoPA): verifica dell'account di servizio, snapshot dei trattamenti (W1, W2) e
ricezione degli eventi tecnici con la loro gestione da parte del DPO (W3).

Vedi `INTEGRAZIONE_VIDEOPA.md`. L'account di servizio ha il ruolo `INTEGRATION` (assegnato da un amministratore dell'ente, come
per ogni utente) e vede solo queste rotte: `integration.view`. Uno snapshot è di sola lettura, con dati minimi.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import get_user_privacy_roles, is_portal_global_or_tenant_admin, require_tenant
from backend.auth.privacypa_permissions import require_permission
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_external_events import ExternalEventStatus
from backend.models.privacypa_processing import ProcessingActivity, ProcessingStatus
from backend.services.privacypa_external_event_service import MAX_BATCH, NOTE_MAX, ExternalEventError, ExternalEventService
from backend.services.privacypa_rules_engine import RulesEngine
from backend.utils.concurrency import assert_version, require_if_match
from backend.services.privacypa_integration_service import (
    CONTRACT_VERSION,
    MAX_PAGE,
    SnapshotService,
    get_integrations,
)

router = APIRouter(prefix="/api/privacypa/integrations", tags=["PrivacyPA - Integrazioni"])


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    wanted = [part.strip() for part in if_none_match.split(",")]
    return "*" in wanted or any(part.removeprefix("W/") == etag for part in wanted)


def _to_naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


async def _visible_activities(db: AsyncSession, tenant_id: str, scope, *, activity_id: str | None = None,
                              only_status: ProcessingStatus | None = None) -> list[ProcessingActivity]:
    stmt = select(ProcessingActivity).where(
        ProcessingActivity.tenant_id == tenant_id, ProcessingActivity.deleted_at.is_(None), ProcessingActivity.is_current.is_(True)
    )
    if activity_id:
        stmt = stmt.where(ProcessingActivity.id == activity_id)
    if only_status:
        stmt = stmt.where(ProcessingActivity.status == only_status)
    rows = (await db.execute(stmt.order_by(ProcessingActivity.code))).scalars().all()
    # Struttura nello scope e livello di riservatezza ammesso: un trattamento che l'account non vede vale come inesistente.
    return [a for a in rows if scope.unit_visible(a.organizational_unit_id) and scope.level_ok(a.confidentiality_level, a.created_by)]


@router.get("/ping")
async def ping(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("integration.view")),
    db: AsyncSession = Depends(get_db),
):
    """Serve a chi configura il connettore: quale ente, con quale ruolo, e se la conservazione è configurata."""
    roles = sorted({r.value for r in await get_user_privacy_roles(db, tenant_id, user.sub or user.email)})
    return {
        "ok": True, "contract_version": CONTRACT_VERSION, "tenant_id": tenant_id, "subject": user.sub or user.email,
        "roles": roles, "tenant_admin": is_portal_global_or_tenant_admin(user), "max_page": MAX_PAGE,
        "retention_category_configured": bool((await get_integrations(db, tenant_id))["retention_category"]),
    }


@router.get("/processing-activities")
async def list_snapshots(
    updated_since: datetime | None = Query(None, description="Solo i trattamenti cambiati da questo momento (incluso), ISO 8601"),
    status_filter: ProcessingStatus | None = Query(None, alias="status"),
    limit: int = Query(MAX_PAGE, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("integration.view")),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    activities = await _visible_activities(db, tenant_id, scope, only_status=status_filter)
    snapshots = await SnapshotService(db).build(tenant_id, activities, scope)
    if updated_since is not None:
        floor = _to_naive_utc(updated_since).replace(microsecond=0).isoformat()
        snapshots = [s for s in snapshots if s["updated_at"] >= floor]
    snapshots.sort(key=lambda s: (s["updated_at"], s["processing_id"]))
    return {
        "items": snapshots[offset:offset + limit], "total": len(snapshots), "limit": limit, "offset": offset,
        "as_of": datetime.utcnow().replace(microsecond=0).isoformat(),
    }


@router.get("/processing-activities/{processing_id}/snapshot")
async def get_snapshot(
    processing_id: str,
    request: Request,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("integration.view")),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    activities = await _visible_activities(db, tenant_id, scope, activity_id=processing_id)
    if not activities:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    snapshot = (await SnapshotService(db).build(tenant_id, activities, scope))[0]
    etag = snapshot["etag"]
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if _matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return snapshot


# ---------------------------------------------------------------- W3: eventi tecnici da sistemi esterni

@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_events(
    body: dict = Body(..., examples=[{"events": [{"source": "VIDEOPA", "event_id": "…", "type": "AudioEnabled",
                                                   "occurred_at": "2026-09-21T09:58:00", "processing_id": "…"}]}]),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("integration.create")),
    db: AsyncSession = Depends(get_db),
):
    """
    Fino a 100 eventi per chiamata (con `Idempotency-Key` un ritentativo dell'intera chiamata dà la stessa risposta). Esito per evento:
    ACCEPTED, DUPLICATE, UNMATCHED o REJECTED con motivo. Solo i campi del contratto vengono conservati. Non cambia il trattamento.
    """
    events = body.get("events")
    if not isinstance(events, list) or not events:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indicare `events`: un elenco non vuoto")
    if len(events) > MAX_BATCH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Al massimo {MAX_BATCH} eventi per chiamata")
    scope = await build_scope(db, tenant_id, user)
    months = (await get_integrations(db, tenant_id))["external_events_retention_months"]
    results = await ExternalEventService(db).ingest(tenant_id, user.sub or user.email, scope, events, retention_months=months)
    if any(r["outcome"] == "ACCEPTED" for r in results):
        await RulesEngine(db).evaluate(tenant_id)                    # la segnalazione per il DPO compare subito
    counts = {o: sum(1 for r in results if r["outcome"] == o) for o in ("ACCEPTED", "DUPLICATE", "UNMATCHED", "REJECTED")}
    return {"results": results, "counts": counts}


class ArchiveIn(BaseModel):
    note: str = Field(..., min_length=1, max_length=NOTE_MAX)


@router.get("/external-events")
async def list_external_events(
    status_filter: ExternalEventStatus | None = Query(None, alias="status"),
    processing_id: str | None = None,
    event_type: str | None = Query(None, alias="type"),
    limit: int = Query(MAX_PAGE, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("externalevent.view")),
    db: AsyncSession = Depends(get_db),
):
    """Per il DPO: gli eventi ricevuti, nell'ambito dell'utente. Quelli senza trattamento (UNMATCHED) li vede solo chi ha l'intero ente."""
    scope = await build_scope(db, tenant_id, user)
    items, total = await ExternalEventService(db).list(
        tenant_id, scope, status=status_filter, processing_id=processing_id, event_type=event_type, limit=limit, offset=offset)
    return {"items": [e.to_dict() for e in items], "total": total, "limit": limit, "offset": offset}


@router.post("/external-events/{event_id}/archive")
async def archive_external_event(
    event_id: str,
    body: ArchiveIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("externalevent.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Archivia un evento con una nota («nessun impatto»): chiude la segnalazione se non ne restano di pendenti dello stesso tipo."""
    service = ExternalEventService(db)
    event = await service.get(tenant_id, event_id)
    scope = await build_scope(db, tenant_id, user)
    if event is None or not (scope.processing_visible(event.processing_activity_id) if event.processing_activity_id else scope.unrestricted):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Evento non trovato")
    assert_version(event, version)
    try:
        event = await service.archive(tenant_id, event, user.sub or user.email, body.note)
    except ExternalEventError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    await RulesEngine(db).evaluate(tenant_id)
    response.headers["ETag"] = f'"{event.row_version}"'
    return event.to_dict()
