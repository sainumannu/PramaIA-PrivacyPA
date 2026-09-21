from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_approvals import ApprovalDecision
from backend.models.privacypa_assessments import DpiaStatus, SCREENING_CRITERIA, ScreeningOutcome
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.services.privacypa_approval_service import DPIA, ApprovalError, ApprovalService, http_error
from backend.services.privacypa_dpia_service import DpiaService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/dpias",
    tags=["PrivacyPA - DPIA"],
    dependencies=[Depends(require_module_view("dpia"))],
)


def _status_permission(target: DpiaStatus) -> str:
    """Permesso della matrice (§10.1) richiesto da una transizione di stato della DPIA."""
    return "dpia.approve" if target in (DpiaStatus.APPROVED, DpiaStatus.NOT_REQUIRED) else "dpia.edit"


async def _write_scope(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str):
    return await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code))


class ScreeningAnswerIn(BaseModel):
    criterion_code: str
    answer: bool | None = None
    notes: str | None = None


class DpiaUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    processing_activity_id: str | None = None
    screening_outcome: ScreeningOutcome | None = None
    screening_rationale: str | None = None
    description: str | None = None
    necessity_proportionality: str | None = None
    risks_text: str | None = None
    measures_text: str | None = None
    residual_risk_text: str | None = None
    dpo_opinion: str | None = None
    controller_decision: str | None = None
    action_plan: str | None = None
    next_review_date: date | None = None
    # Assente = invariato (in creazione INTERNAL): una modifica che lo omette non abbassa la riservatezza.
    confidentiality_level: ConfidentialityLevel | None = None


class DpiaStatusIn(BaseModel):
    status: DpiaStatus
    notes: str | None = None


class ScreeningIn(BaseModel):
    answers: list[ScreeningAnswerIn]


@router.get("/criteria")
async def screening_criteria():
    return [{"code": c, "label": l} for c, l in SCREENING_CRITERIA]


async def _get_accessible_dpia(
    db: AsyncSession,
    tenant_id: str,
    user: TokenPayload,
    dpia_id: str,
    *,
    write: bool = False,
    version: int | None = None,
    code: str = "dpia.edit",
):
    """Carica la DPIA (404) e verifica permesso e scope sul trattamento collegato (403)."""
    dpia = await DpiaService(db).get(tenant_id, dpia_id)
    if not dpia:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DPIA non trovata")
    if write:
        await assert_permission(user, db, code)
        scope = await _write_scope(db, tenant_id, user, code)
    else:
        scope = await build_scope(db, tenant_id, user)
    scope.assert_processing(dpia.processing_activity_id)
    scope.assert_level(dpia.confidentiality_level, dpia.created_by)
    if version is not None:
        assert_version(dpia, version)
    return dpia


@router.get("")
async def list_dpias(
    status_filter: DpiaStatus | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    items = scope.filter_dpias(await DpiaService(db).list_dpias(tenant_id, status=status_filter))
    return [d.to_dict() for d in items]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_dpia(
    body: DpiaUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dpia.create")),
    db: AsyncSession = Depends(get_db),
):
    scope = await _write_scope(db, tenant_id, user, "dpia.create")
    scope.assert_processing(body.processing_activity_id)
    scope.assert_level_change(None, body.confidentiality_level)
    data = {k: v for k, v in body.model_dump().items() if not (k == "confidentiality_level" and v is None)}
    dpia = await DpiaService(db).create(tenant_id, user.sub or user.email, **data)
    answers = await DpiaService(db).list_answers(dpia.id)
    return {**dpia.to_dict(), "screening": [a.to_dict() for a in answers]}


@router.get("/{dpia_id}")
async def get_dpia(
    dpia_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = DpiaService(db)
    dpia = await _get_accessible_dpia(db, tenant_id, user, dpia_id)
    response.headers["ETag"] = etag_of(dpia)
    answers = await svc.list_answers(dpia_id)
    return {**dpia.to_dict(), "screening": [a.to_dict() for a in answers]}


@router.patch("/{dpia_id}")
async def update_dpia(
    dpia_id: str,
    body: DpiaUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("dpia.edit")),
    db: AsyncSession = Depends(get_db),
):
    current = await _get_accessible_dpia(db, tenant_id, user, dpia_id, write=True, version=version)
    scope = await _write_scope(db, tenant_id, user, "dpia.edit")
    scope.assert_processing(body.processing_activity_id)
    if body.confidentiality_level is not None:
        scope.assert_level_change(current.confidentiality_level, body.confidentiality_level, current.created_by)
    try:
        data = {k: v for k, v in body.model_dump().items() if not (k == "confidentiality_level" and v is None)}
        dpia = await DpiaService(db).update(tenant_id, dpia_id, user.sub or user.email, **data)
        answers = await DpiaService(db).list_answers(dpia.id)
        return {**dpia.to_dict(), "screening": [a.to_dict() for a in answers]}
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.put("/{dpia_id}/screening")
async def save_screening(
    dpia_id: str,
    body: ScreeningIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("dpia.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _get_accessible_dpia(db, tenant_id, user, dpia_id, write=True, version=version)
    try:
        dpia = await DpiaService(db).save_screening(
            tenant_id, dpia_id, user.sub or user.email, [a.model_dump() for a in body.answers]
        )
        answers = await DpiaService(db).list_answers(dpia.id)
        return {**dpia.to_dict(), "screening": [a.to_dict() for a in answers]}
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/{dpia_id}/status")
async def change_dpia_status(
    dpia_id: str,
    body: DpiaStatusIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = DpiaService(db)
    approvals = ApprovalService(db)
    actor = user.sub or user.email
    # Il permesso dipende dalla transizione (§10.1) e deve valere sulla struttura del trattamento collegato.
    dpia = await _get_accessible_dpia(
        db, tenant_id, user, dpia_id, write=True, version=version, code=_status_permission(body.status)
    )
    origin, target = dpia.status, body.status
    try:
        if target == DpiaStatus.APPROVED:
            info = await approvals.load_entity(tenant_id, DPIA, dpia_id)
            approval = await approvals.ensure_pending(tenant_id, info)
            await approvals.decide(tenant_id, approval, actor, ApprovalDecision.APPROVED, body.notes)
        elif origin == DpiaStatus.AWAITING_DECISION and target == DpiaStatus.IN_PROGRESS:
            info = await approvals.load_entity(tenant_id, DPIA, dpia_id)
            pending = await approvals.pending_for(tenant_id, DPIA, dpia_id)
            decides = (
                pending is not None
                and pending.requested_by != actor
                and await can_for_unit(user, db, info.unit_id, "dpia.approve")
            )
            if decides:
                await approvals.decide(
                    tenant_id, pending, actor, ApprovalDecision.CHANGES_REQUESTED, body.notes
                )
            else:
                await svc.transition(tenant_id, dpia_id, actor, target)
                await approvals.cancel_pending(tenant_id, DPIA, dpia_id, actor)
        else:
            await svc.transition(tenant_id, dpia_id, actor, target)
            if target == DpiaStatus.AWAITING_DECISION:
                info = await approvals.load_entity(tenant_id, DPIA, dpia_id)
                await approvals.request_review(tenant_id, info, actor, body.notes)
        return (await svc.get(tenant_id, dpia_id)).to_dict()
    except ApprovalError as e:
        raise http_error(e)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
