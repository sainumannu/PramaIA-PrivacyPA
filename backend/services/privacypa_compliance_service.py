"""
PrivacyPA — Servizi di: riferimenti normativi (§13), azioni correttive (§29), data breach (§26, §157),
richieste degli interessati (§27, §158) e audit strutturati (§28).

Le scadenze delle richieste si calcolano qui (§158) con parametri per ente. Il tempo trascorso da un data
breach è solo informativo: non stabilisce l'obbligo di notifica (§26.2).
"""
import re
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assessments import Dpia
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog
from backend.models.privacypa_compliance import (
    BREACH_LINK_TYPES,
    ActionStatus,
    AuditFinding,
    AuditFindingStatus,
    AuditStatus,
    BreachStatus,
    CorrectiveAction,
    CorrectiveSource,
    DataBreach,
    DataBreachLink,
    DataSubjectRequest,
    LegalReference,
    PrivacyAudit,
    RequestStatus,
)
from backend.models.privacypa_documents import Document
from backend.models.privacypa_organization import OrganizationalUnit
from backend.models.privacypa_parties import Party
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_risk import SecurityMeasure
from backend.models.privacypa_tasks import TaskPriority, TaskSourceType, TaskStatus
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_deadlines import DEFAULT_DEADLINES, add_months, get_deadlines  # noqa: F401
from backend.services.privacypa_task_service import TaskService

_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


class ComplianceError(ValueError):
    """Dati non validi o non coerenti con lo stato dell'entità (400)."""


class ComplianceNotFound(LookupError):
    """Entità non trovata nell'ente (404)."""


class _Base:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def _exists(self, model, tenant_id: str, entity_id: str | None, *, tenant_col: bool = True, soft: bool = True) -> bool:
        if not entity_id:
            return False
        stmt = select(model.id).where(model.id == entity_id)
        if tenant_col:
            stmt = stmt.where(model.tenant_id == tenant_id)
        if soft and hasattr(model, "deleted_at"):
            stmt = stmt.where(model.deleted_at.is_(None))
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def _check_unit(self, tenant_id: str, unit_id: str | None) -> None:
        if unit_id and not await self._exists(OrganizationalUnit, tenant_id, unit_id, soft=False):
            raise ComplianceError("Struttura organizzativa non trovata")

    async def _check_document(self, tenant_id: str, document_id: str | None) -> None:
        if document_id and not await self._exists(Document, tenant_id, document_id):
            raise ComplianceError("Documento non trovato")


# ---------------------------------------------------------------- riferimenti normativi

