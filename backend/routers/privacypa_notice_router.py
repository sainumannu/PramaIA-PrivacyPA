"""
PrivacyPA — Informative (§25): elenco, versioni, collegamento ai trattamenti, approvazione e confronto.

Visibilità e scrittura seguono lo scope della struttura proprietaria; ogni modifica richiede `If-Match` (§54).
L'invio in approvazione e la decisione passano dal workflow comune delle approvazioni (§165).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    can_for_unit,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import VisibilityScope, build_scope
from backend.db.database import get_db
from backend.models.privacypa_approvals import ApprovalDecision
from backend.models.privacypa_notices import NoticeStatus
from backend.services.privacypa_approval_service import NOTICE, ApprovalError, ApprovalService, http_error
from backend.services.privacypa_notice_service import NoticeError, NoticeIncomplete, NoticeService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/notices",
    tags=["PrivacyPA - Informative"],
    dependencies=[Depends(require_module_view("notice"))],
)


class NoticeIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    audience: str | None = Field(None, max_length=255)
    owner_unit_id: str


class NoticeCreateIn(NoticeIn):
    version_label: str | None = Field(None, max_length=50)
    content: str | None = None
    effective_from: date | None = None
    published_url: str | None = Field(None, max_length=500)
    document_id: str | None = None


class VersionIn(BaseModel):
    version_label: str | None = Field(None, max_length=50)
    content: str | None = None
    effective_from: date | None = None
    published_url: str | None = Field(None, max_length=500)
    document_id: str | None = None


class NewVersionIn(VersionIn):
    version_label: str = Field(..., min_length=1, max_length=50)
    copy_current: bool = True


class LinksIn(BaseModel):
    processing_activity_ids: list[str] = Field(default_factory=list, max_length=500)


class StatusIn(BaseModel):
    status: NoticeStatus
    notes: str | None = Field(None, max_length=4000)


class ReviewIn(BaseModel):
    notes: str | None = Field(None, max_length=4000)


def _status_permission(target: NoticeStatus) -> str:
    """Permesso della matrice (§10.1) richiesto da una transizione di stato."""
    return {
        NoticeStatus.IN_REVIEW: "notice.submit", NoticeStatus.DRAFT: "notice.submit",
        NoticeStatus.APPROVED: "notice.approve", NoticeStatus.RETIRED: "notice.archive",
    }[target]


def _bad(e: NoticeError) -> HTTPException:
    code = status.HTTP_422_UNPROCESSABLE_ENTITY if isinstance(e, NoticeIncomplete) else status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(e))


async def _scope(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str | None = None) -> VisibilityScope:
    roles = await roles_for(db, tenant_id, code) if code else None
    return await build_scope(db, tenant_id, user, roles=roles)


async def _load(
    db: AsyncSession, tenant_id: str, user: TokenPayload, notice_id: str, *, code: str | None = None,
    version: int | None = None,
):
    """Informativa (404), con permesso e scope sulla struttura proprietaria (403) e versione (409)."""
    notice = await NoticeService(db).get(tenant_id, notice_id)
    if notice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Informativa non trovata")
    if code:
        await assert_permission(user, db, code)
    (await _scope(db, tenant_id, user, code)).assert_unit(notice.owner_unit_id)
    if version is not None:
        assert_version(notice, version)
    return notice


def _detail(notice, response: Response | None = None) -> dict:
    if response is not None:
        response.headers["ETag"] = etag_of(notice)
    return notice.to_dict(include_versions=True)


@router.get("")
async def list_notices(
    status_filter: NoticeStatus | None = Query(None, alias="status"),
    processing_activity_id: str | None = None,
    search: str | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await _scope(db, tenant_id, user)
    rows = await NoticeService(db).search(
        tenant_id, status=status_filter, processing_id=processing_activity_id, search=search
    )
    return [n.to_dict() for n in rows if scope.unit_visible(n.owner_unit_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_notice(
    body: NoticeCreateIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("notice.create")),
    db: AsyncSession = Depends(get_db),
):
    (await _scope(db, tenant_id, user, "notice.create")).assert_unit(body.owner_unit_id)
    data = body.model_dump()
    try:
        notice = await NoticeService(db).create(
            tenant_id, user.sub or user.email, data.pop("title"), data.pop("owner_unit_id"), data.pop("audience"), **data
        )
    except NoticeError as e:
        raise _bad(e)
    return _detail(notice, response)


@router.get("/{notice_id}")
async def get_notice(
    notice_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return _detail(await _load(db, tenant_id, user, notice_id), response)


@router.patch("/{notice_id}")
async def update_notice(
    notice_id: str,
    body: NoticeIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("notice.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _load(db, tenant_id, user, notice_id, code="notice.edit", version=version)
    (await _scope(db, tenant_id, user, "notice.edit")).assert_unit(body.owner_unit_id)
    try:
        notice = await NoticeService(db).update(
            tenant_id, notice_id, user.sub or user.email, title=body.title, audience=body.audience,
            owner_unit_id=body.owner_unit_id,
        )
    except NoticeError as e:
        raise _bad(e)
    return _detail(notice, response)


@router.delete("/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notice(
    notice_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("notice.delete")),
    db: AsyncSession = Depends(get_db),
):
    await _load(db, tenant_id, user, notice_id, code="notice.delete")
    try:
        await NoticeService(db).soft_delete(tenant_id, notice_id, user.sub or user.email)
    except NoticeError as e:
        raise _bad(e)


@router.post("/{notice_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    notice_id: str,
    body: NewVersionIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("notice.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _load(db, tenant_id, user, notice_id, code="notice.edit", version=version)
    try:
        await NoticeService(db).create_version(tenant_id, notice_id, user.sub or user.email, **body.model_dump())
    except NoticeError as e:
        raise _bad(e)
    return _detail(await NoticeService(db).get(tenant_id, notice_id), response)


@router.patch("/{notice_id}/versions/{version_id}")
async def update_version(
    notice_id: str,
    version_id: str,
    body: VersionIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("notice.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _load(db, tenant_id, user, notice_id, code="notice.edit", version=version)
    try:
        await NoticeService(db).update_version(
            tenant_id, notice_id, version_id, user.sub or user.email, **body.model_dump(exclude_unset=True)
        )
    except NoticeError as e:
        raise _bad(e)
    return _detail(await NoticeService(db).get(tenant_id, notice_id), response)


@router.get("/{notice_id}/versions/{old_id}/diff/{new_id}")
async def diff_versions(
    notice_id: str,
    old_id: str,
    new_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confronto parola per parola tra due versioni (§32)."""
    await _load(db, tenant_id, user, notice_id)
    try:
        return await NoticeService(db).diff(tenant_id, notice_id, old_id, new_id)
    except NoticeError as e:
        raise _bad(e)


