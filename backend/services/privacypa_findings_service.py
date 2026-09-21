"""
PrivacyPA — Regole di conformità: registro delle regole e loro valutatori (§38, AC-PROC-002/003, §21, §167, §168).

Ogni regola ha un codice, una condizione leggibile (mostrata come "Perché vedo questa segnalazione?"),
una gravità e parametri di default, e un valutatore che restituisce le violazioni correnti (`Hit`) su tutti i
dati dell'ente. Persistenza e ciclo di vita dei finding sono in `privacypa_rules_engine`.
"""
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_annotations import CustomFieldDefinition, CustomFieldValue
from backend.models.privacypa_assessments import DpiaStatus, InternationalTransfer, TransferLink
from backend.models.privacypa_assets import Asset, AssetLifecycle, ProcessingAsset
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_catalog import DataCategoryCatalog, GdprDataClass
from backend.models.privacypa_compliance import (
    ActionStatus,
    BreachStatus,
    CorrectiveAction,
    DataBreach,
    DataSubjectRequest,
    RequestStatus,
)
from backend.models.privacypa_notices import DataFlow, FlowEndpointType, NoticeStatus, PrivacyNotice
from backend.models.privacypa_parties import (
    ProcessingRecipient,
    AgreementStatus,
    Contract,
    ContractStatus,
    Party,
    ProcessingProcessor,
    ProcessorAgreement,
    Subprocessor,
    SubprocessorStatus,
)
from backend.models.privacypa_people import (
    Authorization,
    AssignmentStatus,
    DpoOpinion,
    OpinionStatus,
    TrainingAssignment,
    TrainingCompletion,
    TrainingCourse,
)
from backend.models.privacypa_processing import ProcessingActivity, ProcessingDataCategory, ProcessingStatus
from backend.models.privacypa_projects import Project, ProjectStatus
from backend.models.privacypa_retention import RetentionRule
from backend.models.privacypa_risk import RiskAssessment
from backend.models.privacypa_rules import Severity
from backend.models.privacypa_tasks import PrivacyTask, TaskPriority, TaskSourceType, TaskStatus
from backend.services.privacypa_annotation_service import load_refs
from backend.services.privacypa_document_service import DocumentService
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_task_service import TaskService

_LIVE = (ProcessingStatus.APPROVED, ProcessingStatus.ACTIVE)
_SPECIAL = (GdprDataClass.SPECIAL_ART9, GdprDataClass.CRIMINAL_ART10)
_PROCESSING_TYPES = ("processing_activity", "processing")
CHECKLIST_MARKER = "Checklist di dismissione"
CHECKLIST_ITEMS = (
    "Verificare la revoca degli account e degli accessi tecnici",
    "Verificare l'esportazione dei dati",
    "Verificare la cancellazione o la conservazione dei dati presso il fornitore",
    "Aggiornare i trattamenti collegati",
    "Chiudere gli accessi del fornitore",
)


def agreement_is_valid(agreement: ProcessorAgreement, today: date) -> bool:
    """Atto art. 28 firmato, in vigore e non scaduto."""
    return (
        agreement.status == AgreementStatus.SIGNED
        and (agreement.effective_from is None or agreement.effective_from <= today)
        and (agreement.expiry_date is None or agreement.expiry_date >= today)
    )


@dataclass
class Hit:
    """Una violazione rilevata da una regola."""

    entity_type: str
    entity_id: str
    message: str
    entity_code: str | None = None
    processing_id: str | None = None  # trattamento di riferimento (visibilità per scope)
    unit_id: str | None = None        # struttura dell'entità (violazioni, richieste, audit)
    subject: str = ""                 # distingue più violazioni sulla stessa entità
    due_date: date | None = None
    params: dict = field(default_factory=dict)


class Context:
    """Dati dell'ente caricati una sola volta e condivisi dai valutatori."""

    def __init__(self, db: AsyncSession, tenant_id: str, today: date):
        self.db, self.tenant_id, self.today = db, tenant_id, today
        self._cache: dict[str, object] = {}

    async def _rows(self, key: str, stmt):
        if key not in self._cache:
            self._cache[key] = list((await self.db.execute(stmt)).scalars().all())
        return self._cache[key]

    async def parties(self) -> dict[str, Party]:
        rows = await self._rows("parties", select(Party).where(Party.tenant_id == self.tenant_id, Party.deleted_at.is_(None)))
        return {p.id: p for p in rows}

    async def party_name(self, party_id: str) -> str:
        return getattr(( await self.parties()).get(party_id), "legal_name", None) or party_id

    async def agreements(self) -> list[ProcessorAgreement]:
        return await self._rows("agreements", select(ProcessorAgreement).where(ProcessorAgreement.tenant_id == self.tenant_id))

    async def agreements_by_party(self) -> dict[str, list[ProcessorAgreement]]:
        out: dict[str, list[ProcessorAgreement]] = {}
        for a in await self.agreements():
            out.setdefault(a.party_id, []).append(a)
        return out

    async def live(self) -> dict[str, ProcessingActivity]:
        rows = await self._rows("live", select(ProcessingActivity).where(
            ProcessingActivity.tenant_id == self.tenant_id,
            ProcessingActivity.deleted_at.is_(None),
            ProcessingActivity.status.in_(_LIVE),
        ))
        return {a.id: a for a in rows}

    async def processor_links(self) -> list[ProcessingProcessor]:
        live = await self.live()
        if not live:
            return []
        return await self._rows("links", select(ProcessingProcessor).where(
            ProcessingProcessor.processing_activity_id.in_(list(live))))

    async def contracts(self) -> list[Contract]:
        return await self._rows("contracts", select(Contract).where(
            Contract.tenant_id == self.tenant_id, Contract.deleted_at.is_(None), Contract.status == ContractStatus.ACTIVE))

    async def subprocessors(self) -> list[Subprocessor]:
        return await self._rows("subs", select(Subprocessor).where(
            Subprocessor.tenant_id == self.tenant_id, Subprocessor.deleted_at.is_(None),
            Subprocessor.status == SubprocessorStatus.ACTIVE))

    async def assets(self) -> list[Asset]:
        return await self._rows("assets", select(Asset).where(Asset.tenant_id == self.tenant_id, Asset.deleted_at.is_(None)))

    async def tasks(self) -> list[PrivacyTask]:
        return await self._rows("tasks", select(PrivacyTask).where(
            PrivacyTask.tenant_id == self.tenant_id, PrivacyTask.deleted_at.is_(None)))


