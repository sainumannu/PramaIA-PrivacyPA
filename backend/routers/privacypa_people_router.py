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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
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
from backend.models.privacypa_people import AssignmentStatus, Authorization
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
    dependencies=[Depends(require_module_view("authorization"))],
)


class ProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)


class AuthorizationIn(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=100)
    processing_activity_id: str | None = None
    asset_id: str | None = None
    authorization_profile_id: str
    valid_from: date
    valid_to: date | None = None


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
async def list_profiles(tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [p.to_dict() for p in await AuthorizationService(db).search_profiles(tenant_id)]


@au_router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.create")), db: AsyncSession = Depends(get_db),
):
    try:
        profile = await AuthorizationService(db).create_profile(tenant_id, _uid(user), body.name, body.description)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(profile)
    return profile.to_dict()


@au_router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: str, body: ProfileIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    assert_version(_found(await svc.get_profile(tenant_id, profile_id), "Profilo"), version)
    try:
        profile = await svc.update_profile(tenant_id, profile_id, _uid(user), body.name, body.description)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(profile)
    return profile.to_dict()


@au_router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.delete")), db: AsyncSession = Depends(get_db),
):
    try:
        await AuthorizationService(db).delete_profile(tenant_id, profile_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@au_router.get("")
async def list_authorizations(
    user_id: str | None = None, processing_id: str | None = None, asset_id: str | None = None, active_only: bool = False,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    rows = await AuthorizationService(db).search(
        tenant_id, user_id=user_id, processing_id=processing_id, asset_id=asset_id, active_only=active_only)
    return [a.to_dict() for a in rows if not a.processing_activity_id or scope.processing_visible(a.processing_activity_id)]


@au_router.post("", status_code=status.HTTP_201_CREATED)
async def create_authorization(
    body: AuthorizationIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("authorization.create")), db: AsyncSession = Depends(get_db),
):
    if body.processing_activity_id or body.asset_id:
        await assert_permission_for_unit(
            user, db, await _unit_of(db, tenant_id, body.processing_activity_id, body.asset_id), "authorization.create")
    try:
        auth = await AuthorizationService(db).create(tenant_id, _uid(user), **body.model_dump())
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
    return auth.to_dict()


@au_router.patch("/{auth_id}")
async def update_authorization(
    auth_id: str, body: AuthorizationIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    await _auth_guard(db, tenant_id, user, await svc.get(tenant_id, auth_id), "authorization.edit", version)
    if body.processing_activity_id or body.asset_id:
        await assert_permission_for_unit(
            user, db, await _unit_of(db, tenant_id, body.processing_activity_id, body.asset_id), "authorization.edit")
    try:
        auth = await svc.update(tenant_id, auth_id, _uid(user), **body.model_dump())
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(auth)
    return auth.to_dict()


@au_router.post("/{auth_id}/revoke")
async def revoke_authorization(
    auth_id: str, body: ReasonIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("authorization.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthorizationService(db)
    await _auth_guard(db, tenant_id, user, await svc.get(tenant_id, auth_id), "authorization.edit", version)
    try:
        auth = await svc.revoke(tenant_id, auth_id, _uid(user), body.reason)
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