class LegalReferenceService(_Base):
    _FIELDS = {"source_type", "title", "number", "year", "article", "url", "notes"}

    def _q(self, tenant_id: str):
        return select(LegalReference).where(LegalReference.tenant_id == tenant_id, LegalReference.deleted_at.is_(None))

    async def search(self, tenant_id: str, search: str | None = None) -> list[LegalReference]:
        stmt = self._q(tenant_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(LegalReference.title.ilike(like) | LegalReference.article.ilike(like) | LegalReference.number.ilike(like))
        return list((await self.db.execute(stmt.order_by(LegalReference.title, LegalReference.year))).scalars().all())

    async def get(self, tenant_id: str, ref_id: str) -> LegalReference | None:
        return (await self.db.execute(self._q(tenant_id).where(LegalReference.id == ref_id))).scalar_one_or_none()

    @staticmethod
    def _clean(data: dict) -> dict:
        if not (data.get("title") or "").strip():
            raise ComplianceError("Il titolo è obbligatorio")
        data["title"] = data["title"].strip()
        year = data.get("year")
        if year is not None and not 1800 <= year <= date.today().year + 1:
            raise ComplianceError("Anno non valido")
        url = (data.get("url") or "").strip() or None
        if url and not _URL.match(url):
            raise ComplianceError("L'indirizzo deve iniziare con http:// o https://")
        data["url"] = url
        return data

    async def _duplicate(self, tenant_id: str, data: dict, exclude: str | None = None) -> bool:
        stmt = self._q(tenant_id).where(
            LegalReference.title == data["title"], LegalReference.number.is_(data.get("number")) if data.get("number") is None else LegalReference.number == data["number"],
            LegalReference.year.is_(None) if data.get("year") is None else LegalReference.year == data["year"],
            LegalReference.article.is_(None) if not data.get("article") else LegalReference.article == data["article"],
        )
        if exclude:
            stmt = stmt.where(LegalReference.id != exclude)
        return (await self.db.execute(stmt)).first() is not None

    async def create(self, tenant_id: str, actor: str, **fields) -> LegalReference:
        data = self._clean({k: v for k, v in fields.items() if k in self._FIELDS})
        if await self._duplicate(tenant_id, data):
            raise ComplianceError("Riferimento normativo già presente")
        ref = LegalReference(tenant_id=tenant_id, **data)
        self.db.add(ref)
        await self.db.flush()
        await self.audit.log(tenant_id, "legal_reference", "create", entity_id=ref.id, actor_user_id=actor, new_values=ref.to_dict())
        return ref

    async def update(self, tenant_id: str, ref_id: str, actor: str, **fields) -> LegalReference:
        ref = await self.get(tenant_id, ref_id)
        if ref is None:
            raise ComplianceNotFound("Riferimento normativo non trovato")
        old = ref.to_dict()
        data = self._clean({k: fields[k] for k in self._FIELDS if k in fields})
        if await self._duplicate(tenant_id, data, exclude=ref.id):
            raise ComplianceError("Riferimento normativo già presente")
        for key, value in data.items():
            setattr(ref, key, value)
        ref.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "legal_reference", "update", entity_id=ref.id, actor_user_id=actor, old_values=old, new_values=ref.to_dict())
        return ref

    async def list_links(self, processing_id: str) -> list:
        from backend.models.privacypa_compliance import ProcessingLegalReference

        return list((await self.db.execute(select(ProcessingLegalReference).where(
            ProcessingLegalReference.processing_activity_id == processing_id))).scalars().all())

    async def set_links(self, processing_id: str, ids: list[str]) -> None:
        from backend.models.privacypa_compliance import ProcessingLegalReference

        for row in await self.list_links(processing_id):
            await self.db.delete(row)
        await self.db.flush()
        for ref_id in dict.fromkeys(ids):
            self.db.add(ProcessingLegalReference(processing_activity_id=processing_id, legal_reference_id=ref_id))
        await self.db.flush()

    async def in_use(self, ref_id: str) -> bool:
        from backend.models.privacypa_compliance import ProcessingLegalReference
        from backend.models.privacypa_retention import RetentionRule

        used = (await self.db.execute(select(ProcessingLegalReference.processing_activity_id).where(
            ProcessingLegalReference.legal_reference_id == ref_id))).first()
        retention = (await self.db.execute(select(RetentionRule.id).where(
            RetentionRule.legal_reference_id == ref_id, RetentionRule.deleted_at.is_(None)))).first()
        return bool(used or retention)

    async def soft_delete(self, tenant_id: str, ref_id: str, actor: str) -> None:
        ref = await self.get(tenant_id, ref_id)
        if ref is None:
            raise ComplianceNotFound("Riferimento normativo non trovato")
        if await self.in_use(ref.id):
            raise ComplianceError("Il riferimento è usato da trattamenti o regole di conservazione: scollegarlo prima di eliminarlo")
        ref.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "legal_reference", "delete", entity_id=ref.id, actor_user_id=actor, old_values=ref.to_dict())

    async def require_all(self, tenant_id: str, ids: list[str]) -> None:
        found = set((await self.db.execute(select(LegalReference.id).where(
            LegalReference.tenant_id == tenant_id, LegalReference.deleted_at.is_(None), LegalReference.id.in_(ids)))).scalars().all())
        if found != set(ids):
            raise ComplianceError("Riferimento normativo non trovato")


# ---------------------------------------------------------------- azioni correttive

_ACTION_SOURCE_MODEL = {
    CorrectiveSource.AUDIT: PrivacyAudit, CorrectiveSource.BREACH: DataBreach, CorrectiveSource.DPIA: Dpia,
    CorrectiveSource.PROCESSING_REVIEW: ProcessingActivity, CorrectiveSource.PROCESSOR_ASSESSMENT: Party,
}
_ACTION_SOURCE_REQUIRED = (CorrectiveSource.AUDIT, CorrectiveSource.BREACH, CorrectiveSource.DPIA)
_ACTION_FIELDS = {
    "source_type", "source_id", "title", "description", "priority", "owner_user_id", "organizational_unit_id",
    "due_date", "evidence_document_id",
}
_ACTION_TRANSITIONS = {
    ActionStatus.OPEN: {ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED, ActionStatus.CANCELLED},
    ActionStatus.IN_PROGRESS: {ActionStatus.OPEN, ActionStatus.COMPLETED, ActionStatus.CANCELLED},
    ActionStatus.COMPLETED: {ActionStatus.IN_PROGRESS},
    ActionStatus.CANCELLED: {ActionStatus.OPEN},
}


