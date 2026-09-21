"""
PrivacyPA — Regole di conservazione dei trattamenti (§21).

La visibilità e la scrittura seguono lo scope organizzativo del trattamento; le modifiche richiedono
`If-Match` (§54) e aggiornano la versione del trattamento.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_confidentiality import require_admin_step_up
from backend.auth.privacypa_permissions import (
    assert_permission,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_retention import DisposalMethod, RetentionUnit
from backend.services.privacypa_processing_service import ProcessingService
from backend.services.privacypa_retention_service import (
    DISPOSAL_LABEL,
    RetentionError,
    RetentionService,
    describe_period,
)
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa",
    tags=["PrivacyPA - Conservazione"],
    dependencies=[Depends(require_module_view("retention"))],
)


class RetentionRuleIn(BaseModel):
    data_category_id: str | None = None
    record_class: str | None = Field(None, max_length=100)
    retention_value: int | None = None
    retention_unit: RetentionUnit
    retention_criterion: str = Field(..., min_length=1)
    start_trigger: str | None = Field(None, max_length=255)
    legal_reference: str | None = None
    legal_reference_id: str | None = None
    disposal_method: DisposalMethod
    notes: str | None = None


def _present(rule) -> dict:
    payload = rule.to_dict()
    payload["period_text"] = describe_period(rule.retention_unit, rule.retention_value, rule.start_trigger)
    payload["disposal_text"] = DISPOSAL_LABEL[rule.disposal_method]
    return payload


async def _processing(db: AsyncSession, tenant_id: str, processing_id: str):
    activity = await ProcessingService(db).get_activity(tenant_id, processing_id)
    if activity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trattamento non trovato")
    return activity


async def _write_scope(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str):
    return await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code))


@router.get("/processing-activities/{processing_id}/retention-rules")
async def list_retention_rules(
    processing_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _processing(db, tenant_id, processing_id)
    (await build_scope(db, tenant_id, user)).assert_processing(processing_id)
    return [_present(r) for r in await RetentionService(db).list_for_processing(tenant_id, processing_id)]


@router.post("/processing-activities/{processing_id}/retention-rules", status_code=status.HTTP_201_CREATED)
async def create_retention_rule(
    processing_id: str,
    body: RetentionRuleIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("retention.create")),
    db: AsyncSession = Depends(get_db),
):
    await _processing(db, tenant_id, processing_id)
    (await _write_scope(db, tenant_id, user, "retention.create")).assert_processing(processing_id)
    await require_admin_step_up(db, tenant_id, user)              # §136: modifica della conservazione
    try:
        rule = await RetentionService(db).create(tenant_id, user.sub or user.email, processing_id, **body.model_dump())
    except RetentionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return _present(rule)


async def _rule_for(db: AsyncSession, tenant_id: str, user: TokenPayload, rule_id: str, code: str | None):
    """Regola (404) con controllo di scope sul suo trattamento: di lettura o, con `code`, di scrittura (403)."""
    rule = await RetentionService(db).get(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Regola di conservazione non trovata")
    if code:
        await assert_permission(user, db, code)
        scope = await _write_scope(db, tenant_id, user, code)
    else:
        scope = await build_scope(db, tenant_id, user)
    scope.assert_processing(rule.processing_activity_id)
    return rule


@router.get("/retention-rules/{rule_id}")
async def get_retention_rule(
    rule_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _rule_for(db, tenant_id, user, rule_id, None)
    response.headers["ETag"] = etag_of(rule)
    return _present(rule)


@router.patch("/retention-rules/{rule_id}")
async def update_retention_rule(
    rule_id: str,
    body: RetentionRuleIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("retention.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await _rule_for(db, tenant_id, user, rule_id, "retention.edit")
    assert_version(rule, version)
    await require_admin_step_up(db, tenant_id, user)
    try:
        rule = await RetentionService(db).update(tenant_id, rule_id, user.sub or user.email, **body.model_dump())
    except RetentionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return _present(rule)


@router.delete("/retention-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_retention_rule(
    rule_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("retention.delete")),
    db: AsyncSession = Depends(get_db),
):
    await _rule_for(db, tenant_id, user, rule_id, "retention.delete")
    await require_admin_step_up(db, tenant_id, user)
    try:
        await RetentionService(db).soft_delete(tenant_id, rule_id, user.sub or user.email)
    except RetentionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