Evaluator = Callable[[Context, dict], Awaitable[list[Hit]]]


@dataclass
class RuleSpec:
    code: str
    name: str
    description: str
    condition: str  # può contenere {parametri}
    severity: Severity
    evaluator: Evaluator
    params: dict = field(default_factory=dict)
    # Azione eseguita una sola volta, quando il finding viene aperto (es. creazione di una checklist).
    on_open: Callable[[Context, Hit], Awaitable[None]] | None = None


# ---------------------------------------------------------------- valutatori

async def _processor_without_agreement(ctx: Context, params: dict) -> list[Hit]:
    live, by_party = await ctx.live(), await ctx.agreements_by_party()
    by_id = {a.id: a for a in await ctx.agreements()}
    hits = []
    for link in await ctx.processor_links():
        if link.processor_agreement_id:
            chosen = by_id.get(link.processor_agreement_id)
            ok = chosen is not None and chosen.party_id == link.party_id and agreement_is_valid(chosen, ctx.today)
        else:
            ok = any(agreement_is_valid(a, ctx.today) for a in by_party.get(link.party_id, []))
        if not ok:
            activity, party = live[link.processing_activity_id], await ctx.party_name(link.party_id)
            hits.append(Hit(
                "processing_activity", activity.id,
                f"Il trattamento {activity.code} usa il responsabile {party} senza un atto art. 28 valido",
                entity_code=activity.code, processing_id=activity.id, subject=link.party_id,
                params={"processing": activity.code, "party": party},
            ))
    return hits


async def _agreement_expired(ctx: Context, params: dict) -> list[Hit]:
    hits = []
    for a in await ctx.agreements():
        if a.status == AgreementStatus.SIGNED and a.expiry_date and a.expiry_date < ctx.today:
            party = await ctx.party_name(a.party_id)
            hits.append(Hit(
                "processor_agreement", a.id, f"L'atto art. 28 con {party} è scaduto il {a.expiry_date.isoformat()}",
                due_date=a.expiry_date, params={"party": party, "date": a.expiry_date.isoformat()},
            ))
    return hits


async def _agreement_expiring(ctx: Context, params: dict) -> list[Hit]:
    horizon = ctx.today + timedelta(days=int(params.get("horizon_days", 60)))
    hits = []
    for a in await ctx.agreements():
        if a.status == AgreementStatus.SIGNED and a.expiry_date and ctx.today <= a.expiry_date <= horizon:
            party = await ctx.party_name(a.party_id)
            hits.append(Hit(
                "processor_agreement", a.id, f"L'atto art. 28 con {party} scade il {a.expiry_date.isoformat()}",
                due_date=a.expiry_date, params={"party": party, "date": a.expiry_date.isoformat()},
            ))
    return hits


async def _contract_expired(ctx: Context, params: dict) -> list[Hit]:
    hits = []
    for c in await ctx.contracts():
        if c.end_date and c.end_date < ctx.today:
            party = await ctx.party_name(c.party_id)
            hits.append(Hit(
                "contract", c.id, f"Il contratto {c.code} con {party} è scaduto il {c.end_date.isoformat()}",
                entity_code=c.code, due_date=c.end_date,
                params={"contract": c.code, "party": party, "date": c.end_date.isoformat()},
            ))
    return hits


async def _contract_expiring(ctx: Context, params: dict) -> list[Hit]:
    horizon = ctx.today + timedelta(days=int(params.get("horizon_days", 60)))
    hits = []
    for c in await ctx.contracts():
        if c.end_date and c.end_date < ctx.today:
            continue  # già scaduto: lo segnala CONTRACT_EXPIRED
        upcoming = [d for d in (c.end_date, c.renewal_date) if d and ctx.today <= d <= horizon]
        if upcoming:
            due, party = min(upcoming), await ctx.party_name(c.party_id)
            hits.append(Hit(
                "contract", c.id, f"Il contratto {c.code} con {party} scade o si rinnova il {due.isoformat()}",
                entity_code=c.code, due_date=due, params={"contract": c.code, "party": party, "date": due.isoformat()},
            ))
    return hits


async def _subprocessor_expired(ctx: Context, params: dict) -> list[Hit]:
    hits = []
    for s in await ctx.subprocessors():
        if s.valid_to and s.valid_to < ctx.today:
            sub, proc = await ctx.party_name(s.subprocessor_party_id), await ctx.party_name(s.processor_party_id)
            hits.append(Hit(
                "subprocessor", s.id,
                f"L'autorizzazione di {sub} come sub-responsabile di {proc} è scaduta",
                due_date=s.valid_to, params={"subprocessor": sub, "processor": proc, "date": s.valid_to.isoformat()},
            ))
    return hits


async def _subprocessor_no_clause(ctx: Context, params: dict) -> list[Hit]:
    by_party, hits = await ctx.agreements_by_party(), []
    for s in await ctx.subprocessors():
        valid = [a for a in by_party.get(s.processor_party_id, []) if agreement_is_valid(a, ctx.today)]
        if valid and not any(a.subprocessor_clause for a in valid):
            sub, proc = await ctx.party_name(s.subprocessor_party_id), await ctx.party_name(s.processor_party_id)
            hits.append(Hit(
                "subprocessor", s.id, f"L'atto art. 28 con {proc} non prevede la clausola sui sub-responsabili",
                params={"subprocessor": sub, "processor": proc},
            ))
    return hits