class CorrectiveActionService(_Base):
    def _q(self, tenant_id: str):
        return select(CorrectiveAction).where(CorrectiveAction.tenant_id == tenant_id, CorrectiveAction.deleted_at.is_(None))

    async def search(
        self, tenant_id: str, *, status: ActionStatus | None = None, source_type: CorrectiveSource | None = None,
        source_id: str | None = None, owner: str | None = None,
    ) -> list[CorrectiveAction]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(CorrectiveAction.status == status)
        if source_type:
            stmt = stmt.where(CorrectiveAction.source_type == source_type)
        if source_id:
            stmt = stmt.where(CorrectiveAction.source_id == source_id)
        if owner:
            stmt = stmt.where(CorrectiveAction.owner_user_id == owner)
        return list((await self.db.execute(stmt.order_by(CorrectiveAction.due_date, CorrectiveAction.code))).scalars().all())

    async def get(self, tenant_id: str, action_id: str) -> CorrectiveAction | None:
        return (await self.db.execute(self._q(tenant_id).where(CorrectiveAction.id == action_id))).scalar_one_or_none()

    async def _source_unit(self, tenant_id: str, source_type: CorrectiveSource, source_id: str | None) -> str | None:
        model = _ACTION_SOURCE_MODEL.get(source_type)
        if source_type in _ACTION_SOURCE_REQUIRED and not source_id:
            raise ComplianceError(f"La sorgente {source_type.value} richiede l'elemento di origine")
        if not source_id:
            return None
        if model is None:
            raise ComplianceError(f"La sorgente {source_type.value} non ha un elemento di origine")
        row = (await self.db.execute(select(model).where(model.id == source_id, model.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None or getattr(row, "deleted_at", None) is not None:
            raise ComplianceError("Elemento di origine non trovato")
        return getattr(row, "organizational_unit_id", None)

    async def _validate(self, tenant_id: str, data: dict, *, creating: bool) -> dict:
        if not (data.get("title") or "").strip() or not (data.get("description") or "").strip():
            raise ComplianceError("Titolo e descrizione sono obbligatori")
        if not (data.get("owner_user_id") or "").strip():
            raise ComplianceError("Serve un responsabile dell'azione")
        if creating and data["due_date"] < date.today():
            raise ComplianceError("La scadenza non può essere nel passato")
        source_unit = await self._source_unit(tenant_id, data["source_type"], data.get("source_id"))
        data["organizational_unit_id"] = data.get("organizational_unit_id") or source_unit
        await self._check_unit(tenant_id, data["organizational_unit_id"])
        await self._check_document(tenant_id, data.get("evidence_document_id"))
        return data

    async def create(self, tenant_id: str, actor: str, **fields) -> CorrectiveAction:
        data = await self._validate(tenant_id, {k: v for k, v in fields.items() if k in _ACTION_FIELDS}, creating=True)
        action = CorrectiveAction(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, CorrectiveAction, tenant_id, "CA"),
            created_by=actor, **data,
        )
        self.db.add(action)
        await self.db.flush()
        await self.audit.log(tenant_id, "corrective_action", "create", entity_id=action.id, actor_user_id=actor,
                             new_values={"code": action.code, "status": action.status.value, "due_date": action.due_date.isoformat()})
        return action

    async def update(self, tenant_id: str, action_id: str, actor: str, **fields) -> CorrectiveAction:
        action = await self.get(tenant_id, action_id)
        if action is None:
            raise ComplianceNotFound("Azione correttiva non trovata")
        if action.status in (ActionStatus.COMPLETED, ActionStatus.CANCELLED):
            raise ComplianceError("L'azione è chiusa: riaprirla per modificarla")
        merged = {k: getattr(action, k) for k in _ACTION_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _ACTION_FIELDS})
        data = await self._validate(tenant_id, merged, creating=False)
        old = {"due_date": action.due_date.isoformat(), "status": action.status.value}
        for key, value in data.items():
            setattr(action, key, value)
        action.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "corrective_action", "update", entity_id=action.id, actor_user_id=actor,
                             old_values=old, new_values={"due_date": action.due_date.isoformat(), "status": action.status.value})
        return action

    async def change_status(
        self, tenant_id: str, action_id: str, actor: str, target: ActionStatus, notes: str | None = None,
        evidence_document_id: str | None = None,
    ) -> CorrectiveAction:
        action = await self.get(tenant_id, action_id)
        if action is None:
            raise ComplianceNotFound("Azione correttiva non trovata")
        if target not in _ACTION_TRANSITIONS[action.status]:
            raise ComplianceError(f"Transizione non ammessa: {action.status.value} → {target.value}")
        notes = (notes or "").strip() or None
        if target in (ActionStatus.COMPLETED, ActionStatus.CANCELLED):
            if not notes:
                raise ComplianceError("Per chiudere l'azione serve una nota di chiusura")
            await self._check_document(tenant_id, evidence_document_id)
            action.closure_notes, action.closed_at = notes, datetime.utcnow()
            if evidence_document_id:
                action.evidence_document_id = evidence_document_id
        else:
            action.closed_at = None
        old = action.status
        action.status, action.updated_at = target, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "corrective_action", "status_change", event_type=f"corrective_action.status.{target.value.lower()}",
                             entity_id=action.id, actor_user_id=actor, old_values={"status": old.value},
                             new_values={"status": target.value, "due_date": action.due_date.isoformat()})
        return action

    async def soft_delete(self, tenant_id: str, action_id: str, actor: str) -> None:
        action = await self.get(tenant_id, action_id)
        if action is None:
            raise ComplianceNotFound("Azione correttiva non trovata")
        action.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "corrective_action", "delete", entity_id=action.id, actor_user_id=actor,
                             old_values={"code": action.code})


# ---------------------------------------------------------------- data breach

_BREACH_FIELDS = {
    "title", "organizational_unit_id", "detected_at", "reported_by", "description", "confidentiality_affected",
    "integrity_affected", "availability_affected", "approx_data_subjects", "approx_records", "confidentiality_level",
}
DECISION_FIELDS = (
    "risk_level", "risk_rationale", "notify_authority", "notify_authority_rationale", "authority_notified_at",
    "notify_subjects", "notify_subjects_rationale", "subjects_notified_at",
)
_BREACH_TRANSITIONS = {
    BreachStatus.OPEN: {BreachStatus.ASSESSING, BreachStatus.CONTAINED, BreachStatus.CLOSED},
    BreachStatus.ASSESSING: {BreachStatus.OPEN, BreachStatus.CONTAINED, BreachStatus.CLOSED},
    BreachStatus.CONTAINED: {BreachStatus.ASSESSING, BreachStatus.CLOSED},
    BreachStatus.CLOSED: {BreachStatus.ASSESSING},
}


