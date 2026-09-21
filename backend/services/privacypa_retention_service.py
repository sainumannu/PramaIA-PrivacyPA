"""
PrivacyPA — Regole di conservazione dei dati (§21).

Una regola appartiene a un trattamento e, facoltativamente, a una delle sue categorie di dati. Ogni
modifica aggiorna anche il trattamento (versione riga): una richiesta di approvazione già inviata non
vale più per un contenuto cambiato (§54, §165).
"""
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_catalog import DataCategoryCatalog
from backend.models.privacypa_processing import ProcessingDataCategory
from backend.models.privacypa_retention import DisposalMethod, RetentionRule, RetentionUnit
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_processing_service import ProcessingService

_EDITABLE = {
    "data_category_id", "record_class", "retention_value", "retention_unit", "retention_criterion",
    "start_trigger", "legal_reference", "legal_reference_id", "disposal_method", "notes",
}
_TIMED = (RetentionUnit.DAYS, RetentionUnit.MONTHS, RetentionUnit.YEARS)
_MAX_VALUE = {RetentionUnit.DAYS: 36500, RetentionUnit.MONTHS: 1200, RetentionUnit.YEARS: 100}

_UNIT_LABEL = {
    RetentionUnit.DAYS: ("giorno", "giorni"),
    RetentionUnit.MONTHS: ("mese", "mesi"),
    RetentionUnit.YEARS: ("anno", "anni"),
}
DISPOSAL_LABEL = {
    DisposalMethod.DELETION: "cancellazione",
    DisposalMethod.ANONYMIZATION: "anonimizzazione",
    DisposalMethod.ARCHIVAL: "archiviazione",
    DisposalMethod.TRANSFER_TO_ARCHIVE: "trasferimento all'archivio",
    DisposalMethod.OTHER: "altro",
}


class RetentionError(ValueError):
    """Regola non valida o riferimenti non coerenti (400)."""


def describe_period(unit: RetentionUnit, value: int | None, trigger: str | None = None) -> str:
    """Termine in chiaro, per interfaccia e report art. 30 (es. "10 anni dalla cessazione del rapporto")."""
    if unit == RetentionUnit.PERMANENT:
        return "conservazione illimitata"
    if unit == RetentionUnit.EVENT_BASED:
        return f"fino a: {trigger}" if trigger else "legato a un evento"
    singular, plural = _UNIT_LABEL[unit]
    text = f"{value} {singular if value == 1 else plural}"
    return f"{text} da: {trigger}" if trigger else text


def validate_rule(fields: dict) -> None:
    """Coerenza interna della regola (§21); RetentionError se non valida."""
    unit, value = fields.get("retention_unit"), fields.get("retention_value")
    if unit is None:
        raise RetentionError("L'unità di conservazione è obbligatoria")
    if not (fields.get("retention_criterion") or "").strip():
        raise RetentionError("Il criterio di conservazione è obbligatorio")
    if unit in _TIMED:
        if value is None or value < 1:
            raise RetentionError("Per un termine in giorni, mesi o anni serve una durata maggiore di zero")
        if value > _MAX_VALUE[unit]:
            raise RetentionError(f"Durata non plausibile: massimo {_MAX_VALUE[unit]} {_UNIT_LABEL[unit][1]}")
    elif value is not None:
        raise RetentionError("La durata non si indica per conservazione legata a un evento o illimitata")
    if unit == RetentionUnit.EVENT_BASED and not (fields.get("start_trigger") or "").strip():
        raise RetentionError("Per una conservazione legata a un evento indicare l'evento (start_trigger)")
    if unit == RetentionUnit.PERMANENT and fields.get("disposal_method") == DisposalMethod.DELETION:
        raise RetentionError("Con conservazione illimitata il metodo di fine ciclo non può essere la cancellazione")


class RetentionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(RetentionRule).where(RetentionRule.tenant_id == tenant_id, RetentionRule.deleted_at.is_(None))

    async def list_for_processing(self, tenant_id: str, processing_id: str) -> list[RetentionRule]:
        result = await self.db.execute(
            self._q(tenant_id)
            .where(RetentionRule.processing_activity_id == processing_id)
            .order_by(RetentionRule.created_at, RetentionRule.id)
        )
        return list(result.scalars().all())

    async def list_for_tenant(self, tenant_id: str) -> list[RetentionRule]:
        result = await self.db.execute(self._q(tenant_id).order_by(RetentionRule.created_at, RetentionRule.id))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, rule_id: str) -> RetentionRule | None:
        result = await self.db.execute(self._q(tenant_id).where(RetentionRule.id == rule_id))
        return result.scalar_one_or_none()

    async def _check_category(self, tenant_id: str, processing_id: str, category_id: str | None) -> None:
        if not category_id:
            return
        exists = (
            await self.db.execute(
                select(DataCategoryCatalog.id).where(
                    DataCategoryCatalog.id == category_id,
                    or_(DataCategoryCatalog.tenant_id.is_(None), DataCategoryCatalog.tenant_id == tenant_id),
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise RetentionError("Categoria di dati non trovata")
        linked = (
            await self.db.execute(
                select(ProcessingDataCategory.id).where(
                    ProcessingDataCategory.processing_activity_id == processing_id,
                    ProcessingDataCategory.data_category_id == category_id,
                )
            )
        ).first()
        if linked is None:
            raise RetentionError("La categoria di dati non è associata al trattamento")

    async def _check_legal_reference(self, tenant_id: str, ref_id: str | None) -> None:
        if not ref_id:
            return
        from backend.services.privacypa_compliance_service import ComplianceError, LegalReferenceService

        try:
            await LegalReferenceService(self.db).require_all(tenant_id, [ref_id])
        except ComplianceError as e:
            raise RetentionError(str(e)) from None

    async def _touch_processing(self, tenant_id: str, processing_id: str, actor: str) -> None:
        activity = await ProcessingService(self.db).get_activity(tenant_id, processing_id)
        if activity is not None:
            activity.updated_by = actor
            activity.updated_at = datetime.utcnow()
            await self.db.flush()

    async def create(self, tenant_id: str, actor: str, processing_id: str, **fields) -> RetentionRule:
        data = {k: v for k, v in fields.items() if k in _EDITABLE}
        validate_rule(data)
        await self._check_category(tenant_id, processing_id, data.get("data_category_id"))
        await self._check_legal_reference(tenant_id, data.get("legal_reference_id"))
        rule = RetentionRule(
            tenant_id=tenant_id, processing_activity_id=processing_id, created_by=actor, updated_by=actor, **data
        )
        self.db.add(rule)
        await self.db.flush()
        await self._touch_processing(tenant_id, processing_id, actor)
        await self.audit.log(
            tenant_id, "retention_rule", "create", entity_id=rule.id, actor_user_id=actor,
            new_values=rule.to_dict(), metadata={"processing_activity_id": processing_id},
        )
        return rule

    async def update(self, tenant_id: str, rule_id: str, actor: str, **fields) -> RetentionRule:
        rule = await self.get(tenant_id, rule_id)
        if rule is None:
            raise RetentionError("Regola di conservazione non trovata")
        old = rule.to_dict()
        merged = {**{k: getattr(rule, k) for k in _EDITABLE}, **{k: v for k, v in fields.items() if k in _EDITABLE}}
        validate_rule(merged)
        await self._check_category(tenant_id, rule.processing_activity_id, merged.get("data_category_id"))
        await self._check_legal_reference(tenant_id, merged.get("legal_reference_id"))
        for key in _EDITABLE:
            setattr(rule, key, merged[key])
        rule.updated_by = actor
        rule.updated_at = datetime.utcnow()
        await self.db.flush()
        await self._touch_processing(tenant_id, rule.processing_activity_id, actor)
        await self.audit.log(
            tenant_id, "retention_rule", "update", entity_id=rule.id, actor_user_id=actor,
            old_values=old, new_values=rule.to_dict(), metadata={"processing_activity_id": rule.processing_activity_id},
        )
        return rule

    async def soft_delete(self, tenant_id: str, rule_id: str, actor: str) -> RetentionRule:
        rule = await self.get(tenant_id, rule_id)
        if rule is None:
            raise RetentionError("Regola di conservazione non trovata")
        rule.deleted_at = datetime.utcnow()
        rule.updated_by = actor
        await self.db.flush()
        await self._touch_processing(tenant_id, rule.processing_activity_id, actor)
        await self.audit.log(
            tenant_id, "retention_rule", "delete", entity_id=rule.id, actor_user_id=actor,
            old_values=rule.to_dict(), metadata={"processing_activity_id": rule.processing_activity_id},
        )
        return rule
