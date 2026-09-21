"""
PrivacyPA — Coda delle approvazioni (§165, §166, AC-REG-003).

Elenca le richieste di approvazione visibili all'utente e registra la decisione. Le richieste nascono
quando un trattamento passa in revisione o una DPIA in attesa di decisione (vedi i router dei moduli).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    assert_permission_for_unit,
    can_for_unit,
    require_module_view,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_approvals import Approval, ApprovalDecision, ApprovalState
from backend.services.privacypa_approval_service import (
    APPROVE_PERMISSION,
    ApprovalError,
    ApprovalService,
    http_error,
)

router = APIRouter(
    prefix="/api/privacypa/approvals",
    tags=["PrivacyPA - Approvazioni"],
    dependencies=[Depends(require_module_view("approval"))],
)


class DecisionIn(BaseModel):
    decision: ApprovalDecision
    notes: str | None = Field(None, max_length=4000)


async def _describe(
    db: AsyncSession,
    svc: ApprovalService,
    tenant_id: str,
    user: TokenPayload,
    approval: Approval,
    unit_permissions: dict,
) -> dict:
    """Approvazione + indicatori per l'interfaccia: contenuto cambiato e possibilità di decidere."""
    payload = approval.to_dict()
    payload.update(stale=False, can_decide=False, blocked_reason=None)
    if approval.state != ApprovalState.PENDING:
        return payload
    info = await svc.load_entity(tenant_id, approval.entity_type, approval.entity_id)
    if svc.is_stale(approval, info):
        payload.update(stale=True, blocked_reason="Il contenuto è cambiato dopo la richiesta: va inviato di nuovo")
        return payload
    key = (approval.entity_type, approval.organizational_unit_id)
    if key not in unit_permissions:
        unit_permissions[key] = await can_for_unit(
            user, db, approval.organizational_unit_id, APPROVE_PERMISSION[approval.entity_type]
        )
    if not unit_permissions[key]:
        payload["blocked_reason"] = "Permesso di approvazione non valido sulla struttura del record"
        return payload
    problem = await svc.segregation_problem(tenant_id, approval, user.sub or user.email)
    payload.update(can_decide=problem is None, blocked_reason=problem)
    return payload


@router.get("")
async def list_approvals(
    state: ApprovalState | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    decidable: bool = Query(False, description="solo richieste che l'utente può decidere ora"),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if entity_type and entity_type not in APPROVE_PERMISSION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Tipo di entità non gestito: {entity_type}")
    svc = ApprovalService(db)
    scope = await build_scope(db, tenant_id, user)
    rows = await svc.search(
        tenant_id, entity_type=entity_type, entity_id=entity_id, state=state or (ApprovalState.PENDING if decidable else None)
    )
    cache: dict = {}
    items = []
    for approval in rows:
        if not scope.unit_visible(approval.organizational_unit_id):
            continue
        item = await _describe(db, svc, tenant_id, user, approval, cache)
        if decidable and not item["can_decide"]:
            continue
        items.append(item)
    return items


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApprovalService(db)
    approval = await svc.get(tenant_id, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Richiesta di approvazione non trovata")
    (await build_scope(db, tenant_id, user)).assert_unit(approval.organizational_unit_id)
    return await _describe(db, svc, tenant_id, user, approval, {})


@router.post("/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: DecisionIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApprovalService(db)
    approval = await svc.get(tenant_id, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Richiesta di approvazione non trovata")
    code = APPROVE_PERMISSION[approval.entity_type]
    await assert_permission(user, db, code)
    (await build_scope(db, tenant_id, user)).assert_unit(approval.organizational_unit_id)
    await assert_permission_for_unit(user, db, approval.organizational_unit_id, code)
    try:
        await svc.decide(tenant_id, approval, user.sub or user.email, body.decision, body.notes)
    except ApprovalError as e:
        raise http_error(e)
    except ValueError as e:  # errori di dominio della transizione (es. DPIA senza decisione del titolare)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return await _describe(db, svc, tenant_id, user, approval, {})
