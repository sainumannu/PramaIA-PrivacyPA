"""
PrivacyPA — Riferimenti normativi (§13), azioni correttive (§29), data breach (§26), richieste degli
interessati (§27) e audit strutturati (§28).

Le entità con struttura di riferimento (violazioni, richieste, audit) seguono lo scope organizzativo; le azioni
correttive sono visibili anche a chi le ha create o ne è responsabile. Ogni modifica richiede `If-Match` (§54).
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import assert_permission, require_module_view, require_permission, roles_for
from backend.auth.privacypa_scope import VisibilityScope, build_scope
from backend.db.database import get_db
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.models.privacypa_compliance import (
    ActionPriority,
    ActionStatus,
    AuditFindingStatus,
    AuditStatus,
    AuditType,
    BREACH_LINK_TYPES,
    BreachRisk,
    BreachStatus,
    CorrectiveSource,
    LegalSourceType,
    RequestChannel,
    RequestStatus,
    RequestType,
)
from backend.services.privacypa_compliance_service import (
    DECISION_FIELDS,
    AuditPlanService,
    BreachService,
    ComplianceError,
    ComplianceNotFound,
    CorrectiveActionService,
    LegalReferenceService,
    RequestService,
)
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter()


def _http(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


async def _scope(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str | None = None) -> VisibilityScope:
    roles = await roles_for(db, tenant_id, code) if code else None
    return await build_scope(db, tenant_id, user, roles=roles)


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


async def _guard(
    db: AsyncSession, tenant_id: str, user: TokenPayload, entity, label: str, *, code: str | None = None,
    version: int | None = None, unit_id: str | None = None, personal: tuple = (),
):
    """404 se manca, 403 fuori dallo scope (con `code`: dei ruoli che hanno il permesso), 409 se la versione è stantia."""
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} non trovato")
    if code:
        await assert_permission(user, db, code)
    scope = await _scope(db, tenant_id, user, code)
    if not (scope.unit_visible(unit_id) or _uid(user) in personal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    level = getattr(entity, "confidentiality_level", None)
    if level is not None:
        scope.assert_level(level, getattr(entity, "created_by", None))
    if version is not None:
        assert_version(entity, version)
    return scope


# ================================================================ riferimenti normativi

lr_router = APIRouter(
    prefix="/api/privacypa/legal-references", tags=["PrivacyPA - Riferimenti normativi"],
    dependencies=[Depends(require_module_view("legalref"))],
)


class LegalReferenceIn(BaseModel):
    source_type: LegalSourceType
    title: str = Field(..., min_length=1, max_length=500)
    number: str | None = Field(None, max_length=50)
    year: int | None = None
    article: str | None = Field(None, max_length=100)
    url: str | None = Field(None, max_length=500)
    notes: str | None = None


@lr_router.get("")
async def list_legal_references(
    search: str | None = None, tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db),
):
    return [r.to_dict() for r in await LegalReferenceService(db).search(tenant_id, search)]


@lr_router.post("", status_code=status.HTTP_201_CREATED)
async def create_legal_reference(
    body: LegalReferenceIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("legalref.create")), db: AsyncSession = Depends(get_db),
):
    try:
        ref = await LegalReferenceService(db).create(tenant_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(ref)
    return ref.to_dict()


@lr_router.get("/{ref_id}")
async def get_legal_reference(
    ref_id: str, response: Response, tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db),
):
    ref = await LegalReferenceService(db).get(tenant_id, ref_id)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riferimento normativo non trovato")
    response.headers["ETag"] = etag_of(ref)
    return ref.to_dict()


@lr_router.patch("/{ref_id}")
async def update_legal_reference(
    ref_id: str, body: LegalReferenceIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("legalref.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = LegalReferenceService(db)
    current = await svc.get(tenant_id, ref_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riferimento normativo non trovato")
    assert_version(current, version)
    try:
        ref = await svc.update(tenant_id, ref_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(ref)
    return ref.to_dict()


@lr_router.delete("/{ref_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_legal_reference(
    ref_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("legalref.delete")), db: AsyncSession = Depends(get_db),
):
    try:
        await LegalReferenceService(db).soft_delete(tenant_id, ref_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


# ================================================================ azioni correttive

ca_router = APIRouter(
    prefix="/api/privacypa/corrective-actions", tags=["PrivacyPA - Azioni correttive"],
    dependencies=[Depends(require_module_view("action"))],
)


class ActionIn(BaseModel):
    source_type: CorrectiveSource
    source_id: str | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    priority: ActionPriority = ActionPriority.MEDIUM
    owner_user_id: str = Field(..., min_length=1, max_length=100)
    organizational_unit_id: str | None = None
    due_date: date
    evidence_document_id: str | None = None


class ActionStatusIn(BaseModel):
    status: ActionStatus
    notes: str | None = Field(None, max_length=4000)
    evidence_document_id: str | None = None


async def _action_guard(db, tenant_id, user, action, *, code=None, version=None):
    """Visibile a chi la possiede, a chi l'ha creata e a chi ha visibilità sulla struttura (o sull'intero ente)."""
    return await _guard(db, tenant_id, user, action, "Azione correttiva", code=code, version=version,
                        unit_id=getattr(action, "organizational_unit_id", None), personal=(action.owner_user_id, action.created_by) if action else ())


@ca_router.get("")
async def list_actions(
    status_filter: ActionStatus | None = Query(None, alias="status"), source_type: CorrectiveSource | None = None,
    source_id: str | None = None, mine: bool = False, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user)
    rows = await CorrectiveActionService(db).search(
        tenant_id, status=status_filter, source_type=source_type, source_id=source_id, owner=_uid(user) if mine else None
    )
    return [a.to_dict() for a in rows if scope.unit_visible(a.organizational_unit_id) or _uid(user) in (a.owner_user_id, a.created_by)]


@ca_router.post("", status_code=status.HTTP_201_CREATED)
async def create_action(
    body: ActionIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("action.create")), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user, "action.create")
    if body.organizational_unit_id:
        scope.assert_unit(body.organizational_unit_id)
    elif not scope.unrestricted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Indicare la struttura: senza, serve visibilità sull'intero ente")
    try:
        action = await CorrectiveActionService(db).create(tenant_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(action)
    return action.to_dict()


@ca_router.get("/{action_id}")
async def get_action(
    action_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    action = await CorrectiveActionService(db).get(tenant_id, action_id)
    await _action_guard(db, tenant_id, user, action)
    response.headers["ETag"] = etag_of(action)
    return action.to_dict()


@ca_router.patch("/{action_id}")
async def update_action(
    action_id: str, body: ActionIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("action.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = CorrectiveActionService(db)
    await _action_guard(db, tenant_id, user, await svc.get(tenant_id, action_id), code="action.edit", version=version)
    try:
        action = await svc.update(tenant_id, action_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(action)
    return action.to_dict()


@ca_router.post("/{action_id}/status")
async def change_action_status(
    action_id: str, body: ActionStatusIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("action.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = CorrectiveActionService(db)
    await _action_guard(db, tenant_id, user, await svc.get(tenant_id, action_id), code="action.edit", version=version)
    try:
        action = await svc.change_status(tenant_id, action_id, _uid(user), body.status, body.notes, body.evidence_document_id)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(action)
    return action.to_dict()


@ca_router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action(
    action_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("action.delete")), db: AsyncSession = Depends(get_db),
):
    svc = CorrectiveActionService(db)
    await _action_guard(db, tenant_id, user, await svc.get(tenant_id, action_id), code="action.delete")
    try:
        await svc.soft_delete(tenant_id, action_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


# ================================================================ data breach

br_router = APIRouter(
    prefix="/api/privacypa/breaches", tags=["PrivacyPA - Data breach"], dependencies=[Depends(require_module_view("breach"))],
)


class BreachIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    organizational_unit_id: str
    detected_at: datetime | None = None
    reported_by: str | None = Field(None, max_length=255)
    description: str = Field(..., min_length=1)
    confidentiality_affected: bool = False
    integrity_affected: bool = False
    availability_affected: bool = False
    approx_data_subjects: int | None = Field(None, ge=0)
    approx_records: int | None = Field(None, ge=0)
    # Assente = invariato (in creazione INTERNAL): una modifica che lo omette non abbassa la riservatezza.
    confidentiality_level: ConfidentialityLevel | None = None
    risk_level: BreachRisk | None = None
    risk_rationale: str | None = None
    notify_authority: bool | None = None
    notify_authority_rationale: str | None = None
    authority_notified_at: datetime | None = None
    notify_subjects: bool | None = None
    notify_subjects_rationale: str | None = None
    subjects_notified_at: datetime | None = None


class BreachCreateIn(BreachIn):
    aware_at: datetime


class AwareAtIn(BaseModel):
    aware_at: datetime
    reason: str = Field(..., min_length=1, max_length=4000)


class BreachStatusIn(BaseModel):
    status: BreachStatus


class BreachLinkIn(BaseModel):
    entity_type: str
    entity_id: str


class BreachLinksIn(BaseModel):
    links: list[BreachLinkIn] = Field(default_factory=list, max_length=500)


def _breach_body(body) -> dict:
    data = body.model_dump(exclude={"aware_at"})
    if data.get("confidentiality_level") is None:
        data.pop("confidentiality_level", None)
    return data


@br_router.get("")
async def list_breaches(
    status_filter: BreachStatus | None = Query(None, alias="status"), tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user)
    return [
        b.to_dict() for b in await BreachService(db).search(tenant_id, status_filter)
        if scope.unit_visible(b.organizational_unit_id) and scope.level_ok(b.confidentiality_level, b.created_by)
    ]


@br_router.post("", status_code=status.HTTP_201_CREATED)
async def create_breach(
    body: BreachCreateIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("breach.create")), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user, "breach.create")
    scope.assert_unit(body.organizational_unit_id)
    scope.assert_level_change(None, body.confidentiality_level)
    if any(getattr(body, f) is not None for f in DECISION_FIELDS):
        await assert_permission(user, db, "breach.approve")
    try:
        breach = await BreachService(db).create(tenant_id, _uid(user), body.aware_at, **_breach_body(body))
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.get("/{breach_id}")
async def get_breach(
    breach_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    breach = await BreachService(db).get(tenant_id, breach_id)
    await _guard(db, tenant_id, user, breach, "Violazione", unit_id=getattr(breach, "organizational_unit_id", None))
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.patch("/{breach_id}")
async def update_breach(
    breach_id: str, body: BreachIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("breach.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = BreachService(db)
    breach = await svc.get(tenant_id, breach_id)
    scope = await _guard(db, tenant_id, user, breach, "Violazione", code="breach.edit", version=version,
                         unit_id=getattr(breach, "organizational_unit_id", None))
    data = _breach_body(body)
    scope.assert_unit(data["organizational_unit_id"])
    if "confidentiality_level" in data:
        scope.assert_level_change(breach.confidentiality_level, data["confidentiality_level"], breach.created_by)
    # Valutazione del rischio e decisioni di notifica spettano ai ruoli di governo (§26).
    if any(data[f] != (getattr(breach, f).value if hasattr(getattr(breach, f), "value") else getattr(breach, f)) for f in DECISION_FIELDS):
        await assert_permission(user, db, "breach.approve")
    try:
        breach = await svc.update(tenant_id, breach_id, _uid(user), **data)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.post("/{breach_id}/aware-at")
async def correct_aware_at(
    breach_id: str, body: AwareAtIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("breach.approve")),
    db: AsyncSession = Depends(get_db),
):
    """Correzione motivata del momento di conoscenza (§157): l'originale resta visibile e la modifica è in audit."""
    svc = BreachService(db)
    breach = await svc.get(tenant_id, breach_id)
    await _guard(db, tenant_id, user, breach, "Violazione", code="breach.approve", version=version,
                 unit_id=getattr(breach, "organizational_unit_id", None))
    try:
        breach = await svc.correct_aware_at(tenant_id, breach_id, _uid(user), body.aware_at, body.reason)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.post("/{breach_id}/status")
