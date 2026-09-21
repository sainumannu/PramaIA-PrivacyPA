"""
PrivacyPA — Motore di regole: segnalazioni (finding) e configurazione delle regole (§38, §155).

Le segnalazioni sono persistenti e hanno un ciclo di vita (aperta, presa in carico, accettata con
motivazione, risolta automaticamente). `GET /findings` riallinea prima le segnalazioni ai dati correnti;
`POST /findings/evaluate` fa lo stesso su richiesta ed è il punto di aggancio per uno scheduler esterno.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import VisibilityScope, build_scope
from backend.db.database import get_db
from backend.models.privacypa_rules import ACTIVE_STATUSES, FindingStatus, Severity
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_findings_service import RULES_BY_CODE
from backend.services.privacypa_rules_engine import RuleError, RulesEngine, effective_params, render_condition
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa",
    tags=["PrivacyPA - Regole e segnalazioni"],
    dependencies=[Depends(require_module_view("finding"))],
)

# Segnalazioni mostrate di default: quelle su cui c'è ancora da agire.
DEFAULT_STATUSES = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED)


class FindingStatusIn(BaseModel):
    status: FindingStatus
    notes: str | None = Field(None, max_length=4000)


class RuleUpdateIn(BaseModel):
    enabled: bool | None = None
    severity: Severity | None = None
    params: dict[str, int] | None = None


def visible(scope: VisibilityScope, finding) -> bool:
    """Le segnalazioni su un trattamento seguono lo scope; le altre sono sull'anagrafica condivisa."""
    if finding.processing_activity_id:
        return scope.processing_visible(finding.processing_activity_id)
    if finding.organizational_unit_id:
        return scope.unit_visible(finding.organizational_unit_id)
    return True


async def refreshed_findings(
    db: AsyncSession, tenant_id: str, scope: VisibilityScope, statuses=DEFAULT_STATUSES, **filters
):
    """Riallinea le segnalazioni ai dati correnti e restituisce quelle visibili."""
    engine = RulesEngine(db)
    await engine.evaluate(tenant_id)
    return [f for f in await engine.list_findings(tenant_id, statuses=statuses, **filters) if visible(scope, f)]


@router.get("/findings")
async def list_findings(
    severity: Severity | None = None,
    code: str | None = None,
    status_filter: str | None = Query(None, alias="status", pattern="^(OPEN|ACKNOWLEDGED|ACCEPTED|RESOLVED|ALL)$"),
    refresh: bool = True,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    if status_filter == "ALL":
        statuses = None
    elif status_filter:
        statuses = (FindingStatus(status_filter),)
    else:
        statuses = DEFAULT_STATUSES
    engine = RulesEngine(db)
    if refresh:
        await engine.evaluate(tenant_id)
    items = await engine.list_findings(tenant_id, statuses=statuses, severity=severity, code=code)
    return [f.to_dict() for f in items if visible(scope, f)]


@router.post("/findings/evaluate")
async def evaluate_findings(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("finding.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Rivaluta tutte le regole dell'ente. Idempotente; utilizzabile da uno scheduler esterno."""
    return (await RulesEngine(db).evaluate(tenant_id)).to_dict()


@router.get("/findings/{finding_id}")
async def get_finding(
    finding_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dettaglio con la spiegazione ("Perché vedo questa segnalazione?"): regola, versione, condizione, storico."""
    engine = RulesEngine(db)
    finding = await engine.get_finding(tenant_id, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segnalazione non trovata")
    if not visible(await build_scope(db, tenant_id, user), finding):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    rule = await engine.get_rule(tenant_id, finding.rule_code)
    spec = RULES_BY_CODE.get(finding.rule_code)
    history = await AuditService(db).list_for_entity(tenant_id, "compliance_finding", finding.id)
    response.headers["ETag"] = etag_of(finding)
    return {
        **finding.to_dict(),
        "why": {
            "rule_name": rule.name if rule else None,
            "rule_description": rule.description if rule else None,
            "condition": finding.condition,
            "rule_version": finding.rule_version,
            "current_rule_version": rule.version if rule else None,
            "rule_changed_since": bool(rule and rule.version != finding.rule_version),
            "current_condition": render_condition(spec, rule) if rule and spec else None,
            "params": effective_params(spec, rule) if rule and spec else {},
        },
        "history": [
            {"event_type": e.event_type, "event_time": e.event_time.isoformat(), "actor": e.actor_user_id or "system",
             "details": e.new_values}
            for e in reversed(history)
        ],
    }


@router.post("/findings/{finding_id}/status")
async def change_finding_status(
    finding_id: str,
    body: FindingStatusIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = RulesEngine(db)
    finding = await engine.get_finding(tenant_id, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segnalazione non trovata")
    # Accettare (o revocare un'accettazione) è una decisione di governo; prendere in carico o riaprire no.
    code = "finding.approve" if FindingStatus.ACCEPTED in (body.status, finding.status) else "finding.edit"
    await assert_permission(user, db, code)
    scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code))
    if finding.processing_activity_id:
        scope.assert_processing(finding.processing_activity_id)
    elif finding.organizational_unit_id:
        scope.assert_unit(finding.organizational_unit_id)
    elif not scope.unrestricted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La segnalazione riguarda l'anagrafica dell'ente: serve visibilità sull'intero ente")
    assert_version(finding, version)
    try:
        finding = await engine.change_status(tenant_id, finding, user.sub or user.email, body.status, body.notes)
    except RuleError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return finding.to_dict()


# ---------------------------------------------------------------- regole

def _rule_payload(rule) -> dict:
    spec = RULES_BY_CODE.get(rule.code)
    return {
        **rule.to_dict(),
        "condition": render_condition(spec, rule) if spec else None,
        "params": effective_params(spec, rule) if spec else {},
        "default_params": dict(spec.params) if spec else {},
        "default_severity": spec.severity.value if spec else None,
    }


@router.get("/compliance/rules")
async def list_rules(
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    rules = await RulesEngine(db).ensure_rules(tenant_id)
    order = {spec.code: i for i, spec in enumerate(RULES_BY_CODE.values())}
    return [_rule_payload(r) for r in sorted(rules.values(), key=lambda r: order.get(r.code, 999))]


@router.put("/compliance/rules/{code}")
async def update_rule(
    code: str,
    body: RuleUpdateIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("finding.administer")),
    db: AsyncSession = Depends(get_db),
):
    engine = RulesEngine(db)
    rule = await engine.get_rule(tenant_id, code)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Regola non trovata")
    assert_version(rule, version)
    try:
        rule = await engine.update_rule(
            tenant_id, code, user.sub or user.email, enabled=body.enabled, severity=body.severity, params=body.params
        )
    except RuleError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    response.headers["ETag"] = etag_of(rule)
    return _rule_payload(rule)