@router.put("/{notice_id}/processing-links")
async def set_links(
    notice_id: str,
    body: LinksIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("notice.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _load(db, tenant_id, user, notice_id, code="notice.edit", version=version)
    scope = await _scope(db, tenant_id, user, "notice.edit")
    for pid in set(body.processing_activity_ids):
        scope.assert_processing(pid)
    try:
        notice = await NoticeService(db).set_links(tenant_id, notice_id, user.sub or user.email, body.processing_activity_ids)
    except NoticeError as e:
        raise _bad(e)
    return _detail(notice, response)


@router.post("/{notice_id}/confirm-review")
async def confirm_review(
    notice_id: str,
    body: ReviewIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("notice.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Conferma che l'informativa resta coerente con i trattamenti collegati (chiude NOTICE_REVIEW_REQUIRED)."""
    await _load(db, tenant_id, user, notice_id, code="notice.edit", version=version)
    try:
        notice = await NoticeService(db).confirm_review(tenant_id, notice_id, user.sub or user.email, body.notes)
    except NoticeError as e:
        raise _bad(e)
    return _detail(notice, response)


@router.post("/{notice_id}/status")
async def change_status(
    notice_id: str,
    body: StatusIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc, approvals = NoticeService(db), ApprovalService(db)
    actor = user.sub or user.email
    existing = await _load(db, tenant_id, user, notice_id, code=_status_permission(body.status), version=version)
    origin, target = existing.status, body.status
    try:
        if origin == NoticeStatus.IN_REVIEW and target == NoticeStatus.APPROVED:
            info = await approvals.load_entity(tenant_id, NOTICE, notice_id)
            approval = await approvals.ensure_pending(tenant_id, info)
            await approvals.decide(tenant_id, approval, actor, ApprovalDecision.APPROVED, body.notes)
        elif origin == NoticeStatus.IN_REVIEW and target == NoticeStatus.DRAFT:
            pending = await approvals.pending_for(tenant_id, NOTICE, notice_id)
            decides = (
                pending is not None
                and pending.requested_by != actor
                and await can_for_unit(user, db, existing.owner_unit_id, "notice.approve")
            )
            if decides:
                await approvals.decide(tenant_id, pending, actor, ApprovalDecision.CHANGES_REQUESTED, body.notes)
            else:
                await svc.transition(tenant_id, notice_id, actor, target)
                await approvals.cancel_pending(tenant_id, NOTICE, notice_id, actor)
        else:
            await svc.transition(tenant_id, notice_id, actor, target)
            if target == NoticeStatus.IN_REVIEW:
                info = await approvals.load_entity(tenant_id, NOTICE, notice_id)
                await approvals.request_review(tenant_id, info, actor, body.notes)
    except ApprovalError as e:
        raise http_error(e)
    except NoticeError as e:
        raise _bad(e)
    return _detail(await svc.get(tenant_id, notice_id), response)