async def change_breach_status(
    breach_id: str, body: BreachStatusIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    svc = BreachService(db)
    breach = await svc.get(tenant_id, breach_id)
    closing = body.status == BreachStatus.CLOSED or (breach is not None and breach.status == BreachStatus.CLOSED)
    await _guard(db, tenant_id, user, breach, "Violazione", code="breach.approve" if closing else "breach.edit",
                 version=version, unit_id=getattr(breach, "organizational_unit_id", None))
    try:
        breach = await svc.transition(tenant_id, breach_id, _uid(user), body.status)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.put("/{breach_id}/links")
async def set_breach_links(
    breach_id: str, body: BreachLinksIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("breach.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = BreachService(db)
    breach = await svc.get(tenant_id, breach_id)
    scope = await _guard(db, tenant_id, user, breach, "Violazione", code="breach.edit", version=version,
                         unit_id=getattr(breach, "organizational_unit_id", None))
    for link in body.links:
        if link.entity_type == "processing_activity":
            scope.assert_processing(link.entity_id)
    try:
        breach = await svc.set_links(tenant_id, breach_id, _uid(user), [l.model_dump() for l in body.links])
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(breach)
    return breach.to_dict()


@br_router.get("/meta/link-types")
async def breach_link_types():
    return list(BREACH_LINK_TYPES)


@br_router.delete("/{breach_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_breach(
    breach_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("breach.delete")), db: AsyncSession = Depends(get_db),
):
    svc = BreachService(db)
    breach = await svc.get(tenant_id, breach_id)
    await _guard(db, tenant_id, user, breach, "Violazione", code="breach.delete", unit_id=getattr(breach, "organizational_unit_id", None))
    try:
        await svc.soft_delete(tenant_id, breach_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


# ================================================================ richieste degli interessati

rq_router = APIRouter(
    prefix="/api/privacypa/requests", tags=["PrivacyPA - Richieste interessati"], dependencies=[Depends(require_module_view("dsr"))],
)


class RequestIn(BaseModel):
    organizational_unit_id: str
    request_type: RequestType
    received_at: datetime
    channel: RequestChannel
    identity_verified: bool = False
    identity_verification_notes: str | None = None
    requester_reference: str | None = Field(None, max_length=255)


class ExtensionIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)
    communicated_at: datetime | None = None


class RequestStatusIn(BaseModel):
    status: RequestStatus
    outcome: str | None = Field(None, max_length=8000)
    response_sent_at: datetime | None = None


class OfficeTasksIn(BaseModel):
    unit_ids: list[str] = Field(..., min_length=1, max_length=50)
    notes: str | None = Field(None, max_length=2000)


@rq_router.get("")
async def list_requests(
    status_filter: RequestStatus | None = Query(None, alias="status"), tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user)
    return [r.to_dict() for r in await RequestService(db).search(tenant_id, status_filter) if scope.unit_visible(r.organizational_unit_id)]


@rq_router.post("", status_code=status.HTTP_201_CREATED)
async def create_request(
    body: RequestIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dsr.create")), db: AsyncSession = Depends(get_db),
):
    (await _scope(db, tenant_id, user, "dsr.create")).assert_unit(body.organizational_unit_id)
    data = body.model_dump()
    try:
        req = await RequestService(db).create(tenant_id, _uid(user), data.pop("received_at"), **data)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(req)
    return req.to_dict()


@rq_router.get("/{request_id}")
async def get_request(
    request_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    req = await RequestService(db).get(tenant_id, request_id)
    await _guard(db, tenant_id, user, req, "Richiesta", unit_id=getattr(req, "organizational_unit_id", None))
    response.headers["ETag"] = etag_of(req)
    return req.to_dict()


@rq_router.patch("/{request_id}")
async def update_request(
    request_id: str, body: RequestIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("dsr.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = RequestService(db)
    req = await svc.get(tenant_id, request_id)
    scope = await _guard(db, tenant_id, user, req, "Richiesta", code="dsr.edit", version=version, unit_id=getattr(req, "organizational_unit_id", None))
    scope.assert_unit(body.organizational_unit_id)
    try:
        req = await svc.update(tenant_id, request_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(req)
    return req.to_dict()


@rq_router.post("/{request_id}/extension")
async def apply_extension(
    request_id: str, body: ExtensionIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("dsr.approve")),
    db: AsyncSession = Depends(get_db),
):
    svc = RequestService(db)
    req = await svc.get(tenant_id, request_id)
    await _guard(db, tenant_id, user, req, "Richiesta", code="dsr.approve", version=version, unit_id=getattr(req, "organizational_unit_id", None))
    try:
        req = await svc.apply_extension(tenant_id, request_id, _uid(user), body.reason, body.communicated_at)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(req)
    return req.to_dict()


@rq_router.post("/{request_id}/status")
async def change_request_status(
    request_id: str, body: RequestStatusIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    svc = RequestService(db)
    req = await svc.get(tenant_id, request_id)
    decides = body.status in (RequestStatus.RESPONDED, RequestStatus.CLOSED)
    await _guard(db, tenant_id, user, req, "Richiesta", code="dsr.approve" if decides else "dsr.edit", version=version,
                 unit_id=getattr(req, "organizational_unit_id", None))
    try:
        req = await svc.transition(tenant_id, request_id, _uid(user), body.status, outcome=body.outcome,
                                   response_sent_at=body.response_sent_at)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(req)
    return req.to_dict()


@rq_router.post("/{request_id}/tasks", status_code=status.HTTP_201_CREATED)
async def create_office_tasks(
    request_id: str, body: OfficeTasksIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dsr.edit")), db: AsyncSession = Depends(get_db),
):
    """Un task interno per ciascun ufficio coinvolto nella risposta (§27)."""
    svc = RequestService(db)
    req = await svc.get(tenant_id, request_id)
    await _guard(db, tenant_id, user, req, "Richiesta", code="dsr.edit", unit_id=getattr(req, "organizational_unit_id", None))
    try:
        tasks = await svc.create_office_tasks(tenant_id, request_id, _uid(user), body.unit_ids, body.notes)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return [t.to_dict() for t in tasks]


@rq_router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_request(
    request_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dsr.delete")), db: AsyncSession = Depends(get_db),
):
    svc = RequestService(db)
    req = await svc.get(tenant_id, request_id)
    await _guard(db, tenant_id, user, req, "Richiesta", code="dsr.delete", unit_id=getattr(req, "organizational_unit_id", None))
    try:
        await svc.soft_delete(tenant_id, request_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


# ================================================================ audit strutturati

au_router = APIRouter(
    prefix="/api/privacypa/audits", tags=["PrivacyPA - Audit"], dependencies=[Depends(require_module_view("auditplan"))],
)


class AuditIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    audit_type: AuditType
    organizational_unit_id: str | None = None
    planned_date: date
    lead_auditor_user_id: str = Field(..., min_length=1, max_length=100)


class AuditStatusIn(BaseModel):
    status: AuditStatus
    performed_date: date | None = None
    summary: str | None = Field(None, max_length=8000)


class AuditFindingIn(BaseModel):
    severity: str = Field(..., pattern="^(HIGH|MEDIUM|LOW)$")
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    evidence: str | None = None


class AuditFindingStatusIn(BaseModel):
    status: AuditFindingStatus


class FindingActionIn(BaseModel):
    owner_user_id: str = Field(..., min_length=1, max_length=100)
    due_date: date
    priority: ActionPriority = ActionPriority.MEDIUM


async def _audit_guard(db, tenant_id, user, audit, *, code=None, version=None):
    return await _guard(db, tenant_id, user, audit, "Audit", code=code, version=version,
                        unit_id=getattr(audit, "organizational_unit_id", None), personal=(audit.lead_auditor_user_id,) if audit else ())


@au_router.get("")
async def list_audits(
    status_filter: AuditStatus | None = Query(None, alias="status"), tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user)
    rows = await AuditPlanService(db).search(tenant_id, status_filter)
    return [a.to_dict() for a in rows if scope.unit_visible(a.organizational_unit_id) or _uid(user) == a.lead_auditor_user_id]


@au_router.post("", status_code=status.HTTP_201_CREATED)
async def create_audit(
    body: AuditIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("auditplan.create")), db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user, "auditplan.create")
    if body.organizational_unit_id:
        scope.assert_unit(body.organizational_unit_id)
    elif not scope.unrestricted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Indicare la struttura: senza, serve visibilità sull'intero ente")
    try:
        audit = await AuditPlanService(db).create(tenant_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(audit)
    return audit.to_dict(include_findings=True)


@au_router.get("/{audit_id}")
async def get_audit(
    audit_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    audit = await AuditPlanService(db).get(tenant_id, audit_id)
    await _audit_guard(db, tenant_id, user, audit)
    response.headers["ETag"] = etag_of(audit)
    return audit.to_dict(include_findings=True)


@au_router.patch("/{audit_id}")
async def update_audit(
    audit_id: str, body: AuditIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("auditplan.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.edit", version=version)
    try:
        audit = await svc.update(tenant_id, audit_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(audit)
    return audit.to_dict(include_findings=True)


@au_router.post("/{audit_id}/status")
async def change_audit_status(
    audit_id: str, body: AuditStatusIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("auditplan.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.edit", version=version)
    try:
        audit = await svc.transition(tenant_id, audit_id, _uid(user), body.status,
                                     performed_date=body.performed_date, summary=body.summary)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(audit)
    return audit.to_dict(include_findings=True)


@au_router.post("/{audit_id}/findings", status_code=status.HTTP_201_CREATED)
async def add_audit_finding(
    audit_id: str, body: AuditFindingIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("auditplan.edit")), db: AsyncSession = Depends(get_db),
):
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.edit")
    try:
        finding = await svc.add_finding(tenant_id, audit_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return finding.to_dict()


@au_router.patch("/{audit_id}/findings/{finding_id}")
async def set_audit_finding_status(
    audit_id: str, finding_id: str, body: AuditFindingStatusIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("auditplan.edit")), db: AsyncSession = Depends(get_db),
):
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.edit")
    try:
        return (await svc.set_finding_status(tenant_id, audit_id, finding_id, _uid(user), body.status)).to_dict()
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@au_router.post("/{audit_id}/findings/{finding_id}/corrective-action", status_code=status.HTTP_201_CREATED)
async def create_action_from_finding(
    audit_id: str, finding_id: str, body: FindingActionIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("action.create")), db: AsyncSession = Depends(get_db),
):
    """Apre un'azione correttiva dal rilievo e lo segna come "azione pianificata"."""
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.edit")
    try:
        action = await svc.create_action_from_finding(
            tenant_id, audit_id, finding_id, _uid(user), owner_user_id=body.owner_user_id, due_date=body.due_date,
            priority=body.priority,
        )
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return action.to_dict()


@au_router.delete("/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    audit_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("auditplan.delete")), db: AsyncSession = Depends(get_db),
):
    svc = AuditPlanService(db)
    await _audit_guard(db, tenant_id, user, await svc.get(tenant_id, audit_id), code="auditplan.delete")
    try:
        await svc.soft_delete(tenant_id, audit_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


for _sub in (lr_router, ca_router, br_router, rq_router, au_router):
    router.include_router(_sub)
