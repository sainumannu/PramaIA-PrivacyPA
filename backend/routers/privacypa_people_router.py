"""
PrivacyPA — Formazione (§44) e autorizzati al trattamento (§45).

Corsi e assegnazioni: tutti vedono il catalogo dei corsi, ma solo chi gestisce la formazione vede le
assegnazioni e i completamenti altrui (gli altri vedono i propri). Le autorizzazioni seguono lo scope del
trattamento (o della struttura responsabile del sistema) e si revocano con motivazione, non si cancellano.
Ogni modifica richiede `If-Match` (§54).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import record_on_behalf_of, require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    assert_permission_for_unit,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_people import AssignmentStatus, Authorization, AuthorizationProfile
from backend.services import privacypa_directory as directory
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound
from backend.services.privacypa_people_service import AuthorizationService, TrainingService
from backend.services.privacypa_processing_service import ProcessingService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter()


def _http(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


def _found(entity, label: str):
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} non trovato")
    return entity


async def _can(db: AsyncSession, user: TokenPayload, code: str) -> bool:
    try:
        await assert_permission(user, db, code)
    except HTTPException:
        return False
    return True


# ================================================================ formazione

tr_router = APIRouter(
    prefix="/api/privacypa/training", tags=["PrivacyPA - Formazione"], dependencies=[Depends(require_module_view("training"))],
)


class CourseIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    version: str = Field("1.0", min_length=1, max_length=50)
    description: str | None = Field(None, max_length=4000)
    mandatory: bool = False
    target_roles: list[str] = Field(default_factory=list, max_length=20)
    target_unit_ids: list[str] = Field(default_factory=list, max_length=200)
    validity_months: int | None = Field(None, ge=1, le=120)


class ActiveIn(BaseModel):
    active: bool


class SessionIn(BaseModel):
    session_date: date
    trainer: str | None = Field(None, max_length=255)
    location: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=2000)


class AssignIn(BaseModel):
    user_ids: list[str] = Field(..., min_length=1, max_length=500)
    due_date: date


class AssignTargetsIn(BaseModel):
    due_date: date


class CompleteIn(BaseModel):
    completed_at: date
    score: int | None = Field(None, ge=0, le=100)
    certificate_document_id: str | None = None
    session_id: str | None = None


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


@tr_router.get("/courses")
async def list_courses(
    active: bool | None = None, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return [c.to_dict() for c in await TrainingService(db).search_courses(tenant_id, active)]


@tr_router.post("/courses", status_code=status.HTTP_201_CREATED)
async def create_course(
    body: CourseIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("training.create")), db: AsyncSession = Depends(get_db),
):
    try:
        course = await TrainingService(db).create_course(tenant_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(course)
    return course.to_dict()


@tr_router.get("/courses/{course_id}")
async def get_course(
    course_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    course = _found(await TrainingService(db).get_course(tenant_id, course_id), "Corso")
    response.headers["ETag"] = etag_of(course)
    return course.to_dict()


@tr_router.patch("/courses/{course_id}")
async def update_course(
    course_id: str, body: CourseIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("training.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = TrainingService(db)
    assert_version(_found(await svc.get_course(tenant_id, course_id), "Corso"), version)
    try:
        course = await svc.update_course(tenant_id, course_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(course)
    return course.to_dict()


@tr_router.post("/courses/{course_id}/active")
async def set_course_active(
    course_id: str, body: ActiveIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("training.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = TrainingService(db)
    assert_version(_found(await svc.get_course(tenant_id, course_id), "Corso"), version)
    try:
        course = await svc.set_active(tenant_id, course_id, _uid(user), body.active)
    except ComplianceNotFound as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(course)
    return course.to_dict()


@tr_router.get("/courses/{course_id}/sessions")
async def list_sessions(
    course_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TrainingService(db)
    _found(await svc.get_course(tenant_id, course_id), "Corso")
    return [s.to_dict() for s in await svc.list_sessions(tenant_id, course_id)]


@tr_router.post("/courses/{course_id}/sessions", status_code=status.HTTP_201_CREATED)
async def add_session(
    course_id: str, body: SessionIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("training.edit")), db: AsyncSession = Depends(get_db),
):
    try:
        session = await TrainingService(db).add_session(tenant_id, course_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return session.to_dict()


@tr_router.get("/courses/{course_id}/targets")
async def preview_targets(
    course_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("training.edit")), db: AsyncSession = Depends(get_db),
):
    """Utenti che l'assegnazione automatica raggiungerebbe (senza un completamento ancora valido)."""
    svc = TrainingService(db)
    course = _found(await svc.get_course(tenant_id, course_id), "Corso")
    valid = await svc._valid_users(tenant_id, course_id, date.today())
    return {"user_ids": [u for u in await svc.target_users(tenant_id, course) if u not in valid]}


