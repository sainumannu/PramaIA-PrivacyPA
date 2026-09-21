"""
PrivacyPA — Registro dei trattamenti (§11-15).
"""
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.auth.privacypa_authz import (
    get_accessible_unit_ids,
    is_portal_global_or_tenant_admin,
    require_tenant,
)
from backend.auth.privacypa_permissions import (
    assert_permission,
    can_for_unit,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.db.database import get_db
from backend.models.privacypa_approvals import ApprovalDecision
from backend.models.privacypa_assets import AssetRole
from backend.models.privacypa_parties import CommunicationType
from backend.models.privacypa_processing import ConfidentialityLevel, ProcessingStatus
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_approval_service import PROCESSING, ApprovalError, ApprovalService, http_error
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_retention_service import RetentionService, describe_period
from backend.services.privacypa_dataflow_service import DataFlowService
from backend.services.privacypa_compliance_service import ComplianceError, LegalReferenceService
from backend.services.privacypa_notice_service import NoticeService
from backend.services.privacypa_processing_service import MissingRequiredFields, ProcessingService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/processing-activities",
    tags=["PrivacyPA - Registro"],
    dependencies=[Depends(require_module_view("processing"))],
)


def _status_permission(current: ProcessingStatus, target: ProcessingStatus) -> str:
    """Permesso della matrice (§10.1) richiesto da una transizione di stato."""
    if target == ProcessingStatus.IN_REVIEW:
        return "processing.submit" if current == ProcessingStatus.DRAFT else "processing.reopen"
    if target == ProcessingStatus.DRAFT:
        return "processing.submit"  # ritiro della richiesta; chi approva chiede modifiche con una nota
    if target in (ProcessingStatus.APPROVED, ProcessingStatus.ACTIVE):
        return "processing.approve"
    return "processing.archive"


class ProcessingUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    organizational_unit_id: str | None = None
    owner_user_id: str | None = None
    privacy_referent_user_id: str | None = None
    purpose_summary: str | None = None
    source_of_data: str | None = None
    collection_method: str | None = None
    mandatory_provision: bool | None = None
    automated_decision: bool = False
    profiling: bool = False
    start_date: date | None = None
    last_review_date: date | None = None
    next_review_date: date | None = None
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.INTERNAL

    @field_validator("confidentiality_level")
    @classmethod
    def _not_highly_restricted(cls, value):
        if value == ConfidentialityLevel.HIGHLY_RESTRICTED:
            raise ValueError("Il livello HIGHLY_RESTRICTED vale per documenti, violazioni, DPIA e progetti")
        return value
    purposes: list[str] | None = None
    legal_basis_codes: list[str] | None = None
    data_subject_ids: list[str] | None = None
    data_category_ids: list[str] | None = None


class StatusChange(BaseModel):
    status: ProcessingStatus
    notes: str | None = None


class AssetLinkIn(BaseModel):
    asset_id: str
    role: AssetRole = AssetRole.PROCESSING
    notes: str | None = None


class ProcessorLinkIn(BaseModel):
    party_id: str
    processor_agreement_id: str | None = None
    contract_id: str | None = None
    notes: str | None = None


class RecipientLinkIn(BaseModel):
    party_id: str
    communication_type: CommunicationType = CommunicationType.COMMUNICATION
    notes: str | None = None


async def _enrich(db: AsyncSession, activity) -> dict:
    payload = activity.to_dict(include_relations=True)
    payload["assets"] = [x.to_dict() for x in await AssetService(db).list_links(activity.id)]
    payload["processors"] = [x.to_dict() for x in await PartyService(db).list_processors(activity.id)]
    payload["recipients"] = [x.to_dict() for x in await PartyService(db).list_recipients(activity.id)]
    payload["legal_references"] = [
        r.to_dict() for r in await LegalReferenceService(db).search(activity.tenant_id)
        if r.id in {l.legal_reference_id for l in await LegalReferenceService(db).list_links(activity.id)}
    ]
    payload["notices"] = [
        {"id": n.id, "code": n.code, "title": n.title, "status": n.status.value}
        for n in await NoticeService(db).search(activity.tenant_id, processing_id=activity.id)
    ]
    payload["data_flows"] = [f.to_dict() for f in await DataFlowService(db).list_for_processing(activity.tenant_id, activity.id)]
    payload["retention_rules"] = [
        {**r.to_dict(), "period_text": describe_period(r.retention_unit, r.retention_value, r.start_trigger)}
        for r in await RetentionService(db).list_for_processing(activity.tenant_id, activity.id)
    ]
    return payload


