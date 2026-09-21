"""
PrivacyPA — Pareri del DPO e DPO workspace (§43), relazione annuale (§42).

Il parere è separato dalla decisione del titolare e si emette solo con un ruolo DPO effettivamente assegnato
sulla struttura dell'elemento: gli amministratori del Portal non lo emettono al posto del DPO. La relazione
annuale aggrega dati di tutto l'ente, quindi richiede visibilità sull'intero ente.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import assert_effective_role, require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission_for_unit,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode
from backend.models.privacypa_people import OpinionStatus
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound
from backend.services.privacypa_dpo_service import (
    AnnualReportService,
    DpoWorkspaceService,
    OpinionService,
    VersionConflict,
    annual_report_to_docx,
)
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter()


def _http(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


# ================================================================ pareri

op_router = APIRouter(
    prefix="/api/privacypa/dpo/opinions", tags=["PrivacyPA - Pareri del DPO"], dependencies=[Depends(require_module_view("opinion"))],
)


class OpinionRequestIn(BaseModel):
    entity_type: str = Field(..., max_length=40)
    entity_id: str = Field(..., max_length=36)
    notes: str | None = Field(None, max_length=4000)
    dpo_user_id: str | None = Field(None, max_length=100)


class OpinionIssueIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


async def _visible(db: AsyncSession, tenant_id: str, user: TokenPayload, opinion) -> None:
    if opinion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parere non trovato")
    scope = await build_scope(db, tenant_id, user)
    if not (scope.unit_visible(opinion.organizational_unit_id) or _uid(user) in (opinion.requested_by, opinion.dpo_user_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")


@op_router.get("")
async def list_opinions(
    entity_type: str | None = None, entity_id: str | None = None, status_filter: OpinionStatus | None = Query(None, alias="status"),
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    rows = await OpinionService(db).search(tenant_id, entity_type=entity_type, entity_id=entity_id, status=status_filter)
    return [o.to_dict() for o in rows if scope.unit_visible(o.organizational_unit_id) or _uid(user) in (o.requested_by, o.dpo_user_id)]


@op_router.post("", status_code=status.HTTP_201_CREATED)
async def request_opinion(
    body: OpinionRequestIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("opinion.create")), db: AsyncSession = Depends(get_db),
):
    svc = OpinionService(db)
    try:
        code, _, unit = await svc.entity_ref(tenant_id, body.entity_type, body.entity_id)
    except ComplianceError as e:
        raise _http(e)
    if code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Elemento non trovato")
    await assert_permission_for_unit(user, db, unit, "opinion.create")
    try:
        opinion = await svc.request(tenant_id, _uid(user), body.entity_type, body.entity_id, body.notes, body.dpo_user_id)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(opinion)
    return opinion.to_dict()


@op_router.get("/{opinion_id}")
async def get_opinion(
    opinion_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    opinion = await OpinionService(db).get(tenant_id, opinion_id)
    await _visible(db, tenant_id, user, opinion)
    response.headers["ETag"] = etag_of(opinion)
    return opinion.to_dict()


@op_router.post("/{opinion_id}/issue")
async def issue_opinion(
    opinion_id: str, body: OpinionIssueIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("opinion.approve")),
    db: AsyncSession = Depends(get_db),
):
    svc = OpinionService(db)
    opinion = await svc.get(tenant_id, opinion_id)
    await _visible(db, tenant_id, user, opinion)
    await assert_effective_role(user, db, opinion.organizational_unit_id, PrivacyRoleCode.DPO)      # il parere non si delega
    assert_version(opinion, version)
    try:
        opinion = await svc.issue(tenant_id, opinion_id, _uid(user), body.text)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(opinion)
    return opinion.to_dict()


@op_router.post("/{opinion_id}/withdraw")
async def withdraw_opinion(
    opinion_id: str, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("opinion.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = OpinionService(db)
    opinion = await svc.get(tenant_id, opinion_id)
    await _visible(db, tenant_id, user, opinion)
    await assert_permission_for_unit(user, db, opinion.organizational_unit_id, "opinion.edit")
    assert_version(opinion, version)
    try:
        opinion = await svc.withdraw(tenant_id, opinion_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(opinion)
    return opinion.to_dict()


# ================================================================ workspace

ws_router = APIRouter(
    prefix="/api/privacypa/dpo/workspace", tags=["PrivacyPA - DPO workspace"], dependencies=[Depends(require_module_view("dpo"))],
)


@ws_router.get("")
async def workspace(
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, "dpo.view"))
    return await DpoWorkspaceService(db).build(tenant_id, scope, _uid(user))


# ================================================================ relazione annuale

rp_router = APIRouter(
    prefix="/api/privacypa/reports/annual", tags=["PrivacyPA - Relazione annuale"], dependencies=[Depends(require_module_view("report"))],
)


class CommentsIn(BaseModel):
    comments: dict[str, str] = Field(default_factory=dict)


async def _tenant_wide(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str) -> None:
    scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code))
    if not scope.unrestricted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La relazione annuale richiede visibilità sull'intero ente")


def _conflict(e: VersionConflict) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(e), headers={"ETag": f'"{e.current}"'})


@rp_router.get("/{year}")
async def get_report(
    year: int, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await _tenant_wide(db, tenant_id, user, "report.view")
    try:
        view = await AnnualReportService(db).view(tenant_id, year)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = f'"{view["row_version"]}"'
    return view


@rp_router.put("/{year}/comments")
async def save_comments(
    year: int, body: CommentsIn, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("report.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _tenant_wide(db, tenant_id, user, "report.edit")
    svc = AnnualReportService(db)
    try:
        report = await svc.save_comments(tenant_id, year, _uid(user), body.comments, version)
    except VersionConflict as e:
        raise _conflict(e)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(report)
    return await svc.view(tenant_id, year)


@rp_router.post("/{year}/finalize")
async def finalize_report(
    year: int, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("report.approve")),
    db: AsyncSession = Depends(get_db),
):
    await _tenant_wide(db, tenant_id, user, "report.approve")
    svc = AnnualReportService(db)
    try:
        report = await svc.finalize(tenant_id, year, _uid(user), version)
    except VersionConflict as e:
        raise _conflict(e)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(report)
    return await svc.view(tenant_id, year)


@rp_router.post("/{year}/reopen")
async def reopen_report(
    year: int, response: Response, tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match), user: TokenPayload = Depends(require_permission("report.reopen")),
    db: AsyncSession = Depends(get_db),
):
    await _tenant_wide(db, tenant_id, user, "report.reopen")
    svc = AnnualReportService(db)
    try:
        report = await svc.reopen(tenant_id, year, _uid(user), version)
    except VersionConflict as e:
        raise _conflict(e)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(report)
    return await svc.view(tenant_id, year)


@rp_router.get("/{year}/export")
async def export_report(
    year: int, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _tenant_wide(db, tenant_id, user, "report.view")
    try:
        view = await AnnualReportService(db).view(tenant_id, year)
    except ComplianceError as e:
        raise _http(e)
    return RawResponse(
        content=annual_report_to_docx(view),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="relazione-annuale-{year}.docx"'},
    )


router.include_router(op_router)
router.include_router(ws_router)
router.include_router(rp_router)