class BreachService(_Base):
    def _q(self, tenant_id: str):
        return select(DataBreach).where(DataBreach.tenant_id == tenant_id, DataBreach.deleted_at.is_(None))

    async def search(self, tenant_id: str, status: BreachStatus | None = None) -> list[DataBreach]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(DataBreach.status == status)
        return list((await self.db.execute(stmt.order_by(DataBreach.aware_at.desc(), DataBreach.code))).scalars().all())

    async def get(self, tenant_id: str, breach_id: str) -> DataBreach | None:
        return (await self.db.execute(self._q(tenant_id).where(DataBreach.id == breach_id))).scalar_one_or_none()

    @staticmethod
    def _summary(b: DataBreach) -> dict:
        """Valori tracciati in audit e outbox: nessuna descrizione libera (può contenere dati personali)."""
        return {
            "code": b.code, "status": b.status.value, "aware_at": b.aware_at.isoformat(), "confidentiality_level": b.confidentiality_level.value,
            "risk_level": b.risk_level.value if b.risk_level else None,
            "notify_authority": b.notify_authority, "notify_subjects": b.notify_subjects,
        }

    @staticmethod
    def _not_future(moment: datetime | None, label: str) -> None:
        if moment and moment > datetime.utcnow() + timedelta(minutes=5):
            raise ComplianceError(f"{label} non può essere nel futuro")

    @staticmethod
    def check_decisions(b: DataBreach) -> None:
        """Coerenza delle valutazioni: ogni decisione negativa va motivata, ogni notifica presuppone la decisione."""
        if b.risk_level is not None and not (b.risk_rationale or "").strip():
            raise ComplianceError("La valutazione del rischio richiede una motivazione")
        if b.notify_authority is False and not (b.notify_authority_rationale or "").strip():
            raise ComplianceError("La scelta di non notificare all'Autorità richiede una motivazione")
        if b.authority_notified_at and b.notify_authority is not True:
            raise ComplianceError("La data di notifica all'Autorità presuppone la decisione di notificare")
        if b.notify_subjects is False and not (b.notify_subjects_rationale or "").strip():
            raise ComplianceError("La scelta di non comunicare agli interessati richiede una motivazione")
        if b.subjects_notified_at and b.notify_subjects is not True:
            raise ComplianceError("La data di comunicazione agli interessati presuppone la decisione di comunicare")
        for value, label in ((b.authority_notified_at, "La notifica all'Autorità"), (b.subjects_notified_at, "La comunicazione agli interessati")):
            BreachService._not_future(value, label)
            if value and value < b.aware_at - timedelta(minutes=5):
                raise ComplianceError(f"{label} non può precedere il momento di conoscenza")

    async def create(self, tenant_id: str, actor: str, aware_at: datetime, **fields) -> DataBreach:
        data = {k: v for k, v in fields.items() if k in _BREACH_FIELDS or k in DECISION_FIELDS}
        if not (data.get("title") or "").strip() or not (data.get("description") or "").strip():
            raise ComplianceError("Titolo e descrizione sono obbligatori")
        self._not_future(aware_at, "Il momento di conoscenza")
        await self._check_unit(tenant_id, data.get("organizational_unit_id"))
        for key in ("approx_data_subjects", "approx_records"):
            if data.get(key) is not None and data[key] < 0:
                raise ComplianceError("I conteggi non possono essere negativi")
        breach = DataBreach(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, DataBreach, tenant_id, "BR"),
            aware_at=aware_at, created_by=actor, links=[], **data,
        )
        self.check_decisions(breach)
        self.db.add(breach)
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "create", entity_id=breach.id, actor_user_id=actor, new_values=self._summary(breach))
        return breach

    async def update(self, tenant_id: str, breach_id: str, actor: str, **fields) -> DataBreach:
        breach = await self.get(tenant_id, breach_id)
        if breach is None:
            raise ComplianceNotFound("Violazione non trovata")
        if breach.status == BreachStatus.CLOSED:
            raise ComplianceError("La violazione è chiusa: riaprirla per modificarla")
        old = self._summary(breach)
        if "organizational_unit_id" in fields:
            await self._check_unit(tenant_id, fields["organizational_unit_id"])
        for key, value in fields.items():
            if key in _BREACH_FIELDS or key in DECISION_FIELDS:
                setattr(breach, key, value)
        if not (breach.title or "").strip() or not (breach.description or "").strip():
            raise ComplianceError("Titolo e descrizione sono obbligatori")
        self.check_decisions(breach)
        breach.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "update", entity_id=breach.id, actor_user_id=actor,
                             old_values=old, new_values=self._summary(breach))
        return breach

    async def correct_aware_at(self, tenant_id: str, breach_id: str, actor: str, aware_at: datetime, reason: str) -> DataBreach:
        """Correzione motivata del momento di conoscenza (§157): l'originale si conserva e l'audit registra il cambio."""
        breach = await self.get(tenant_id, breach_id)
        if breach is None:
            raise ComplianceNotFound("Violazione non trovata")
        if not (reason or "").strip():
            raise ComplianceError("La correzione richiede una motivazione")
        self._not_future(aware_at, "Il momento di conoscenza")
        if aware_at == breach.aware_at:
            raise ComplianceError("Il momento di conoscenza indicato coincide con quello registrato")
        old = self._summary(breach)
        if breach.aware_at_original is None:
            breach.aware_at_original = breach.aware_at
        breach.aware_at = aware_at
        breach.aware_at_correction_reason = reason.strip()
        breach.aware_at_corrected_by, breach.aware_at_corrected_at = actor, datetime.utcnow()
        breach.updated_at = datetime.utcnow()
        self.check_decisions(breach)
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "correct_aware_at", event_type="data_breach.aware_at_corrected",
                             entity_id=breach.id, actor_user_id=actor, old_values=old,
                             new_values={**self._summary(breach), "reason": breach.aware_at_correction_reason})
        return breach

    async def transition(self, tenant_id: str, breach_id: str, actor: str, target: BreachStatus) -> DataBreach:
        breach = await self.get(tenant_id, breach_id)
        if breach is None:
            raise ComplianceNotFound("Violazione non trovata")
        if target not in _BREACH_TRANSITIONS[breach.status]:
            raise ComplianceError(f"Transizione non ammessa: {breach.status.value} → {target.value}")
        if target == BreachStatus.CLOSED:
            if breach.risk_level is None or breach.notify_authority is None:
                raise ComplianceError(
                    "Per chiudere serve la valutazione del rischio e la decisione sulla notifica all'Autorità "
                    "(anche negativa, motivata): ogni violazione va documentata (§26.4)"
                )
            breach.closed_at = datetime.utcnow()
        else:
            breach.closed_at = None
        old = breach.status
        breach.status, breach.updated_at = target, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "status_change", event_type=f"data_breach.status.{target.value.lower()}",
                             entity_id=breach.id, actor_user_id=actor, old_values={"status": old.value},
                             new_values=self._summary(breach))
        return breach

    _LINK_MODELS = {
        "processing_activity": (ProcessingActivity, True), "asset": (Asset, True), "party": (Party, True),
        "document": (Document, True), "corrective_action": (CorrectiveAction, True),
    }

    async def _link_exists(self, tenant_id: str, kind: str, entity_id: str) -> bool:
        if kind in self._LINK_MODELS:
            return await self._exists(self._LINK_MODELS[kind][0], tenant_id, entity_id)
        model = {"data_category": DataCategoryCatalog, "data_subject_category": DataSubjectCategoryCatalog, "security_measure": SecurityMeasure}[kind]
        stmt = select(model.id).where(model.id == entity_id, or_(model.tenant_id.is_(None), model.tenant_id == tenant_id))
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def set_links(self, tenant_id: str, breach_id: str, actor: str, links: list[dict]) -> DataBreach:
        breach = await self.get(tenant_id, breach_id)
        if breach is None:
            raise ComplianceNotFound("Violazione non trovata")
        wanted = list(dict.fromkeys((l["entity_type"], l["entity_id"]) for l in links))
        for kind, entity_id in wanted:
            if kind not in BREACH_LINK_TYPES:
                raise ComplianceError(f"Tipo di collegamento non ammesso: {kind}")
            if not await self._link_exists(tenant_id, kind, entity_id):
                raise ComplianceError(f"Elemento collegato non trovato ({kind})")
        before = sorted((l.entity_type, l.entity_id) for l in breach.links)
        if before == sorted(wanted):
            return breach
        breach.links = [DataBreachLink(data_breach_id=breach.id, entity_type=k, entity_id=i) for k, i in wanted]
        breach.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "links_update", event_type="data_breach.links.updated", entity_id=breach.id,
                             actor_user_id=actor, old_values={"links": len(before)}, new_values={"links": len(wanted)})
        return breach

    async def soft_delete(self, tenant_id: str, breach_id: str, actor: str) -> None:
        breach = await self.get(tenant_id, breach_id)
        if breach is None:
            raise ComplianceNotFound("Violazione non trovata")
        if breach.status != BreachStatus.OPEN or breach.risk_level is not None or breach.notify_authority is not None:
            raise ComplianceError("Un registro di violazione con valutazioni o già trattato non si elimina: va chiuso e conservato (§26.4)")
        breach.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_breach", "delete", entity_id=breach.id, actor_user_id=actor, old_values=self._summary(breach))