async def _scope_units(
    db: AsyncSession, tenant_id: str, user: TokenPayload
) -> set[str] | None:
    """Scope di lettura: unione delle strutture coperte da qualunque ruolo dell'utente."""
    return await get_accessible_unit_ids(
        db,
        tenant_id,
        user.sub or user.email,
        is_privileged=is_portal_global_or_tenant_admin(user),
    )


async def _write_scope_units(
    db: AsyncSession, tenant_id: str, user: TokenPayload, code: str = "processing.edit"
) -> set[str] | None:
    """Scope di scrittura: solo le strutture coperte da un ruolo che ha il permesso `code`."""
    return await get_accessible_unit_ids(
        db,
        tenant_id,
        user.sub or user.email,
        is_privileged=is_portal_global_or_tenant_admin(user),
        roles=await roles_for(db, tenant_id, code),
    )


async def _get_writable_activity(
    db: AsyncSession,
    tenant_id: str,
    user: TokenPayload,
    activity_id: str,
    version: int | None = None,
    code: str = "processing.edit",
):
    """Carica il trattamento (404), verifica permesso e scope per `code` (403) e la versione (409)."""
    existing = await ProcessingService(db).get_activity(tenant_id, activity_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    await assert_permission(user, db, code)
    _assert_unit_in_scope(
        existing.organizational_unit_id, await _write_scope_units(db, tenant_id, user, code)
    )
    if version is not None:
        assert_version(existing, version)
    return existing


def _assert_unit_in_scope(unit_id: str | None, accessible: set[str] | None) -> None:
    if accessible is None:
        return
    if not unit_id or unit_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fuori dallo scope organizzativo dell'utente",
        )


