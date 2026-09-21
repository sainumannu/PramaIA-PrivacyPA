"""
PrivacyPA — Privacy by design: nuovi progetti (§30).

I progetti seguono lo scope della struttura proponente (li vedono anche il responsabile e chi li ha creati). Ogni fase di
revisione richiede il permesso della fase sulla struttura del progetto; la revisione del DPO richiede un ruolo DPO
realmente assegnato. Ogni modifica richiede `If-Match` (§54).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import assert_effective_role, require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    assert_permission_for_unit,
    require_module_view,
    require_permission,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.models.privacypa_projects import Project, ProjectStatus, ReviewOutcome, ReviewStage
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound
from backend.services.privacypa_project_service import STAGE_OF, TARGETS, ProjectSegregation, ProjectService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/projects", tags=["PrivacyPA - Privacy by design"], dependencies=[Depends(require_module_view("project"))],
)

REVIEW_PERMISSION = {
    ReviewStage.PRIVACY: "project.review_privacy", ReviewStage.ICT: "project.review_ict", ReviewStage.DPO: "project.review_dpo",
    ReviewStage.APPROVAL: "project.approve", ReviewStage.POST: "project.review_privacy",
}
# Cosa serve poter creare per generare ciascun elemento: (permesso, sulla struttura del progetto?).
GENERATION_PERMISSIONS = {
    "processing": ("processing.create", True), "asset": ("asset.create", False), "contract": ("party.create", False),
    "agreement": ("party.create", False), "notice": ("notice.create", True), "tasks": ("task.create", False),
}


class ProjectIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    proposing_unit_id: str
    project_owner_id: str = Field(..., min_length=1, max_length=100)
    supplier_id: str | None = None
    # Assente = invariato (in creazione INTERNAL): una modifica che lo omette non abbassa la riservatezza.
    confidentiality_level: ConfidentialityLevel | None = None
    uses_cloud: bool = False
    uses_ai: bool = False
    profiles_people: bool = False
    automated_decisions: bool = False
    special_data: bool = False
    criminal_data: bool = False
    minors: bool = False
    systematic_monitoring: bool = False
    large_scale: bool = False


class ReviewIn(BaseModel):
    outcome: ReviewOutcome
    notes: str | None = Field(None, max_length=8000)
    go_live_date: date | None = None


class NotesIn(BaseModel):
    notes: str = Field(..., min_length=1, max_length=8000)


class GenerateIn(BaseModel):
    targets: list[str] | None = Field(None, max_length=len(TARGETS))


def _project_data(body: ProjectIn) -> dict:
    data = body.model_dump()
    if data.get("confidentiality_level") is None:
        data.pop("confidentiality_level", None)
    return data


def _http(e: Exception) -> HTTPException:
    if isinstance(e, ProjectSegregation):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


async def _guard(db: AsyncSession, tenant_id: str, user: TokenPayload, project: Project | None, *, version: int | None = None) -> Project:
    """404 se manca, 403 fuori scope (struttura, responsabile o autore), 409 se la versione è stantia."""
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Progetto non trovato")
    scope = await build_scope(db, tenant_id, user)
    if not (scope.unit_visible(project.proposing_unit_id) or _uid(user) in (project.project_owner_id, project.created_by)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    scope.assert_level(project.confidentiality_level, project.project_owner_id, project.created_by)
    if version is not None:
        assert_version(project, version)
    return project


async def _detail(svc: ProjectService, tenant_id: str, project: Project) -> dict:
    return {
        **project.to_dict(),
        "reviews": [r.to_dict() for r in await svc.reviews(tenant_id, project.id)],
        "artifacts": [a.to_dict() for a in await svc.artifacts(tenant_id, project.id)],
    }


@router.get("")
async def list_projects(
    status_filter: ProjectStatus | None = Query(None, alias="status"), unit_id: str | None = None, mine: bool = False,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    rows = await ProjectService(db).search(tenant_id, status=status_filter, unit_id=unit_id)
    uid = _uid(user)
    return [
        p.to_dict() for p in rows
        if (scope.unit_visible(p.proposing_unit_id) or uid in (p.project_owner_id, p.created_by)) and (not mine or uid == p.project_owner_id)
        and scope.level_ok(p.confidentiality_level, p.project_owner_id, p.created_by)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("project.create")), db: AsyncSession = Depends(get_db),
):
    await assert_permission_for_unit(user, db, body.proposing_unit_id, "project.create")
    (await build_scope(db, tenant_id, user)).assert_level_change(None, body.confidentiality_level)
    try:
        project = await ProjectService(db).create(tenant_id, _uid(user), **_project_data(body))
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.get("/{project_id}")
async def get_project(
    project_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id))
    response.headers["ETag"] = etag_of(project)
    return await _detail(svc, tenant_id, project)


@router.patch("/{project_id}")
async def update_project(
    project_id: str, body: ProjectIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("project.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.edit")
    if body.proposing_unit_id != project.proposing_unit_id:
        await assert_permission_for_unit(user, db, body.proposing_unit_id, "project.edit")
    if body.confidentiality_level is not None:
        (await build_scope(db, tenant_id, user)).assert_level_change(
            project.confidentiality_level, body.confidentiality_level, project.project_owner_id, project.created_by)
    try:
        project = await svc.update(tenant_id, project_id, _uid(user), **_project_data(body))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("project.delete")), db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id))
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.delete")
    try:
        await svc.soft_delete(tenant_id, project_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@router.post("/{project_id}/submit")
async def submit_project(
    project_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("project.submit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.submit")
    try:
        project = await svc.submit(tenant_id, project_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.post("/{project_id}/reviews")
async def review_project(
    project_id: str, body: ReviewIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    stage = STAGE_OF.get(project.status)
    if stage is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Il progetto non è in una fase di revisione")
    code = REVIEW_PERMISSION[stage]
    await assert_permission(user, db, code)
    await assert_permission_for_unit(user, db, project.proposing_unit_id, code)
    if stage == ReviewStage.DPO:
        await assert_effective_role(user, db, project.proposing_unit_id, PrivacyRoleCode.DPO)     # la revisione del DPO non si delega
    try:
        project = await svc.review(tenant_id, project_id, _uid(user), body.outcome, body.notes, body.go_live_date)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.post("/{project_id}/advance")
async def advance_project(
    project_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("project.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.edit")
    try:
        project = await svc.advance(tenant_id, project_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.post("/{project_id}/cancel")
async def cancel_project(
    project_id: str, body: NotesIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("project.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.edit")
    try:
        project = await svc.cancel(tenant_id, project_id, _uid(user), body.notes)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.post("/{project_id}/dpia", status_code=status.HTTP_201_CREATED)
async def create_project_dpia(
    project_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("project.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Crea lo screening della DPIA del progetto (e il trattamento a cui si riferisce, se manca)."""
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id), version=version)
    for code in ("project.edit", "dpia.create", "processing.create"):
        await assert_permission_for_unit(user, db, project.proposing_unit_id, code)
    try:
        project = await svc.link_dpia(tenant_id, project_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(project)
    return project.to_dict()


@router.post("/{project_id}/generate", status_code=status.HTTP_201_CREATED)
async def generate_project_items(
    project_id: str, body: GenerateIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("project.generate")), db: AsyncSession = Depends(get_db),
):
    """Crea in bozza gli elementi mancanti (trattamento, sistema, contratto, atto art. 28, informativa, task)."""
    svc = ProjectService(db)
    project = await _guard(db, tenant_id, user, await svc.get(tenant_id, project_id))
    await assert_permission_for_unit(user, db, project.proposing_unit_id, "project.generate")
    supplier = await svc._supplier(tenant_id, project)
    wanted = [t for t in svc.applicable(supplier) if not body.targets or t in body.targets]
    needed = set(wanted)
    if needed & {"asset", "contract", "agreement", "notice"}:
        needed.add("processing")               # questi elementi si agganciano al trattamento, che si crea se manca
    for target in sorted(needed):             # chi genera deve poter creare ciò che genera, come farebbe a mano
        code, on_unit = GENERATION_PERMISSIONS[target]
        if on_unit:
            await assert_permission_for_unit(user, db, project.proposing_unit_id, code)
        else:
            await assert_permission(user, db, code)
    try:
        return await svc.generate(tenant_id, project_id, _uid(user), body.targets)
    except (ComplianceError, ComplianceNotFound, ValueError) as e:
        raise _http(e)