# ---------------------------------------------------------------- richieste degli interessati

_REQUEST_FIELDS = {
    "organizational_unit_id", "request_type", "received_at", "channel", "identity_verified",
    "identity_verification_notes", "requester_reference",
}
_REQUEST_TRANSITIONS = {
    RequestStatus.RECEIVED: {RequestStatus.IN_PROGRESS},
    RequestStatus.IN_PROGRESS: {RequestStatus.RESPONDED, RequestStatus.RECEIVED},
    RequestStatus.RESPONDED: {RequestStatus.CLOSED, RequestStatus.IN_PROGRESS},
    RequestStatus.CLOSED: set(),
}


class RequestService(_Base):
    def _q(self, tenant_id: str):
        return select(DataSubjectRequest).where(DataSubjectRequest.tenant_id == tenant_id, DataSubjectRequest.deleted_at.is_(None))

    async def search(self, tenant_id: str, status: RequestStatus | None = None) -> list[DataSubjectRequest]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(DataSubjectRequest.status == status)
        return list((await self.db.execute(stmt.order_by(DataSubjectRequest.due_at, DataSubjectRequest.code))).scalars().all())

    async def get(self, tenant_id: str, request_id: str) -> DataSubjectRequest | None:
        return (await self.db.execute(self._q(tenant_id).where(DataSubjectRequest.id == request_id))).scalar_one_or_none()

    @staticmethod
    def _summary(r: DataSubjectRequest) -> dict:
        """Senza riferimento del richiedente né note di verifica dell'identità (§64)."""
        return {"code": r.code, "status": r.status.value, "request_type": r.request_type.value, "due_at": r.due_at.isoformat()}

    async def create(self, tenant_id: str, actor: str, received_at: datetime, **fields) -> DataSubjectRequest:
        data = {k: v for k, v in fields.items() if k in _REQUEST_FIELDS}
        if received_at > datetime.utcnow() + timedelta(minutes=5):
            raise ComplianceError("La data di ricezione non può essere nel futuro")
        await self._check_unit(tenant_id, data.get("organizational_unit_id"))
        deadlines = await get_deadlines(self.db, tenant_id)
        req = DataSubjectRequest(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, DataSubjectRequest, tenant_id, "RI"),
            received_at=received_at, due_at=add_months(received_at, deadlines["dsr_base_months"]), created_by=actor, **data,
        )
        self.db.add(req)
        await self.db.flush()
        await self.audit.log(tenant_id, "data_subject_request", "create", entity_id=req.id, actor_user_id=actor, new_values=self._summary(req))
        return req

    async def update(self, tenant_id: str, request_id: str, actor: str, **fields) -> DataSubjectRequest:
        req = await self.get(tenant_id, request_id)
        if req is None:
            raise ComplianceNotFound("Richiesta non trovata")
        if req.status == RequestStatus.CLOSED:
            raise ComplianceError("La richiesta è chiusa")
        old = self._summary(req)
        # Tutte le letture prima di modificare l'oggetto: una query in mezzo scatenerebbe un flush (e un secondo UPDATE).
        if "organizational_unit_id" in fields:
            await self._check_unit(tenant_id, fields["organizational_unit_id"])
        new_received = fields.get("received_at")
        new_due = None
        if new_received and new_received != req.received_at:
            if req.extension_applied:
                raise ComplianceError("Con una proroga applicata la data di ricezione non si modifica")
            new_due = add_months(new_received, (await get_deadlines(self.db, tenant_id))["dsr_base_months"])
        if new_due is not None:
            req.due_at = new_due
        for key, value in fields.items():
            if key in _REQUEST_FIELDS:
                setattr(req, key, value)
        req.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_subject_request", "update", entity_id=req.id, actor_user_id=actor,
                             old_values=old, new_values=self._summary(req))
        return req

    async def apply_extension(
        self, tenant_id: str, request_id: str, actor: str, reason: str, communicated_at: datetime | None
    ) -> DataSubjectRequest:
        """Proroga del termine (art. 12, par. 3): una sola volta, motivata e comunicata al richiedente."""
        req = await self.get(tenant_id, request_id)
        if req is None:
            raise ComplianceNotFound("Richiesta non trovata")
        if req.extension_applied:
            raise ComplianceError("La proroga è già stata applicata")
        if req.status in (RequestStatus.RESPONDED, RequestStatus.CLOSED):
            raise ComplianceError("La richiesta ha già avuto risposta")
        if not (reason or "").strip():
            raise ComplianceError("La proroga richiede una motivazione")
        if communicated_at and communicated_at < req.received_at:
            raise ComplianceError("La comunicazione della proroga non può precedere la ricezione")
        deadlines = await get_deadlines(self.db, tenant_id)
        old = self._summary(req)
        req.due_at = add_months(req.received_at, deadlines["dsr_base_months"] + deadlines["dsr_extension_months"])
        req.extension_applied, req.extension_reason = True, reason.strip()
        req.extension_communicated_at = communicated_at
        req.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_subject_request", "extension", event_type="data_subject_request.extension_applied",
                             entity_id=req.id, actor_user_id=actor, old_values=old,
                             new_values={**self._summary(req), "communicated": communicated_at is not None})
        return req

    async def transition(
        self, tenant_id: str, request_id: str, actor: str, target: RequestStatus, *, outcome: str | None = None,
        response_sent_at: datetime | None = None,
    ) -> DataSubjectRequest:
        req = await self.get(tenant_id, request_id)
        if req is None:
            raise ComplianceNotFound("Richiesta non trovata")
        if target not in _REQUEST_TRANSITIONS[req.status]:
            raise ComplianceError(f"Transizione non ammessa: {req.status.value} → {target.value}")
        if target == RequestStatus.RESPONDED:
            if not req.identity_verified:
                raise ComplianceError("Prima di rispondere va verificata l'identità del richiedente")
            if not (outcome or "").strip():
                raise ComplianceError("La risposta richiede l'esito")
            sent = response_sent_at or datetime.utcnow()
            if sent < req.received_at:
                raise ComplianceError("La risposta non può precedere la ricezione")
            req.outcome, req.response_sent_at = outcome.strip(), sent
        old = req.status
        req.status, req.updated_at = target, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_subject_request", "status_change",
                             event_type=f"data_subject_request.status.{target.value.lower()}", entity_id=req.id, actor_user_id=actor,
                             old_values={"status": old.value}, new_values=self._summary(req))
        return req

    async def create_office_tasks(
        self, tenant_id: str, request_id: str, actor: str, unit_ids: list[str], notes: str | None = None
    ) -> list:
        """Un task per ufficio coinvolto, con scadenza ridotta di 3 giorni rispetto al termine; senza dati del richiedente."""
        req = await self.get(tenant_id, request_id)
        if req is None:
            raise ComplianceNotFound("Richiesta non trovata")
        if req.status in (RequestStatus.RESPONDED, RequestStatus.CLOSED):
            raise ComplianceError("La richiesta ha già avuto risposta")
        units = list(dict.fromkeys(unit_ids))
        found = {u.id: u for u in (await self.db.execute(select(OrganizationalUnit).where(
            OrganizationalUnit.tenant_id == tenant_id, OrganizationalUnit.id.in_(units)))).scalars().all()} if units else {}
        if not units or set(found) != set(units):
            raise ComplianceError("Indicare strutture esistenti dell'ente")
        due = max(req.due_at - timedelta(days=3), datetime.utcnow() + timedelta(hours=1))
        tasks = TaskService(self.db)
        created = []
        for unit_id in units:
            created.append(await tasks.create(
                tenant_id, actor, f"Richiesta {req.code} ({req.request_type.value}): contributo di {found[unit_id].name}",
                description=(notes or "").strip() or f"Contributo per la richiesta {req.code} entro il termine di risposta.",
                source_type=TaskSourceType.REQUEST, source_id=req.id, assigned_unit_id=unit_id,
                priority=TaskPriority.HIGH, status=TaskStatus.OPEN, due_at=due,
            ))
        await self.audit.log(tenant_id, "data_subject_request", "tasks_created", event_type="data_subject_request.tasks_created",
                             entity_id=req.id, actor_user_id=actor, new_values={"tasks": len(created)})
        return created

    async def soft_delete(self, tenant_id: str, request_id: str, actor: str) -> None:
        req = await self.get(tenant_id, request_id)
        if req is None:
            raise ComplianceNotFound("Richiesta non trovata")
        if req.status != RequestStatus.RECEIVED:
            raise ComplianceError("Una richiesta già trattata non si elimina: va chiusa e conservata")
        req.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "data_subject_request", "delete", entity_id=req.id, actor_user_id=actor, old_values=self._summary(req))


