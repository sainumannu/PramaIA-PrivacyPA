"""
PrivacyPA — Motore di regole: configurazione per ente, valutazione e ciclo di vita dei finding (§38, §155).

`evaluate` è idempotente: confronta le violazioni correnti con i finding attivi, apre quelli nuovi e chiude
automaticamente (RESOLVED) quelli la cui condizione non sussiste più. Non tocca i finding presi in carico o
accettati se la condizione persiste. Ogni finding conserva versione e condizione della regola originaria.
"""
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_audit import ActorType
from backend.models.privacypa_rules import (
    ACTIVE_STATUSES,
    ComplianceFinding,
    ComplianceRule,
    FindingStatus,
    Severity,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_findings_service import RULES, RULES_BY_CODE, Context, Hit, RuleSpec

SYSTEM = "system"

# Transizioni manuali (RESOLVED è solo automatico: un finding risolto che si ripresenta è un nuovo finding).
_MANUAL = {
    FindingStatus.OPEN: {FindingStatus.ACKNOWLEDGED, FindingStatus.ACCEPTED},
    FindingStatus.ACKNOWLEDGED: {FindingStatus.OPEN, FindingStatus.ACCEPTED},
    FindingStatus.ACCEPTED: {FindingStatus.OPEN},
}


class RuleError(ValueError):
    """Configurazione di regola o transizione non valida (400)."""


class FindingNotFound(LookupError):
    pass


@dataclass
class EvaluationStats:
    opened: int = 0
    resolved: int = 0
    updated: int = 0
    evaluated_rules: int = 0

    def to_dict(self) -> dict:
        return {"opened": self.opened, "resolved": self.resolved, "updated": self.updated, "evaluated_rules": self.evaluated_rules}


def effective_params(spec: RuleSpec, rule: ComplianceRule) -> dict:
    return {**spec.params, **((rule.rule_definition or {}).get("params") or {})}


def render_condition(spec: RuleSpec, rule: ComplianceRule) -> str:
    template = (rule.rule_definition or {}).get("condition") or spec.condition
    try:
        return template.format(**effective_params(spec, rule))
    except (KeyError, IndexError, ValueError):
        return template


def _fingerprint(code: str, hit: Hit) -> str:
    return f"{code}:{hit.entity_type}:{hit.entity_id}:{hit.subject}"


class RulesEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    # ------------------------------------------------------------ regole
    async def ensure_rules(self, tenant_id: str) -> dict[str, ComplianceRule]:
        """Le regole dell'ente; quelle mancanti (nuove versioni dell'applicazione) vengono create con i default."""
        rows = (await self.db.execute(select(ComplianceRule).where(ComplianceRule.tenant_id == tenant_id))).scalars().all()
        by_code = {r.code: r for r in rows}
        created = False
        for spec in RULES:
            if spec.code in by_code:
                continue
            rule = ComplianceRule(
                tenant_id=tenant_id, code=spec.code, name=spec.name, description=spec.description,
                severity=spec.severity, enabled=True,
                rule_definition={"condition": spec.condition, "params": dict(spec.params)}, updated_by=SYSTEM,
            )
            self.db.add(rule)
            by_code[spec.code] = rule
            created = True
        if created:
            await self.db.flush()
        return by_code

    async def get_rule(self, tenant_id: str, code: str) -> ComplianceRule | None:
        return (await self.ensure_rules(tenant_id)).get(code)

    async def update_rule(
        self, tenant_id: str, code: str, actor: str, *, enabled: bool | None = None,
        severity: Severity | None = None, params: dict | None = None,
    ) -> ComplianceRule:
        rule = await self.get_rule(tenant_id, code)
        spec = RULES_BY_CODE.get(code)
        if rule is None or spec is None:
            raise RuleError("Regola non trovata")
        old = rule.to_dict()
        if params is not None:
            unknown = set(params) - set(spec.params)
            if unknown:
                raise RuleError(f"Parametri non supportati da {code}: {', '.join(sorted(unknown))}")
            for key, value in params.items():
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3650:
                    raise RuleError(f"Il parametro {key} deve essere un intero tra 1 e 3650")
            definition = dict(rule.rule_definition)
            definition["params"] = {**spec.params, **params}
            rule.rule_definition = definition
        if enabled is not None:
            rule.enabled = enabled
        if severity is not None:
            rule.severity = severity
        rule.updated_by, rule.updated_at = actor, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "compliance_rule", "update", event_type="rule.updated", entity_id=rule.id,
            actor_user_id=actor, old_values=old, new_values=rule.to_dict(),
        )
        return rule

    # ------------------------------------------------------------ valutazione
    async def _active(self, tenant_id: str) -> dict[str, ComplianceFinding]:
        rows = (await self.db.execute(select(ComplianceFinding).where(
            ComplianceFinding.tenant_id == tenant_id, ComplianceFinding.status.in_(ACTIVE_STATUSES)))).scalars().all()
        return {f.active_key: f for f in rows if f.active_key}

    def _close(self, finding: ComplianceFinding, notes: str) -> None:
        finding.status = FindingStatus.RESOLVED
        finding.active_key = None
        finding.resolved_at = datetime.utcnow()
        finding.status_changed_by, finding.status_changed_at = SYSTEM, finding.resolved_at
        finding.resolution_notes = notes

    async def evaluate(self, tenant_id: str, today: date | None = None) -> EvaluationStats:
        today = today or date.today()
        stats = EvaluationStats()
        rules = await self.ensure_rules(tenant_id)
        ctx = Context(self.db, tenant_id, today)
        active = await self._active(tenant_id)
        seen: set[str] = set()

        for spec in RULES:
            rule = rules[spec.code]
            if not rule.enabled:
                continue
            stats.evaluated_rules += 1
            condition = render_condition(spec, rule)
            for hit in await spec.evaluator(ctx, effective_params(spec, rule)):
                key = _fingerprint(spec.code, hit)
                seen.add(key)
                existing = active.get(key)
                if existing is not None:
                    changed = (existing.message, existing.params, existing.due_date) != (hit.message, hit.params, hit.due_date)
                    if changed:
                        existing.message, existing.params, existing.due_date = hit.message, hit.params, hit.due_date
                        stats.updated += 1
                    continue
                finding = ComplianceFinding(
                    tenant_id=tenant_id, rule_id=rule.id, rule_code=spec.code, rule_version=rule.version,
                    severity=hit.severity or rule.severity, condition=condition, entity_type=hit.entity_type, entity_id=hit.entity_id,
                    entity_code=hit.entity_code, processing_activity_id=hit.processing_id, organizational_unit_id=hit.unit_id, fingerprint=key, active_key=key,
                    message=hit.message, params=hit.params, due_date=hit.due_date, status=FindingStatus.OPEN,
                )
                try:
                    async with self.db.begin_nested():
                        self.db.add(finding)
                        await self.db.flush()
                except IntegrityError:
                    continue  # un'altra valutazione concorrente lo ha già aperto
                stats.opened += 1
                active[key] = finding
                await self.audit.log(
                    tenant_id, "compliance_finding", "open", event_type="finding.opened", entity_id=finding.id,
                    actor_type=ActorType.SYSTEM, new_values={**hit.event_data, "rule_code": spec.code, "rule_version": rule.version, "entity_id": hit.entity_id},
                )
                if spec.on_open is not None:
                    await spec.on_open(ctx, hit)

        for key, finding in list(active.items()):
            if key in seen:
                continue
            rule_enabled = rules.get(finding.rule_code) is not None and rules[finding.rule_code].enabled
            self._close(finding, "Condizione non più rilevata" if rule_enabled else "Regola disabilitata")
            stats.resolved += 1
            await self.audit.log(
                tenant_id, "compliance_finding", "resolve", event_type="finding.resolved", entity_id=finding.id,
                actor_type=ActorType.SYSTEM, new_values={"rule_code": finding.rule_code, "notes": finding.resolution_notes},
            )
        await self.db.flush()
        return stats

    # ------------------------------------------------------------ finding
    async def list_findings(
        self, tenant_id: str, *, statuses: tuple[FindingStatus, ...] | None = None,
        severity: Severity | None = None, code: str | None = None,
    ) -> list[ComplianceFinding]:
        stmt = select(ComplianceFinding).where(ComplianceFinding.tenant_id == tenant_id)
        if statuses:
            stmt = stmt.where(ComplianceFinding.status.in_(statuses))
        if severity:
            stmt = stmt.where(ComplianceFinding.severity == severity)
        if code:
            stmt = stmt.where(ComplianceFinding.rule_code == code)
        rows = list((await self.db.execute(stmt)).scalars().all())
        order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        rows.sort(key=lambda f: (order[f.severity], f.rule_code, f.due_date or date.max, f.entity_code or "", f.id))
        return rows

    async def get_finding(self, tenant_id: str, finding_id: str) -> ComplianceFinding | None:
        row = await self.db.get(ComplianceFinding, finding_id)
        return row if row and row.tenant_id == tenant_id else None

    async def change_status(
        self, tenant_id: str, finding: ComplianceFinding, actor: str, target: FindingStatus, notes: str | None
    ) -> ComplianceFinding:
        if target not in _MANUAL.get(finding.status, set()):
            raise RuleError(f"Transizione non ammessa: {finding.status.value} → {target.value}")
        notes = (notes or "").strip() or None
        if target == FindingStatus.ACCEPTED and not notes:
            raise RuleError("Per accettare una segnalazione serve una motivazione")
        old = finding.status
        finding.status = target
        finding.status_changed_by, finding.status_changed_at = actor, datetime.utcnow()
        finding.resolution_notes = notes if target == FindingStatus.ACCEPTED else None
        await self.db.flush()
        await self.audit.log(
            tenant_id, "compliance_finding", "status_change", event_type=f"finding.{target.value.lower()}",
            entity_id=finding.id, actor_user_id=actor, old_values={"status": old.value},
            new_values={"status": target.value, "notes": notes},
        )
        return finding