@tr_router.post("/courses/{course_id}/assign", status_code=status.HTTP_201_CREATED)
async def assign_course(
    course_id: str, body: AssignIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("training.edit")), db: AsyncSession = Depends(get_db),
):
    try:
        result = await TrainingService(db).assign(tenant_id, _uid(user), course_id, body.user_ids, body.due_date)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return {"created": [a.to_dict() for a in result["created"]], "skipped": result["skipped"]}


@tr_router.post("/courses/{course_id}/assign-targets", status_code=status.HTTP_201_CREATED)
async def assign_targets(
    course_id: str, body: AssignTargetsIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("training.edit")), db: AsyncSession = Depends(get_db),
):
    try:
        result = await TrainingService(db).assign_targets(tenant_id, _uid(user), course_id, body.due_date)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return {"created": [a.to_dict() for a in result["created"]], "skipped": result["skipped"]}


@tr_router.get("/assignments")
async def list_assignments(
    course_id: str | None = None, user_id: str | None = None, status_filter: AssignmentStatus | None = Query(None, alias="status"),
    mine: bool = False, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    manager = await _can(db, user, "training.edit")
    if mine or not manager:
        user_id = _uid(user)
    rows = await TrainingService(db).search_assignments(tenant_id, course_id=course_id, user_id=user_id, status=status_filter)
    return [a.to_dict() for a in rows]


@tr_router.post("/assignments/{assignment_id}/complete", status_code=status.HTTP_201_CREATED)
async def complete_assignment(
    assignment_id: str, body: CompleteIn, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("training.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = TrainingService(db)
    assert_version(_found(await svc.get_assignment(tenant_id, assignment_id), "Assegnazione"), version)
    try:
        completion = await svc.complete(tenant_id, assignment_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return completion.to_dict()


@tr_router.post("/assignments/{assignment_id}/waive")
async def waive_assignment(
    assignment_id: str, body: ReasonIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("training.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = TrainingService(db)
    assert_version(_found(await svc.get_assignment(tenant_id, assignment_id), "Assegnazione"), version)
    try:
        assignment = await svc.waive(tenant_id, assignment_id, _uid(user), body.reason)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(assignment)
    return assignment.to_dict()


@tr_router.get("/completions")
async def list_completions(
    course_id: str | None = None, user_id: str | None = None, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if not await _can(db, user, "training.edit"):
        user_id = _uid(user)
    return [c.to_dict() for c in await TrainingService(db).completions(tenant_id, user_id=user_id, course_id=course_id)]


# ================================================================ autorizzati

au_router = APIRouter(
    prefix="/api/privacypa/authorizations", tags=["PrivacyPA - Autorizzati al trattamento"],
    dependencies=[Depends(require_module_view("authorization")), Depends(record_on_behalf_of)],
)


class ProfileIn(BaseModel):
    """Profilo di accesso. In modifica i campi non inviati restano invariati."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    code: str | None = Field(None, max_length=50)
    asset_id: str | None = None
    data_category_ids: list[str] | None = None
    allowed_operations: list[str] | None = None
    allowed_scope_types: list[str] | None = None
    processing_activity_ids: list[str] | None = None
    required_training_course_ids: list[str] | None = None
    max_duration_days: int | None = None
    active: bool | None = None


class ScopeIn(BaseModel):
    type: str = Field(..., max_length=30)
    value: str | None = Field(None, max_length=2000)


class AuthorizationIn(BaseModel):
    """Concessione. In modifica i campi non inviati restano invariati e `user_id` si può omettere (non si cambia)."""

    user_id: str | None = Field(None, min_length=1, max_length=100)
    processing_activity_id: str | None = None
    asset_id: str | None = None
    authorization_profile_id: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    # Estremi dell'autorizzazione del responsabile della banca dati e origine (richieste di accesso dall'HelpDesk).
    authorized_by_name: str | None = Field(None, max_length=255)
    authorization_reference: str | None = Field(None, max_length=255)
    authorization_date: date | None = None
    authorization_notes: str | None = Field(None, max_length=4000)
    operations: list[str] | None = None
    scope: ScopeIn | None = None
    profile_version: int | None = None
    source_module: str | None = Field(None, max_length=30)
    source_ref: str | None = Field(None, max_length=100)
    forced: bool = False


class RevokeIn(BaseModel):
    # `reason` è il nome storico; l'HelpDesk invia `revoked_reason`: valgono entrambi.
    reason: str | None = Field(None, max_length=2000)
    revoked_reason: str | None = Field(None, max_length=2000)
    revocation_reference: str | None = Field(None, max_length=255)


async def _unit_of(db: AsyncSession, tenant_id: str, processing_id: str | None, asset_id: str | None) -> str | None:
    """Struttura su cui si valuta il permesso: quella del trattamento, altrimenti quella responsabile del sistema."""
    if processing_id:
        activity = await ProcessingService(db).get_activity(tenant_id, processing_id)
        if activity is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Trattamento non trovato")
        return activity.organizational_unit_id
    if asset_id:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.tenant_id != tenant_id or asset.deleted_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sistema non trovato")
        return asset.owner_unit_id
    return None


async def _auth_guard(db, tenant_id, user, auth: Authorization | None, code: str | None = None, version: int | None = None):
    _found(auth, "Autorizzazione")
    scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code or "authorization.view"))
    if auth.processing_activity_id:
        scope.assert_processing(auth.processing_activity_id)
    if code:
        await assert_permission_for_unit(user, db, await _unit_of(db, tenant_id, auth.processing_activity_id, auth.asset_id), code)
    if version is not None:
        assert_version(auth, version)


@au_router.get("/profiles")
async def list_profiles(
    asset_id: str | None = None, active_only: bool = False,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return [p.to_dict() for p in await AuthorizationService(db).search_profiles(tenant_id, asset_id=asset_id, active_only=active_only)]


@au_router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.administer")), db: AsyncSession = Depends(get_db),
):
    try:
        profile = await AuthorizationService(db).create_profile(tenant_id, _uid(user), **body.model_dump())
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(profile)
    return profile.to_dict()


@au_router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: str, body: ProfileIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.administer")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    assert_version(_found(await svc.get_profile(tenant_id, profile_id), "Profilo"), version)
    try:
        profile = await svc.update_profile(tenant_id, profile_id, _uid(user), **body.model_dump(exclude_unset=True))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(profile)
    return profile.to_dict()


@au_router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.administer")), db: AsyncSession = Depends(get_db),
):
    try:
        await AuthorizationService(db).delete_profile(tenant_id, profile_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


async def _with_names(db: AsyncSession, tenant_id: str, rows: list[Authorization]) -> list[dict]:
    """Nome del sistema e codice del profilo, anche se disattivati o eliminati (il catalogo mostra solo gli attivi)."""
    asset_ids = {a.asset_id for a in rows if a.asset_id}
    profile_ids = {a.authorization_profile_id for a in rows if a.authorization_profile_id}
    assets = dict((await db.execute(select(Asset.id, Asset.name).where(
        Asset.tenant_id == tenant_id, Asset.id.in_(asset_ids)))).all()) if asset_ids else {}
    profiles = dict((await db.execute(select(AuthorizationProfile.id, AuthorizationProfile.code).where(
        AuthorizationProfile.tenant_id == tenant_id, AuthorizationProfile.id.in_(profile_ids)))).all()) if profile_ids else {}
    return [{**a.to_dict(), "asset_name": assets.get(a.asset_id), "profile_code": profiles.get(a.authorization_profile_id)}
            for a in rows]


@au_router.get("/people")
async def portal_people(tenant_id: str = Depends(require_tenant)):
    """Persone del Portal tra cui scegliere l'incaricato: utenti del tenant con le strutture dell'organigramma.

    Se il Portal non risponde `available` è falso: l'interfaccia lascia comunque scrivere a mano l'identificativo, come per
    le persone che non sono nell'organigramma.
    """
    try:
        return {"available": True, "people": await directory.people(tenant_id)}
    except directory.DirectoryUnavailable as e:
        return {"available": False, "people": [], "detail": str(e)}


@au_router.get("")
async def list_authorizations(
    user_id: str | None = None, processing_id: str | None = None, asset_id: str | None = None, active_only: bool = False,
    include_future: bool = False,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    rows = await AuthorizationService(db).search(tenant_id, user_id=user_id, processing_id=processing_id,
                                                 asset_id=asset_id, active_only=active_only, include_future=include_future)
    visible = [a for a in rows if not a.processing_activity_id or scope.processing_visible(a.processing_activity_id)]
    return await _with_names(db, tenant_id, visible)


@au_router.post("", status_code=status.HTTP_201_CREATED)
async def create_authorization(
    body: AuthorizationIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.create")), db: AsyncSession = Depends(get_db),
):
    if body.processing_activity_id or body.asset_id:
        await assert_permission_for_unit(
            user, db, await _unit_of(db, tenant_id, body.processing_activity_id, body.asset_id), "authorization.create")
    if body.valid_from is None or body.authorization_profile_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "valid_from e authorization_profile_id sono obbligatori")
    svc = AuthorizationService(db)
    fields = body.model_dump()
    existing = await svc.find_registered(tenant_id, fields)
    if existing is not None:              # stessa concessione già registrata dal sistema esterno: nessun duplicato
        response.status_code = status.HTTP_200_OK
        response.headers["ETag"] = etag_of(existing)
        return existing.to_dict()
    try:
        auth = await svc.create(tenant_id, _uid(user), **fields)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(auth)
    return auth.to_dict()


@au_router.get("/{auth_id}")
async def get_authorization(
    auth_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    auth = await AuthorizationService(db).get(tenant_id, auth_id)
    await _auth_guard(db, tenant_id, user, auth)
    response.headers["ETag"] = etag_of(auth)
    return (await _with_names(db, tenant_id, [auth]))[0]


@au_router.patch("/{auth_id}")
async def update_authorization(
    auth_id: str, body: AuthorizationIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    current = await svc.get(tenant_id, auth_id)
    await _auth_guard(db, tenant_id, user, current, "authorization.edit", version)
    fields = body.model_dump(exclude_unset=True)
    processing_id = fields.get("processing_activity_id", current.processing_activity_id)
    asset_id = fields.get("asset_id", current.asset_id)
    if processing_id or asset_id:
        await assert_permission_for_unit(user, db, await _unit_of(db, tenant_id, processing_id, asset_id), "authorization.edit")
    try:
        auth = await svc.update(tenant_id, auth_id, _uid(user), **fields)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(auth)
    return auth.to_dict()


@au_router.post("/{auth_id}/revoke")
async def revoke_authorization(
    auth_id: str, body: RevokeIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    await _auth_guard(db, tenant_id, user, await svc.get(tenant_id, auth_id), "authorization.edit", version)
    try:
        auth = await svc.revoke(tenant_id, auth_id, _uid(user), body.reason or body.revoked_reason or "", body.revocation_reference)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(auth)
    return auth.to_dict()


@au_router.post("/{auth_id}/confirm-review")
async def confirm_authorization_review(
    auth_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    await _auth_guard(db, tenant_id, user, await svc.get(tenant_id, auth_id), "authorization.edit", version)
    try:
        auth = await svc.confirm_review(tenant_id, auth_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(auth)
    return auth.to_dict()


router.include_router(tr_router)
router.include_router(au_router)