# ---------------------------------------------------------------- audit strutturati

_AUDIT_FIELDS = {"title", "audit_type", "organizational_unit_id", "planned_date", "lead_auditor_user_id"}
_AUDIT_TRANSITIONS = {
    AuditStatus.PLANNED: {AuditStatus.IN_PROGRESS, AuditStatus.CANCELLED},
    AuditStatus.IN_PROGRESS: {AuditStatus.COMPLETED, AuditStatus.CANCELLED},
    AuditStatus.COMPLETED: set(),
    AuditStatus.CANCELLED: {AuditStatus.PLANNED},
}
_SEVERITIES = ("HIGH", "MEDIUM", "LOW")


class AuditPlanService(_Base):
    def _q(self, tenant_id: str):
        return select(PrivacyAudit).where(PrivacyAudit.tenant_id == tenant_id, PrivacyAudit.deleted_at.is_(None))

    async def search(self, tenant_id: str, status: AuditStatus | None = None) -> list[PrivacyAudit]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(PrivacyAudit.status == status)
        return list((await self.db.execute(stmt.order_by(PrivacyAudit.planned_date.desc(), PrivacyAudit.code))).scalars().all())

    async def get(self, tenant_id: str, audit_id: str) -> PrivacyAudit | None:
        return (await self.db.execute(self._q(tenant_id).where(PrivacyAudit.id == audit_id))).scalar_one_or_none()

    async def create(self, tenant_id: str, actor: str, **fields) -> PrivacyAudit:
        data = {k: v for k, v in fields.items() if k in _AUDIT_FIELDS}
        if not (data.get("title") or "").strip() or not (data.get("lead_auditor_user_id") or "").strip():
            raise ComplianceError("Titolo e responsabile dell'audit sono obbligatori")
        await self._check_unit(tenant_id, data.get("organizational_unit_id"))
        audit = PrivacyAudit(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, PrivacyAudit, tenant_id, "AU"),
            created_by=actor, findings=[], **data,
        )
        self.db.add(audit)
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "create", entity_id=audit.id, actor_user_id=actor,
                             new_values={"code": audit.code, "status": audit.status.value})
        return audit

    async def update(self, tenant_id: str, audit_id: str, actor: str, **fields) -> PrivacyAudit:
        audit = await self.get(tenant_id, audit_id)
        if audit is None:
            raise ComplianceNotFound("Audit non trovato")
        if audit.status in (AuditStatus.COMPLETED, AuditStatus.CANCELLED):
            raise ComplianceError("L'audit è chiuso e non si modifica")
        await self._check_unit(tenant_id, fields.get("organizational_unit_id"))
        for key, value in fields.items():
            if key in _AUDIT_FIELDS:
                setattr(audit, key, value)
        audit.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "update", entity_id=audit.id, actor_user_id=actor, new_values={"code": audit.code})
        return audit

    async def transition(
        self, tenant_id: str, audit_id: str, actor: str, target: AuditStatus, *, performed_date: date | None = None,
        summary: str | None = None,
    ) -> PrivacyAudit:
        audit = await self.get(tenant_id, audit_id)
        if audit is None:
            raise ComplianceNotFound("Audit non trovato")
        if target not in _AUDIT_TRANSITIONS[audit.status]:
            raise ComplianceError(f"Transizione non ammessa: {audit.status.value} → {target.value}")
        if target == AuditStatus.COMPLETED:
            performed = performed_date or audit.performed_date
            if performed is None or not (summary or audit.summary or "").strip():
                raise ComplianceError("Per completare l'audit servono la data di esecuzione e la sintesi")
            if performed > date.today():
                raise ComplianceError("La data di esecuzione non può essere nel futuro")
            audit.performed_date, audit.summary = performed, (summary or audit.summary).strip()
        old = audit.status
        audit.status, audit.updated_at = target, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "status_change", event_type=f"audit_plan.status.{target.value.lower()}",
                             entity_id=audit.id, actor_user_id=actor, old_values={"status": old.value},
                             new_values={"status": target.value})
        return audit

    async def add_finding(self, tenant_id: str, audit_id: str, actor: str, **fields) -> AuditFinding:
        audit = await self.get(tenant_id, audit_id)
        if audit is None:
            raise ComplianceNotFound("Audit non trovato")
        if audit.status not in (AuditStatus.PLANNED, AuditStatus.IN_PROGRESS):
            raise ComplianceError("I rilievi si registrano su un audit pianificato o in corso")
        if fields.get("severity") not in _SEVERITIES:
            raise ComplianceError("Gravità non valida")
        if not (fields.get("title") or "").strip() or not (fields.get("description") or "").strip():
            raise ComplianceError("Titolo e descrizione del rilievo sono obbligatori")
        finding = AuditFinding(audit_id=audit.id, severity=fields["severity"], title=fields["title"].strip(),
                               description=fields["description"].strip(), evidence=fields.get("evidence"))
        audit.findings.append(finding)
        audit.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "finding_add", event_type="audit_plan.finding.added", entity_id=audit.id,
                             actor_user_id=actor, new_values={"severity": finding.severity})
        return finding

    async def set_finding_status(
        self, tenant_id: str, audit_id: str, finding_id: str, actor: str, status: AuditFindingStatus
    ) -> AuditFinding:
        audit = await self.get(tenant_id, audit_id)
        finding = next((f for f in audit.findings if f.id == finding_id), None) if audit else None
        if finding is None:
            raise ComplianceNotFound("Rilievo non trovato")
        finding.status = status
        audit.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "finding_status", event_type="audit_plan.finding.status", entity_id=audit.id,
                             actor_user_id=actor, new_values={"status": status.value})
        return finding

    async def create_action_from_finding(
        self, tenant_id: str, audit_id: str, finding_id: str, actor: str, *, owner_user_id: str, due_date: date,
        priority,
    ) -> CorrectiveAction:
        audit = await self.get(tenant_id, audit_id)
        finding = next((f for f in audit.findings if f.id == finding_id), None) if audit else None
        if finding is None:
            raise ComplianceNotFound("Rilievo non trovato")
        if finding.status == AuditFindingStatus.CLOSED:
            raise ComplianceError("Il rilievo è chiuso")
        action = await CorrectiveActionService(self.db).create(
            tenant_id, actor, source_type=CorrectiveSource.AUDIT, source_id=audit.id, title=finding.title,
            description=f"Rilievo dell'audit {audit.code} ({finding.severity}): {finding.description}",
            priority=priority, owner_user_id=owner_user_id, due_date=due_date,
        )
        finding.status = AuditFindingStatus.ACTION_PLANNED
        await self.db.flush()
        return action

    async def soft_delete(self, tenant_id: str, audit_id: str, actor: str) -> None:
        audit = await self.get(tenant_id, audit_id)
        if audit is None:
            raise ComplianceNotFound("Audit non trovato")
        if audit.status != AuditStatus.PLANNED or audit.findings:
            raise ComplianceError("Si elimina solo un audit pianificato e senza rilievi; per gli altri usare l'annullamento")
        audit.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "audit_plan", "delete", entity_id=audit.id, actor_user_id=actor, old_values={"code": audit.code})