async def _retention_missing(ctx: Context, params: dict) -> list[Hit]:
    with_rules = set((await ctx.db.execute(select(RetentionRule.processing_activity_id).where(
        RetentionRule.tenant_id == ctx.tenant_id, RetentionRule.deleted_at.is_(None)))).scalars().all())
    return [
        Hit("processing_activity", a.id, f"Il trattamento {a.code} non ha regole di conservazione",
            entity_code=a.code, processing_id=a.id, params={"processing": a.code})
        for a in (await ctx.live()).values() if a.id not in with_rules
    ]


async def _risk_assessment_review(ctx: Context, params: dict) -> list[Hit]:
    live = await ctx.live()
    if not live:
        return []
    special = set((await ctx.db.execute(
        select(ProcessingDataCategory.processing_activity_id)
        .join(DataCategoryCatalog, DataCategoryCatalog.id == ProcessingDataCategory.data_category_id)
        .where(ProcessingDataCategory.processing_activity_id.in_(list(live)), DataCategoryCatalog.gdpr_class.in_(_SPECIAL))
    )).scalars().all())
    assessed = set((await ctx.db.execute(select(RiskAssessment.entity_id).where(
        RiskAssessment.tenant_id == ctx.tenant_id, RiskAssessment.entity_type.in_(_PROCESSING_TYPES)))).scalars().all())
    return [
        Hit("processing_activity", pid, f"Il trattamento {live[pid].code} tratta categorie particolari di dati senza una valutazione del rischio",
            entity_code=live[pid].code, processing_id=pid, params={"processing": live[pid].code})
        for pid in sorted(special - assessed)
    ]


async def _asset_owner_missing(ctx: Context, params: dict) -> list[Hit]:
    return [
        Hit("asset", a.id, f"Il sistema {a.code} non ha una struttura responsabile", entity_code=a.code, params={"asset": a.code})
        for a in await ctx.assets()
        if a.owner_unit_id is None and a.lifecycle_status != AssetLifecycle.DISMISSED
    ]


async def _processing_owner_missing(ctx: Context, params: dict) -> list[Hit]:
    return [
        Hit("processing_activity", a.id, f"Il trattamento {a.code} non ha un responsabile interno",
            entity_code=a.code, processing_id=a.id, params={"processing": a.code})
        for a in (await ctx.live()).values() if not a.owner_user_id
    ]


async def _processing_review_due(ctx: Context, params: dict) -> list[Hit]:
    return [
        Hit("processing_activity", a.id, f"Il trattamento {a.code} andava rivisto entro il {a.next_review_date.isoformat()}",
            entity_code=a.code, processing_id=a.id, due_date=a.next_review_date,
            params={"processing": a.code, "date": a.next_review_date.isoformat()})
        for a in (await ctx.live()).values() if a.next_review_date and a.next_review_date <= ctx.today
    ]


def _is_checklist(task: PrivacyTask) -> bool:
    return (task.description or "").startswith(CHECKLIST_MARKER)


async def _asset_dismissed(ctx: Context, params: dict) -> list[Hit]:
    """Sistema dismesso: la checklist va creata (una volta) e completata."""
    tasks_by_asset: dict[str, list[PrivacyTask]] = {}
    for t in await ctx.tasks():
        if t.source_type == TaskSourceType.ASSET and t.source_id and _is_checklist(t):
            tasks_by_asset.setdefault(t.source_id, []).append(t)
    hits = []
    for a in await ctx.assets():
        if a.lifecycle_status != AssetLifecycle.DISMISSED:
            continue
        tasks = tasks_by_asset.get(a.id, [])
        open_tasks = [t for t in tasks if t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)]
        if not tasks or open_tasks:
            hits.append(Hit(
                "asset", a.id, f"Il sistema {a.code} è stato dismesso: completare la checklist di dismissione",
                entity_code=a.code, params={"asset": a.code, "open_tasks": len(open_tasks) if tasks else len(CHECKLIST_ITEMS)},
            ))
    return hits


async def _create_dismissal_checklist(ctx: Context, hit: Hit) -> None:
    asset = next(a for a in await ctx.assets() if a.id == hit.entity_id)
    tasks = TaskService(ctx.db)
    for title in CHECKLIST_ITEMS:
        await tasks.create(
            ctx.tenant_id, "system", title,
            description=f"{CHECKLIST_MARKER} del sistema {asset.code} ({asset.name})",
            source_type=TaskSourceType.ASSET, source_id=asset.id, assigned_unit_id=asset.owner_unit_id,
            priority=TaskPriority.MEDIUM, status=TaskStatus.OPEN,
        )
    ctx._cache.pop("tasks", None)


# ---------------------------------------------------------------- informative e flussi

# Campi del trattamento la cui modifica richiede di ricontrollare l'informativa (§25.1): chiave dell'audit -> etichetta.
_NOTICE_RELEVANT = {
    "purposes": "finalità", "purpose_summary": "finalità", "legal_bases": "base giuridica",
    "data_subjects": "dati", "data_categories": "dati", "recipients": "destinatari", "processors": "destinatari",
    "legal_references": "base giuridica",
}
_CONTROLLER_FIELDS = ("name", "pec", "dpo_name", "dpo_contact", "tax_code", "ipa_code")