@router.get("/summary")
async def processing_summary(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    accessible = await _scope_units(db, tenant_id, user)
    return await svc.summary(tenant_id, accessible_unit_ids=accessible)


@router.get("")
async def list_processing_activities(
    status_filter: ProcessingStatus | None = Query(None, alias="status"),
    organizational_unit_id: str | None = None,
    search: str | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    accessible = await _scope_units(db, tenant_id, user)
    items = await svc.list_activities(
        tenant_id,
        status=status_filter,
        organizational_unit_id=organizational_unit_id,
        search=search,
        accessible_unit_ids=accessible,
    )
    return [i.to_dict() for i in items]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_processing_activity(
    body: ProcessingUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("processing.create")),
    db: AsyncSession = Depends(get_db),
):
    _assert_unit_in_scope(
        body.organizational_unit_id, await _write_scope_units(db, tenant_id, user, "processing.create")
    )
    svc = ProcessingService(db)
    activity = await svc.create_activity(
        tenant_id, actor_user_id=user.sub or user.email, **body.model_dump()
    )
    return activity.to_dict(include_relations=True)


@router.get("/{activity_id}")
async def get_processing_activity(
    activity_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    activity = await svc.get_activity(tenant_id, activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    accessible = await _scope_units(db, tenant_id, user)
    _assert_unit_in_scope(activity.organizational_unit_id, accessible)
    response.headers["ETag"] = etag_of(activity)
    return await _enrich(db, activity)


@router.get("/{activity_id}/versions")
async def list_processing_versions(
    activity_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    activity = await svc.get_activity(tenant_id, activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    _assert_unit_in_scope(activity.organizational_unit_id, await _scope_units(db, tenant_id, user))
    versions = await svc.list_versions(tenant_id, activity_id)
    return [v.to_dict(include_snapshot=False) for v in versions]


@router.get("/{activity_id}/versions/{version_id}")
async def get_processing_version(
    activity_id: str,
    version_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    activity = await svc.get_activity(tenant_id, activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    _assert_unit_in_scope(activity.organizational_unit_id, await _scope_units(db, tenant_id, user))
    version = await svc.get_version(tenant_id, activity_id, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versione non trovata")
    return version.to_dict(include_snapshot=True)


@router.patch("/{activity_id}")
async def update_processing_activity(
    activity_id: str,
    body: ProcessingUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("processing.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    await _get_writable_activity(db, tenant_id, user, activity_id, version)
    _assert_unit_in_scope(
        body.organizational_unit_id, await _write_scope_units(db, tenant_id, user)
    )
    try:
        activity = await svc.update_activity(
            tenant_id,
            activity_id,
            actor_user_id=user.sub or user.email,
            **body.model_dump(),
        )
        return activity.to_dict(include_relations=True)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{activity_id}/status")
async def change_processing_status(
    activity_id: str,
    body: StatusChange,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    approvals = ApprovalService(db)
    actor = user.sub or user.email
    current = await svc.get_activity(tenant_id, activity_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trattamento non trovato")
    # Il permesso dipende dalla transizione (§10.1) e deve valere sulla struttura del record (§9.3).
    existing = await _get_writable_activity(
        db, tenant_id, user, activity_id, version, _status_permission(current.status, body.status)
    )
    origin, target = existing.status, body.status
    try:
        if origin == ProcessingStatus.IN_REVIEW and target == ProcessingStatus.APPROVED:
            info = await approvals.load_entity(tenant_id, PROCESSING, activity_id)
            approval = await approvals.ensure_pending(tenant_id, info)
            await approvals.decide(tenant_id, approval, actor, ApprovalDecision.APPROVED, body.notes)
        elif origin == ProcessingStatus.IN_REVIEW and target == ProcessingStatus.DRAFT:
            pending = await approvals.pending_for(tenant_id, PROCESSING, activity_id)
            decides = (
                pending is not None
                and pending.requested_by != actor
                and await can_for_unit(user, db, existing.organizational_unit_id, "processing.approve")
            )
            if decides:
                await approvals.decide(
                    tenant_id, pending, actor, ApprovalDecision.CHANGES_REQUESTED, body.notes
                )
            else:
                await svc.transition_status(tenant_id, activity_id, actor_user_id=actor, target=target)
                await approvals.cancel_pending(tenant_id, PROCESSING, activity_id, actor)
        else:
            await svc.transition_status(tenant_id, activity_id, actor_user_id=actor, target=target)
            if target == ProcessingStatus.IN_REVIEW:
                info = await approvals.load_entity(tenant_id, PROCESSING, activity_id)
                await approvals.request_review(tenant_id, info, actor, body.notes)
        return (await svc.get_activity(tenant_id, activity_id)).to_dict(include_relations=True)
    except ApprovalError as e:
        raise http_error(e)
    except MissingRequiredFields as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_processing_activity(
    activity_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("processing.delete")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProcessingService(db)
    await _get_writable_activity(db, tenant_id, user, activity_id, code="processing.delete")
    try:
        await svc.soft_delete(tenant_id, activity_id, actor_user_id=user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def _require_assets(db: AsyncSession, tenant_id: str, asset_ids: list[str]) -> None:
    svc = AssetService(db)
    for asset_id in {a for a in asset_ids if a}:
        if await svc.get(tenant_id, asset_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Sistema non trovato: {asset_id}")


async def _require_parties(db: AsyncSession, tenant_id: str, party_ids: list[str]) -> None:
    svc = PartyService(db)
    for party_id in {p for p in party_ids if p}:
        if await svc.get(tenant_id, party_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Soggetto non trovato: {party_id}")


async def _apply_relations(
    db: AsyncSession,
    tenant_id: str,
    user: TokenPayload,
    activity_id: str,
    version: int,
    *,
    assets: list[AssetLinkIn] | None = None,
    processors: list[ProcessorLinkIn] | None = None,
    recipients: list[RecipientLinkIn] | None = None,
    legal_references: list[str] | None = None,
):
    """
    Sostituisce i collegamenti indicati (None = invariati) in un'unica transazione. Il trattamento cambia
    versione e viene tracciato in audit: un'approvazione già richiesta non vale più per il nuovo contenuto.
    """
    await _get_writable_activity(db, tenant_id, user, activity_id, version)
    party_svc, asset_svc = PartyService(db), AssetService(db)
    if assets is not None:
        await _require_assets(db, tenant_id, [i.asset_id for i in assets])
    if processors is not None:
        await _require_parties(db, tenant_id, [i.party_id for i in processors])
        for item in processors:
            if item.processor_agreement_id:
                agreements = await party_svc.list_agreements(tenant_id, item.party_id)
                if item.processor_agreement_id not in {a.id for a in agreements}:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Atto art. 28 non valido per il soggetto: {item.processor_agreement_id}",
                    )
            try:
                await party_svc.check_contract(tenant_id, item.party_id, item.contract_id)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if recipients is not None:
        await _require_parties(db, tenant_id, [i.party_id for i in recipients])
    legal_svc = LegalReferenceService(db)
    if legal_references is not None:
        try:
            await legal_svc.require_all(tenant_id, legal_references)
        except ComplianceError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    async def snapshot() -> dict:
        # Senza gli id: sostituire un collegamento con uno identico non è una modifica.
        def content(rows):
            items = [{k: v for k, v in r.to_dict().items() if k != "id"} for r in rows]
            return sorted(items, key=lambda i: json.dumps(i, sort_keys=True, default=str))

        return {
            "assets": content(await asset_svc.list_links(activity_id)),
            "processors": content(await party_svc.list_processors(activity_id)),
            "recipients": content(await party_svc.list_recipients(activity_id)),
            "legal_references": sorted(l.legal_reference_id for l in await legal_svc.list_links(activity_id)),
        }

    before = await snapshot()
    dump = lambda items: [i.model_dump(mode="json") for i in items]  # noqa: E731
    if assets is not None:
        await asset_svc.set_links(activity_id, dump(assets))
    if processors is not None:
        await party_svc.set_processors(activity_id, dump(processors))
    if recipients is not None:
        await party_svc.set_recipients(activity_id, dump(recipients))
    if legal_references is not None:
        await legal_svc.set_links(activity_id, legal_references)
    after = await snapshot()
    changed = [k for k in before if before[k] != after[k]]
    activity = await ProcessingService(db).get_activity(tenant_id, activity_id)
    actor = user.sub or user.email
    if changed:
        activity.updated_by, activity.updated_at = actor, datetime.utcnow()
        await db.flush()
        await AuditService(db).log(
            tenant_id, "processing_activity", "update_relations", event_type="processing_activity.relations.updated",
            entity_id=activity_id, actor_user_id=actor,
            old_values={k: before[k] for k in changed}, new_values={k: after[k] for k in changed},
        )
    return activity, after


class RelationsIn(BaseModel):
    assets: list[AssetLinkIn] | None = None
    processors: list[ProcessorLinkIn] | None = None
    recipients: list[RecipientLinkIn] | None = None
    legal_references: list[str] | None = None


@router.put("/{activity_id}/relations")
async def set_processing_relations(
    activity_id: str,
    body: RelationsIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("processing.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Sistemi, responsabili e destinatari in un colpo solo: una sola versione, un solo evento di audit."""
    activity, _ = await _apply_relations(
        db, tenant_id, user, activity_id, version,
        assets=body.assets, processors=body.processors, recipients=body.recipients, legal_references=body.legal_references,
    )
    response.headers["ETag"] = etag_of(activity)
    return await _enrich(db, activity)


@router.put("/{activity_id}/assets")
async def set_processing_assets(
    activity_id: str,
    body: list[AssetLinkIn],
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("processing.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _apply_relations(db, tenant_id, user, activity_id, version, assets=body)
    return [x.to_dict() for x in await AssetService(db).list_links(activity_id)]


@router.put("/{activity_id}/processors")
async def set_processing_processors(
    activity_id: str,
    body: list[ProcessorLinkIn],
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("processing.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _apply_relations(db, tenant_id, user, activity_id, version, processors=body)
    return [x.to_dict() for x in await PartyService(db).list_processors(activity_id)]


@router.put("/{activity_id}/recipients")
async def set_processing_recipients(
    activity_id: str,
    body: list[RecipientLinkIn],
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("processing.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _apply_relations(db, tenant_id, user, activity_id, version, recipients=body)
    return [x.to_dict() for x in await PartyService(db).list_recipients(activity_id)]