def _content(value):
    """Forma confrontabile di un valore di audit: senza id (che cambiano quando un collegamento viene ricreato)."""
    if isinstance(value, list):
        return sorted((_content(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, dict):
        return {k: _content(v) for k, v in value.items() if k not in ("id", "processing_activity_id")}
    return value


async def _notice_review_required(ctx: Context, params: dict) -> list[Hit]:
    """Trattamento collegato modificato dopo l'approvazione (o l'ultima verifica) dell'informativa (§25.1, §38.2)."""
    notices = list((await ctx.db.execute(
        select(PrivacyNotice).options(selectinload(PrivacyNotice.versions)).where(
            PrivacyNotice.tenant_id == ctx.tenant_id, PrivacyNotice.deleted_at.is_(None),
            PrivacyNotice.current_version_id.is_not(None), PrivacyNotice.status != NoticeStatus.RETIRED)
    )).scalars().all())
    if not notices:
        return []
    activities = {a.id: a for a in (await ctx.db.execute(select(ProcessingActivity).where(
        ProcessingActivity.tenant_id == ctx.tenant_id, ProcessingActivity.deleted_at.is_(None)))).scalars().all()}
    events = list((await ctx.db.execute(select(AuditLog).where(
        AuditLog.tenant_id == ctx.tenant_id,
        AuditLog.entity_type.in_(("processing_activity", "retention_rule", "organization")),
        AuditLog.action.in_(("update", "update_relations", "create", "delete")),
    ))).scalars().all())
    transfers = list((await ctx.db.execute(select(InternationalTransfer, TransferLink.entity_id).join(
        TransferLink, TransferLink.transfer_id == InternationalTransfer.id).where(
        InternationalTransfer.tenant_id == ctx.tenant_id, TransferLink.entity_type.in_(_PROCESSING_TYPES)))).all())

    hits: list[Hit] = []
    for notice in notices:
        since = notice.reference_date
        if since is None:
            continue
        changed: dict[str, set[str]] = {}
        for e in events:
            if e.event_time is None or e.event_time <= since:
                continue
            if e.entity_type == "processing_activity" and e.action in ("update", "update_relations"):
                pid, old, new = e.entity_id, e.old_values or {}, e.new_values or {}
                for key, label in _NOTICE_RELEVANT.items():
                    if (key in old or key in new) and _content(old.get(key)) != _content(new.get(key)):
                        changed.setdefault(pid, set()).add(label)
            elif e.entity_type == "retention_rule":
                pid = (e.event_metadata or {}).get("processing_activity_id")
                if pid:
                    changed.setdefault(pid, set()).add("conservazione")
        for transfer, pid in transfers:
            if (transfer.created_at and transfer.created_at > since) or (transfer.updated_at and transfer.updated_at > since):
                changed.setdefault(pid, set()).add("trasferimenti")
        linked = {l.processing_activity_id for l in notice.links}
        for pid in sorted(linked & set(changed)):
            activity = activities.get(pid)
            if activity is None:
                continue
            fields = ", ".join(sorted(changed[pid]))
            hits.append(Hit(
                "privacy_notice", notice.id,
                f"Il trattamento {activity.code} è stato modificato dopo l'approvazione dell'informativa {notice.code}. Verificare coerenza su: {fields}",
                entity_code=notice.code, processing_id=pid, subject=pid,
                params={"processing": activity.code, "notice": notice.code, "fields": fields},
            ))
        controller = set()
        for e in events:
            if e.entity_type == "organization" and e.action == "update" and e.event_time and e.event_time > since:
                old, new = e.old_values or {}, e.new_values or {}
                controller |= {f for f in _CONTROLLER_FIELDS if old.get(f) != new.get(f)}
        if controller:
            hits.append(Hit(
                "privacy_notice", notice.id,
                f"I dati del titolare o del DPO sono cambiati dopo l'approvazione dell'informativa {notice.code}: verificare i contatti",
                entity_code=notice.code, subject="controller", params={"notice": notice.code},
            ))
    return hits


async def _flow_unlinked_endpoint(ctx: Context, params: dict) -> list[Hit]:
    """Un flusso passa per un sistema o un soggetto che il trattamento non dichiara tra i propri collegamenti."""
    live = await ctx.live()
    if not live:
        return []
    ids = list(live)
    flows = list((await ctx.db.execute(select(DataFlow).where(
        DataFlow.tenant_id == ctx.tenant_id, DataFlow.deleted_at.is_(None), DataFlow.processing_activity_id.in_(ids)))).scalars().all())
    if not flows:
        return []
    assets: dict[str, set[str]] = {}
    for row in (await ctx.db.execute(select(ProcessingAsset).where(ProcessingAsset.processing_activity_id.in_(ids)))).scalars():
        assets.setdefault(row.processing_activity_id, set()).add(row.asset_id)
    parties: dict[str, set[str]] = {}
    for row in await ctx.processor_links():
        parties.setdefault(row.processing_activity_id, set()).add(row.party_id)
    for row in (await ctx.db.execute(select(ProcessingRecipient).where(ProcessingRecipient.processing_activity_id.in_(ids)))).scalars():
        parties.setdefault(row.processing_activity_id, set()).add(row.party_id)
    names = {a.id: a.name for a in await ctx.assets()}
    hits = []
    for flow in flows:
        activity = live[flow.processing_activity_id]
        for side in ("source", "target"):
            kind, entity_id = getattr(flow, f"{side}_type"), getattr(flow, f"{side}_id")
            if kind == FlowEndpointType.ASSET and entity_id not in assets.get(activity.id, set()):
                label = names.get(entity_id, entity_id)
            elif kind == FlowEndpointType.PARTY and entity_id not in parties.get(activity.id, set()):
                label = await ctx.party_name(entity_id)
            else:
                continue
            hits.append(Hit(
                "processing_activity", activity.id,
                f"Un flusso del trattamento {activity.code} coinvolge {label}, che non è collegato al trattamento",
                entity_code=activity.code, processing_id=activity.id, subject=f"{flow.id}:{side}",
                params={"processing": activity.code, "endpoint": label},
            ))
    return hits


# ---------------------------------------------------------------- azioni correttive, violazioni, richieste

async def _action_overdue(ctx: Context, params: dict) -> list[Hit]:
    rows = (await ctx.db.execute(select(CorrectiveAction).where(
        CorrectiveAction.tenant_id == ctx.tenant_id, CorrectiveAction.deleted_at.is_(None),
        CorrectiveAction.status.in_((ActionStatus.OPEN, ActionStatus.IN_PROGRESS)),
        CorrectiveAction.due_date < ctx.today))).scalars().all()
    return [
        Hit("corrective_action", a.id, f"L'azione correttiva {a.code} è scaduta il {a.due_date.isoformat()}",
            entity_code=a.code, unit_id=a.organizational_unit_id, due_date=a.due_date,
            params={"action": a.code, "date": a.due_date.isoformat()})
        for a in rows
    ]


async def _breach_decision_missing(ctx: Context, params: dict) -> list[Hit]:
    """Promemoria a documentare la decisione sulla notifica: non stabilisce l'obbligo (§26.2)."""
    warn = int(params.get("warn_hours", 48))
    now = datetime.utcnow()
    rows = (await ctx.db.execute(select(DataBreach).where(
        DataBreach.tenant_id == ctx.tenant_id, DataBreach.deleted_at.is_(None), DataBreach.status != BreachStatus.CLOSED,
        DataBreach.notify_authority.is_(None)))).scalars().all()
    return [
        Hit("data_breach", b.id,
            f"Dalla conoscenza della violazione {b.code} sono trascorse {int((now - b.aware_at).total_seconds() // 3600)} ore "
            "e non è registrata la decisione sulla notifica all'Autorità",
            entity_code=b.code, unit_id=b.organizational_unit_id,
            params={"breach": b.code, "hours": int((now - b.aware_at).total_seconds() // 3600)})
        for b in rows if (now - b.aware_at) >= timedelta(hours=warn)
    ]


async def _open_requests(ctx: Context) -> list[DataSubjectRequest]:
    return list((await ctx.db.execute(select(DataSubjectRequest).where(
        DataSubjectRequest.tenant_id == ctx.tenant_id, DataSubjectRequest.deleted_at.is_(None),
        DataSubjectRequest.status.in_((RequestStatus.RECEIVED, RequestStatus.IN_PROGRESS))))).scalars().all())


async def _dsr_approaching(ctx: Context, params: dict) -> list[Hit]:
    now, days = datetime.utcnow(), int(params.get("days", 7))
    return [
        Hit("data_subject_request", r.id, f"La richiesta {r.code} scade il {r.due_at.date().isoformat()}",
            entity_code=r.code, unit_id=r.organizational_unit_id, due_date=r.due_at.date(),
            params={"request": r.code, "date": r.due_at.date().isoformat()})
        for r in await _open_requests(ctx) if now <= r.due_at <= now + timedelta(days=days)
    ]


async def _dsr_overdue(ctx: Context, params: dict) -> list[Hit]:
    now = datetime.utcnow()
    return [
        Hit("data_subject_request", r.id, f"La richiesta {r.code} ha superato il termine del {r.due_at.date().isoformat()}",
            entity_code=r.code, unit_id=r.organizational_unit_id, due_date=r.due_at.date(),
            params={"request": r.code, "date": r.due_at.date().isoformat()})
        for r in await _open_requests(ctx) if r.due_at < now
    ]


# ---------------------------------------------------------------- formazione, autorizzati, pareri del DPO

async def _training_overdue(ctx: Context, params: dict) -> list[Hit]:
    courses = {c.id: c for c in (await ctx.db.execute(select(TrainingCourse).where(
        TrainingCourse.tenant_id == ctx.tenant_id, TrainingCourse.deleted_at.is_(None), TrainingCourse.mandatory.is_(True),
        TrainingCourse.active.is_(True)))).scalars().all()}
    if not courses:
        return []
    rows = (await ctx.db.execute(select(TrainingAssignment).where(
        TrainingAssignment.tenant_id == ctx.tenant_id, TrainingAssignment.status == AssignmentStatus.ASSIGNED,
        TrainingAssignment.due_date < ctx.today, TrainingAssignment.course_id.in_(list(courses))))).scalars().all()
    return [
        Hit("training_assignment", a.id,
            f"La formazione obbligatoria {courses[a.course_id].code} assegnata a {a.user_id} è scaduta il {a.due_date.isoformat()}",
            entity_code=courses[a.course_id].code, due_date=a.due_date,
            params={"course": courses[a.course_id].code, "user": a.user_id, "date": a.due_date.isoformat()})
        for a in rows
    ]


async def _training_renewal_due(ctx: Context, params: dict) -> list[Hit]:
    """Ultimo completamento in scadenza (o scaduto) senza una nuova assegnazione aperta né un completamento successivo."""
    horizon = ctx.today + timedelta(days=int(params.get("horizon_days", 60)))
    courses = {c.id: c for c in (await ctx.db.execute(select(TrainingCourse).where(
        TrainingCourse.tenant_id == ctx.tenant_id, TrainingCourse.deleted_at.is_(None), TrainingCourse.active.is_(True),
        TrainingCourse.validity_months.is_not(None)))).scalars().all()}
    if not courses:
        return []
    latest: dict[tuple[str, str], TrainingCompletion] = {}
    for c in (await ctx.db.execute(select(TrainingCompletion).where(
            TrainingCompletion.tenant_id == ctx.tenant_id, TrainingCompletion.course_id.in_(list(courses))))).scalars().all():
        key = (c.user_id, c.course_id)
        if key not in latest or c.completed_at > latest[key].completed_at:
            latest[key] = c
    open_assignments = {(a.user_id, a.course_id) for a in (await ctx.db.execute(select(TrainingAssignment).where(
        TrainingAssignment.tenant_id == ctx.tenant_id, TrainingAssignment.status == AssignmentStatus.ASSIGNED))).scalars().all()}
    return [
        Hit("training_completion", c.id,
            f"La formazione {courses[c.course_id].code} di {c.user_id} scade il {c.next_due_at.isoformat()}: assegnare il rinnovo",
            entity_code=courses[c.course_id].code, due_date=c.next_due_at,
            params={"course": courses[c.course_id].code, "user": c.user_id, "date": c.next_due_at.isoformat()})
        for key, c in latest.items() if c.next_due_at and c.next_due_at <= horizon and key not in open_assignments
    ]


async def _authorization_expired(ctx: Context, params: dict) -> list[Hit]:
    rows = (await ctx.db.execute(select(Authorization).where(
        Authorization.tenant_id == ctx.tenant_id, Authorization.revoked_at.is_(None), Authorization.valid_to.is_not(None),
        Authorization.valid_to < ctx.today))).scalars().all()
    return [
        Hit("authorization", a.id, f"L'autorizzazione di {a.user_id} è scaduta il {a.valid_to.isoformat()} e non risulta revocata",
            processing_id=a.processing_activity_id, due_date=a.valid_to,
            params={"user": a.user_id, "date": a.valid_to.isoformat()})
        for a in rows
    ]


async def _authorization_review_needed(ctx: Context, params: dict) -> list[Hit]:
    """La struttura del trattamento è cambiata dopo la concessione (o l'ultima verifica confermata) dell'autorizzazione (§45)."""
    auths = [a for a in (await ctx.db.execute(select(Authorization).where(
        Authorization.tenant_id == ctx.tenant_id, Authorization.revoked_at.is_(None),
        Authorization.processing_activity_id.is_not(None)))).scalars().all() if a.is_active(ctx.today)]
    if not auths:
        return []
    events = list((await ctx.db.execute(select(AuditLog).where(
        AuditLog.tenant_id == ctx.tenant_id, AuditLog.entity_type == "processing_activity", AuditLog.action == "update",
        AuditLog.entity_id.in_({a.processing_activity_id for a in auths})))).scalars().all())
    changed: dict[str, list[datetime]] = {}
    for e in events:
        old, new = e.old_values or {}, e.new_values or {}
        if e.event_time and old.get("organizational_unit_id") != new.get("organizational_unit_id"):
            changed.setdefault(e.entity_id, []).append(e.event_time)
    hits = []
    for a in auths:
        since = a.last_reviewed_at or a.granted_at
        if since and any(t > since for t in changed.get(a.processing_activity_id, [])):
            hits.append(Hit(
                "authorization", a.id, f"La struttura del trattamento collegato all'autorizzazione di {a.user_id} è cambiata: verificarla",
                processing_id=a.processing_activity_id, params={"user": a.user_id}))
    return hits


async def _dpo_opinion_pending(ctx: Context, params: dict) -> list[Hit]:
    limit = datetime.utcnow() - timedelta(days=int(params.get("days", 15)))
    rows = (await ctx.db.execute(select(DpoOpinion).where(
        DpoOpinion.tenant_id == ctx.tenant_id, DpoOpinion.status == OpinionStatus.REQUESTED,
        DpoOpinion.requested_at <= limit))).scalars().all()
    return [
        Hit("dpo_opinion", o.id, f"Il parere del DPO su {o.entity_code or o.entity_type} è in attesa dal {o.requested_at.date().isoformat()}",
            entity_code=o.entity_code, unit_id=o.organizational_unit_id,
            params={"entity": o.entity_code or o.entity_type, "date": o.requested_at.date().isoformat()})
        for o in rows
    ]


# ---------------------------------------------------------------- privacy by design (§30)

_PROJECT_REVIEW_STATES = (ProjectStatus.PRIVACY_REVIEW, ProjectStatus.ICT_REVIEW, ProjectStatus.DPO_REVIEW, ProjectStatus.APPROVAL)


async def _project_post_review_due(ctx: Context, params: dict) -> list[Hit]:
    rows = (await ctx.db.execute(select(Project).where(
        Project.tenant_id == ctx.tenant_id, Project.deleted_at.is_(None), Project.status == ProjectStatus.GO_LIVE,
        Project.post_review_due_date.is_not(None), Project.post_review_due_date <= ctx.today))).scalars().all()
    return [
        Hit("project", p.id, f"Il progetto {p.code} è in esercizio: la revisione post-implementazione era prevista per il {p.post_review_due_date.isoformat()}",
            entity_code=p.code, unit_id=p.proposing_unit_id, due_date=p.post_review_due_date,
            params={"project": p.code, "date": p.post_review_due_date.isoformat()})
        for p in rows
    ]


async def _project_stalled(ctx: Context, params: dict) -> list[Hit]:
    limit = datetime.utcnow() - timedelta(days=int(params.get("days", 30)))
    rows = (await ctx.db.execute(select(Project).where(
        Project.tenant_id == ctx.tenant_id, Project.deleted_at.is_(None), Project.status.in_(_PROJECT_REVIEW_STATES),
        Project.status_changed_at <= limit))).scalars().all()
    return [
        Hit("project", p.id, f"Il progetto {p.code} è fermo in {p.status.value} dal {p.status_changed_at.date().isoformat()}",
            entity_code=p.code, unit_id=p.proposing_unit_id,
            params={"project": p.code, "status": p.status.value, "date": p.status_changed_at.date().isoformat()})
        for p in rows
    ]


# ---------------------------------------------------------------- campi custom (§77)

async def _custom_field_missing(ctx: Context, params: dict) -> list[Hit]:
    """Elementi senza il valore di un campo custom obbligatorio (le definizioni sono per ente e per tipo di elemento)."""
    definitions = list((await ctx.db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.tenant_id == ctx.tenant_id, CustomFieldDefinition.required.is_(True),
        CustomFieldDefinition.active.is_(True)))).scalars().all())
    if not definitions:
        return []
    filled = {(d, e) for d, e in (await ctx.db.execute(select(CustomFieldValue.definition_id, CustomFieldValue.entity_id).where(
        CustomFieldValue.tenant_id == ctx.tenant_id, CustomFieldValue.value.is_not(None)))).all()}
    hits: list[Hit] = []
    for entity_type in sorted({d.entity_type for d in definitions}):
        refs = await load_refs(ctx.db, ctx.tenant_id, entity_type)
        for d in (d for d in definitions if d.entity_type == entity_type):
            for ref in refs:
                if (d.id, ref.entity_id) not in filled:
                    hits.append(Hit(
                        entity_type, ref.entity_id, f"Il campo obbligatorio «{d.label}» non è compilato per {ref.code or ref.entity_id}",
                        entity_code=ref.code, processing_id=ref.processing_id, unit_id=None if ref.processing_id else ref.unit_id,
                        subject=d.key, params={"field": d.label, "entity": ref.code or ref.entity_id}))
    return hits


# ---------------------------------------------------------------- scadenze di task, documenti e DPIA (§70: eventi dei webhook)

async def _task_overdue(ctx: Context, params: dict) -> list[Hit]:
    hits = []
    for t in await TaskService(ctx.db).list_tasks(ctx.tenant_id):
        if t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED) or not t.due_at or t.due_at.date() >= ctx.today:
            continue
        hits.append(Hit(
            "task", t.id, f"Il task {t.code} è scaduto il {t.due_at.date().isoformat()}", entity_code=t.code, unit_id=t.assigned_unit_id,
            processing_id=t.source_id if t.source_type == TaskSourceType.PROCESSING else None, due_date=t.due_at.date(),
            params={"task": t.code, "date": t.due_at.date().isoformat()}))
    return hits


async def _document_expiring(ctx: Context, params: dict) -> list[Hit]:
    limit = ctx.today + timedelta(days=int(params.get("horizon_days", 30)))
    hits = []
    for d in await DocumentService(ctx.db).list_documents(ctx.tenant_id):
        current = next((v for v in d.versions if v.id == d.current_version_id), None)
        if current is None or not current.expiry_date or current.expiry_date > limit:
            continue
        processing = next((lnk.entity_id for lnk in d.links if lnk.entity_type in _PROCESSING_TYPES), None)
        when = current.expiry_date.isoformat()
        hits.append(Hit(
            "document", d.id, f"Il documento {d.code} {'è scaduto' if current.expiry_date < ctx.today else 'scade'} il {when}",
            entity_code=d.code, processing_id=processing, due_date=current.expiry_date, params={"document": d.code, "date": when}))
    return hits


async def _dpia_review_due(ctx: Context, params: dict) -> list[Hit]:
    return [
        Hit("dpia", d.id, f"La DPIA {d.code} andava rivista entro il {d.next_review_date.isoformat()}", entity_code=d.code,
            processing_id=d.processing_activity_id, due_date=d.next_review_date, params={"dpia": d.code, "date": d.next_review_date.isoformat()})
        for d in await DpiaService(ctx.db).list_dpias(ctx.tenant_id)
        if d.status in (DpiaStatus.TO_REVIEW, DpiaStatus.APPROVED) and d.next_review_date and d.next_review_date <= ctx.today
    ]


# ---------------------------------------------------------------- registro

_H, _M, _L = Severity.HIGH, Severity.MEDIUM, Severity.LOW

RULES: tuple[RuleSpec, ...] = (
    RuleSpec("PROCESSOR_NO_VALID_AGREEMENT", "Responsabile senza atto art. 28 valido",
             "Un trattamento approvato o attivo usa un responsabile privo di un atto art. 28 valido (AC-PROC-002).",
             "Trattamento approvato/attivo con un responsabile collegato senza atto art. 28 firmato, in vigore e non scaduto "
             "(se il collegamento indica un atto preciso, deve essere quello valido).", _H, _processor_without_agreement),
    RuleSpec("AGREEMENT_EXPIRED", "Atto art. 28 scaduto", "Un atto art. 28 firmato ha superato la data di scadenza (AC-PROC-003).",
             "Atto art. 28 firmato con data di scadenza precedente a oggi.", _H, _agreement_expired),
    RuleSpec("AGREEMENT_EXPIRING", "Atto art. 28 in scadenza", "Un atto art. 28 firmato scade a breve (AC-PROC-003).",
             "Atto art. 28 firmato che scade entro {horizon_days} giorni.", _M, _agreement_expiring, {"horizon_days": 60}),
    RuleSpec("CONTRACT_EXPIRED", "Contratto scaduto", "Un contratto attivo ha superato la data di fine.",
             "Contratto attivo con data di fine precedente a oggi.", _H, _contract_expired),
    RuleSpec("CONTRACT_EXPIRING", "Contratto in scadenza o da rinnovare", "Un contratto attivo scade o va rinnovato a breve.",
             "Contratto attivo con fine o rinnovo entro {horizon_days} giorni.", _M, _contract_expiring, {"horizon_days": 60}),
    RuleSpec("SUBPROCESSOR_EXPIRED", "Autorizzazione di sub-responsabile scaduta", "L'autorizzazione a un sub-responsabile attivo è scaduta.",
             "Sub-responsabile attivo con validità terminata prima di oggi.", _H, _subprocessor_expired),
    RuleSpec("SUBPROCESSOR_NO_CLAUSE", "Atto senza clausola sui sub-responsabili",
             "Il responsabile ha sub-responsabili attivi ma il suo atto art. 28 valido non prevede la clausola.",
             "Sub-responsabile attivo il cui responsabile ha atti art. 28 validi senza la clausola sui sub-responsabili.", _M,
             _subprocessor_no_clause),
    RuleSpec("RETENTION_MISSING", "Conservazione non definita", "Un trattamento approvato o attivo non ha regole di conservazione (§21).",
             "Trattamento approvato/attivo senza alcuna regola di conservazione.", _M, _retention_missing),
    RuleSpec("RISK_ASSESSMENT_REVIEW", "Valutazione del rischio mancante",
             "Un trattamento con categorie particolari di dati (art. 9/10) non ha una valutazione del rischio.",
             "Trattamento approvato/attivo con categorie di dati art. 9 o 10 e nessuna valutazione del rischio registrata.", _M,
             _risk_assessment_review),
    RuleSpec("ASSET_OWNER_MISSING", "Sistema senza struttura responsabile", "Ogni sistema deve avere una struttura responsabile (§167).",
             "Sistema non dismesso senza struttura responsabile.", _L, _asset_owner_missing),
    RuleSpec("PROCESSING_OWNER_MISSING", "Trattamento senza responsabile interno", "Ogni trattamento attivo deve avere un responsabile (§167).",
             "Trattamento approvato/attivo senza responsabile interno.", _L, _processing_owner_missing),
    RuleSpec("PROCESSING_REVIEW_DUE", "Revisione del trattamento scaduta", "La data di prossima revisione è passata (§168).",
             "Trattamento approvato/attivo con prossima revisione a oggi o precedente.", _M, _processing_review_due),
    RuleSpec("ASSET_DISMISSED_CHECKLIST", "Checklist di dismissione", "Un sistema dismesso richiede verifiche di chiusura (§38.2).",
             "Sistema in stato dismesso con checklist non creata o con attività ancora aperte "
             "(la checklist viene creata automaticamente al primo rilevamento).", _M, _asset_dismissed, {}, _create_dismissal_checklist),
    RuleSpec("NOTICE_REVIEW_REQUIRED", "Informativa da verificare",
             "Un trattamento collegato a un'informativa approvata è cambiato dopo l'approvazione (§25.1). "
             "L'informativa non viene modificata: va verificata e, se serve, riapprovata o confermata.",
             "Informativa approvata con un trattamento collegato modificato dopo l'approvazione o l'ultima verifica confermata "
             "(finalità, base giuridica, dati, destinatari, conservazione, trasferimenti) oppure con contatti del titolare/DPO cambiati.",
             _M, _notice_review_required),
    RuleSpec("DATA_FLOW_UNLINKED_ENDPOINT", "Flusso con elemento non collegato",
             "Un flusso dei dati coinvolge un sistema o un soggetto che il trattamento non dichiara (§20).",
             "Trattamento approvato/attivo con un flusso il cui sistema non è tra i sistemi del trattamento "
             "o il cui soggetto non è tra responsabili e destinatari.", _L, _flow_unlinked_endpoint),
    RuleSpec("CORRECTIVE_ACTION_OVERDUE", "Azione correttiva scaduta", "Un'azione correttiva aperta ha superato la scadenza (§29).",
             "Azione correttiva aperta o in corso con scadenza precedente a oggi.", _H, _action_overdue),
    RuleSpec("BREACH_NOTIFICATION_DECISION_MISSING", "Decisione di notifica non registrata",
             "Una violazione dei dati non chiusa non ha la decisione sulla notifica all'Autorità dopo il tempo indicato. "
             "È un promemoria a documentare la valutazione: non stabilisce se la notifica sia dovuta (§26.2).",
             "Violazione non chiusa, senza decisione sulla notifica, con almeno {warn_hours} ore dalla conoscenza.",
             _H, _breach_decision_missing, {"warn_hours": 48}),
    RuleSpec("DSR_DEADLINE_APPROACHING", "Richiesta di un interessato in scadenza", "Il termine di risposta è vicino (§158).",
             "Richiesta non ancora evasa con termine di risposta entro {days} giorni.", _M, _dsr_approaching, {"days": 7}),
    RuleSpec("DSR_OVERDUE", "Richiesta di un interessato oltre il termine", "Il termine di risposta è scaduto (§158).",
             "Richiesta non ancora evasa con termine di risposta già trascorso.", _H, _dsr_overdue),
    RuleSpec("TRAINING_OVERDUE", "Formazione obbligatoria scaduta", "Un'assegnazione di formazione obbligatoria ha superato la scadenza (§44).",
             "Assegnazione di un corso obbligatorio attivo ancora aperta con scadenza precedente a oggi.", _M, _training_overdue),
    RuleSpec("TRAINING_RENEWAL_DUE", "Rinnovo della formazione", "La formazione completata scade a breve e non è assegnato il rinnovo (§44).",
             "Ultimo completamento di un corso con validità che scade entro {horizon_days} giorni, senza assegnazione aperta.",
             _L, _training_renewal_due, {"horizon_days": 60}),
    RuleSpec("AUTHORIZATION_EXPIRED_NOT_REVOKED", "Autorizzazione scaduta non revocata", "Un'autorizzazione con validità terminata non è stata revocata (§45).",
             "Autorizzazione non revocata con data di fine precedente a oggi.", _H, _authorization_expired),
    RuleSpec("AUTHORIZATION_REVIEW_UNIT_CHANGED", "Autorizzazione da verificare", "La struttura del trattamento è cambiata dopo la concessione (§45).",
             "Autorizzazione attiva su un trattamento la cui struttura è cambiata dopo la concessione o l'ultima verifica confermata.",
             _M, _authorization_review_needed),
    RuleSpec("DPO_OPINION_PENDING", "Parere del DPO in attesa", "Un parere richiesto al DPO non è ancora stato emesso (§43).",
             "Parere richiesto e non emesso da almeno {days} giorni.", _L, _dpo_opinion_pending, {"days": 15}),
    RuleSpec("PROJECT_POST_REVIEW_DUE", "Revisione post-implementazione da fare", "Un progetto in esercizio ha superato la data della revisione successiva (§30).",
             "Progetto in esercizio con data della revisione post-implementazione a oggi o precedente.", _M, _project_post_review_due),
    RuleSpec("PROJECT_STALLED", "Progetto fermo in revisione", "Un progetto attende da tempo la decisione di una fase di revisione (§30).",
             "Progetto in una fase di revisione senza cambi di stato da almeno {days} giorni.", _L, _project_stalled, {"days": 30}),
    RuleSpec("TASK_OVERDUE", "Task scaduto", "Un task non completato ha superato la scadenza.",
             "Task non completato né annullato con scadenza precedente a oggi.", _M, _task_overdue),
    RuleSpec("DOCUMENT_EXPIRING", "Documento in scadenza", "La versione corrente di un documento scade a breve o è scaduta.",
             "Documento la cui versione corrente scade entro {horizon_days} giorni o è già scaduta.", _L, _document_expiring, {"horizon_days": 30}),
    RuleSpec("DPIA_REVIEW_DUE", "DPIA da rivedere", "La data di revisione di una DPIA approvata è arrivata (§28).",
             "DPIA approvata o da rivedere con data di prossima revisione uguale o precedente a oggi.", _M, _dpia_review_due),
    RuleSpec("CUSTOM_FIELD_REQUIRED_MISSING", "Campo custom obbligatorio non compilato", "Un elemento non ha il valore di un campo custom obbligatorio (§77).",
             "Elemento di un tipo con un campo custom attivo e obbligatorio, privo del suo valore.", _L, _custom_field_missing),
)
RULES_BY_CODE = {r.code: r for r in RULES}
