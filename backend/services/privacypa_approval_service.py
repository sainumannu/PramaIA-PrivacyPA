"""
PrivacyPA — Workflow di approvazione (§165, §166, AC-REG-003).

Ogni invio in revisione crea una richiesta legata alla versione del record; la decisione
(APPROVED / REJECTED / CHANGES_REQUESTED) è valida solo se il contenuto non è cambiato dopo la
richiesta ed è soggetta alla segregazione dei compiti configurata dall'ente. La transizione di stato
dell'entità avviene nella stessa transazione della decisione.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_access import AccessAssessment
from backend.models.privacypa_approvals import Approval, ApprovalDecision, ApprovalState
from backend.models.privacypa_assessments import DpiaStatus
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_notices import NoticeStatus
from backend.models.privacypa_processing import ProcessingStatus
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_notice_service import NoticeService
from backend.services.privacypa_policy_service import get_segregation
from backend.services.privacypa_processing_service import ProcessingService

PROCESSING = "processing_activity"
DPIA = "dpia"
NOTICE = "privacy_notice"
# Richiesta di accesso non standard inviata dall'HelpDesk: la decisione non cambia stati in PrivacyPA, la legge l'HelpDesk.
ACCESS = "access_assessment"
# Permesso `<modulo>.approve` che abilita la decisione, per tipo di entità.
APPROVE_PERMISSION = {PROCESSING: "processing.approve", DPIA: "dpia.approve", NOTICE: "notice.approve", ACCESS: "access.approve"}
# Stato in cui l'entità attende una decisione.
AWAITING_STATUS = {PROCESSING: ProcessingStatus.IN_REVIEW, DPIA: DpiaStatus.AWAITING_DECISION, NOTICE: NoticeStatus.IN_REVIEW}
# Richiedente delle richieste create per record già in revisione prima dell'introduzione delle approvazioni.
LEGACY_REQUESTER = "system:legacy"
# Azioni di audit che modificano il contenuto (rilevanti per la segregazione "chi ha modificato").
_CONTENT_ACTIONS = ("create", "update", "screening", "import", "version_create", "version_update", "links_update")


class ApprovalError(Exception):
    """Errore di dominio con codice HTTP suggerito."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def http_error(error: ApprovalError):
    """Adattatore HTTP per i router: stesso codice e messaggio dell'errore di dominio."""
    from fastapi import HTTPException

    return HTTPException(status_code=error.status_code, detail=error.message)


class ApprovalNotFound(ApprovalError):
    status_code = 404


class ApprovalConflict(ApprovalError):
    status_code = 409


class SegregationViolation(ApprovalError):
    status_code = 403


@dataclass
class EntityInfo:
    entity: object
    entity_type: str
    unit_id: str | None
    code: str | None
    title: str | None
    # Versione da approvare quando non è la row_version del record (valutazioni: versione della richiesta dell'HelpDesk).
    entity_version: int | None = None

    @property
    def version(self) -> int:
        return self.entity_version if self.entity_version is not None else self.entity.row_version


class ApprovalService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    # ------------------------------------------------------------ entità
    async def load_entity(self, tenant_id: str, entity_type: str, entity_id: str) -> EntityInfo | None:
        if entity_type == PROCESSING:
            entity = await ProcessingService(self.db).get_activity(tenant_id, entity_id)
            if entity is None:
                return None
            return EntityInfo(entity, entity_type, entity.organizational_unit_id, entity.code, entity.title)
        if entity_type == DPIA:
            entity = await DpiaService(self.db).get(tenant_id, entity_id)
            if entity is None:
                return None
            unit = None
            if entity.processing_activity_id:
                proc = await ProcessingService(self.db).get_activity(tenant_id, entity.processing_activity_id)
                unit = proc.organizational_unit_id if proc else None
            return EntityInfo(entity, entity_type, unit, entity.code, entity.title)
        if entity_type == NOTICE:
            entity = await NoticeService(self.db).get(tenant_id, entity_id)
            if entity is None:
                return None
            return EntityInfo(entity, entity_type, entity.owner_unit_id, entity.code, entity.title)
        if entity_type == ACCESS:
            entity = await self.db.get(AccessAssessment, entity_id)
            if entity is None or entity.tenant_id != tenant_id:
                return None
            from backend.services.privacypa_access_service import assessment_title

            return EntityInfo(entity, entity_type, entity.organizational_unit_id, entity.source_ref[:30],
                              await assessment_title(self.db, entity), entity_version=entity.request_version)
        return None

    async def content_editors(self, tenant_id: str, entity_type: str, entity_id: str) -> set[str]:
        """Chi ha modificato il contenuto dall'ultima approvazione (o da sempre, se non ce n'è)."""
        last = (
            await self.db.execute(
                select(Approval.decision_at)
                .where(
                    Approval.tenant_id == tenant_id,
                    Approval.entity_type == entity_type,
                    Approval.entity_id == entity_id,
                    Approval.decision == ApprovalDecision.APPROVED,
                )
                .order_by(Approval.decision_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        stmt = select(AuditLog.actor_user_id).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
            AuditLog.action.in_(_CONTENT_ACTIONS),
            AuditLog.actor_user_id.is_not(None),
        )
        if last is not None:
            stmt = stmt.where(AuditLog.event_time > last)
        return set((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ richieste
    async def get(self, tenant_id: str, approval_id: str) -> Approval | None:
        approval = await self.db.get(Approval, approval_id)
        return approval if approval and approval.tenant_id == tenant_id else None

    async def pending_for(self, tenant_id: str, entity_type: str, entity_id: str) -> Approval | None:
        result = await self.db.execute(
            select(Approval)
            .where(
                Approval.tenant_id == tenant_id,
                Approval.entity_type == entity_type,
                Approval.entity_id == entity_id,
                Approval.decision.is_(None),
                Approval.cancelled_at.is_(None),
            )
            .order_by(Approval.requested_at.desc())
        )
        return result.scalars().first()

    async def search(
        self,
        tenant_id: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        state: ApprovalState | None = None,
        limit: int = 200,
    ) -> list[Approval]:
        stmt = select(Approval).where(Approval.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(Approval.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(Approval.entity_id == entity_id)
        if state == ApprovalState.PENDING:
            stmt = stmt.where(Approval.decision.is_(None), Approval.cancelled_at.is_(None))
        elif state == ApprovalState.DECIDED:
            stmt = stmt.where(Approval.decision.is_not(None))
        elif state == ApprovalState.CANCELLED:
            stmt = stmt.where(Approval.decision.is_(None), Approval.cancelled_at.is_not(None))
        result = await self.db.execute(stmt.order_by(Approval.requested_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def request_review(
        self, tenant_id: str, info: EntityInfo, requested_by: str, notes: str | None = None
    ) -> Approval:
        """Da chiamare dopo la transizione verso lo stato di attesa: `info.version` è quella da approvare."""
        await self.cancel_pending(tenant_id, info.entity_type, info.entity.id, requested_by, audit=False)
        approval = Approval(
            tenant_id=tenant_id,
            entity_type=info.entity_type,
            entity_id=info.entity.id,
            entity_version=info.version,
            entity_code=info.code,
            entity_title=info.title,
            organizational_unit_id=info.unit_id,
            requested_by=requested_by,
            request_notes=notes,
        )
        self.db.add(approval)
        await self.db.flush()
        await self.audit.log(
            tenant_id, info.entity_type, "approval_requested", event_type="approval.requested",
            entity_id=info.entity.id, actor_user_id=requested_by,
            new_values={"approval_id": approval.id, "entity_version": approval.entity_version},
        )
        return approval

    async def cancel_pending(
        self, tenant_id: str, entity_type: str, entity_id: str, actor: str, *, audit: bool = True
    ) -> Approval | None:
        """Ritira la richiesta in attesa (il richiedente riporta il record in bozza, o ne invia una nuova)."""
        approval = await self.pending_for(tenant_id, entity_type, entity_id)
        if approval is None:
            return None
        approval.cancelled_at = datetime.utcnow()
        approval.cancelled_by = actor
        await self.db.flush()
        if audit:
            await self.audit.log(
                tenant_id, entity_type, "approval_cancelled", event_type="approval.cancelled",
                entity_id=entity_id, actor_user_id=actor, new_values={"approval_id": approval.id},
            )
        return approval

    async def ensure_pending(self, tenant_id: str, info: EntityInfo) -> Approval:
        """
        La richiesta in attesa per il record. Se manca (record già in revisione prima dell'introduzione
        delle approvazioni) ne crea una implicita, con richiedente sconosciuto: la segregazione
        "chi ha modificato" resta valida perché si basa sull'audit.
        """
        approval = await self.pending_for(tenant_id, info.entity_type, info.entity.id)
        if approval is not None:
            return approval
        return await self.request_review(
            tenant_id, info, LEGACY_REQUESTER, notes="Richiesta implicita: record già in revisione"
        )

    # ------------------------------------------------------------ segregazione
    async def segregation_problem(
        self, tenant_id: str, approval: Approval, approver_id: str
    ) -> str | None:
        """Motivo per cui `approver_id` non può decidere per policy dell'ente, oppure None."""
        if approval.entity_type == ACCESS:
            # Sempre, indipendentemente dalla policy: chi richiede (anche tramite l'HelpDesk) o riceve l'accesso non decide.
            assessment = await self.db.get(AccessAssessment, approval.entity_id)
            if assessment is not None and approver_id == assessment.subject_user_id:
                return "Non si decide su una richiesta di accesso che riguarda sé stessi"
            if approver_id in (approval.requested_by, approval.requested_on_behalf_of):
                return "Non si decide su una richiesta di accesso inviata da sé stessi"
        rules = await get_segregation(self.db, tenant_id)
        if rules["approver_not_requester"] and approver_id == approval.requested_by:
            return "La policy dell'ente non consente di approvare una richiesta inviata da sé stessi"
        if rules["approver_not_editor"]:
            editors = await self.content_editors(tenant_id, approval.entity_type, approval.entity_id)
            if approver_id in editors:
                return "La policy dell'ente non consente di approvare un contenuto modificato da sé stessi"
        return None

    def is_stale(self, approval: Approval, info: EntityInfo | None) -> bool:
        return info is None or info.version != approval.entity_version

    # ------------------------------------------------------------ decisione
    async def decide(
        self,
        tenant_id: str,
        approval: Approval,
        approver_id: str,
        decision: ApprovalDecision,
        notes: str | None = None,
    ) -> Approval:
        if approval.state != ApprovalState.PENDING:
            raise ApprovalConflict("La richiesta di approvazione non è più in attesa")
        if decision != ApprovalDecision.APPROVED and not (notes or "").strip():
            raise ApprovalError("Per respingere o chiedere modifiche è obbligatoria una nota")
        info = await self.load_entity(tenant_id, approval.entity_type, approval.entity_id)
        if self.is_stale(approval, info):
            raise ApprovalConflict(
                "Il contenuto è stato modificato dopo la richiesta: il richiedente deve inviarlo di nuovo in revisione"
            )
        problem = await self.segregation_problem(tenant_id, approval, approver_id)
        if problem:
            raise SegregationViolation(problem)

        await self._apply_transition(tenant_id, info, approver_id, decision)

        approval.approver_id = approver_id
        approval.decision = decision
        approval.decision_at = datetime.utcnow()
        approval.notes = (notes or "").strip() or None
        await self.db.flush()
        extra = {}
        if approval.entity_type == ACCESS:      # per il webhook access_approval.decided (le note restano fuori)
            extra = {"source_ref": info.entity.source_ref, "request_version": info.entity.request_version}
        await self.audit.log(
            tenant_id, approval.entity_type, "approval_decided", event_type=f"approval.{decision.value.lower()}",
            entity_id=approval.entity_id, actor_user_id=approver_id,
            new_values={"approval_id": approval.id, "decision": decision.value, "entity_version": approval.entity_version, **extra},
        )
        return approval

    async def _apply_transition(
        self, tenant_id: str, info: EntityInfo, actor: str, decision: ApprovalDecision
    ) -> None:
        approved = decision == ApprovalDecision.APPROVED
        if info.entity_type == PROCESSING:
            target = ProcessingStatus.APPROVED if approved else ProcessingStatus.DRAFT
            await ProcessingService(self.db).transition_status(tenant_id, info.entity.id, actor, target)
        elif info.entity_type == DPIA:
            target = DpiaStatus.APPROVED if approved else DpiaStatus.IN_PROGRESS
            await DpiaService(self.db).transition(tenant_id, info.entity.id, actor, target)
        elif info.entity_type == NOTICE:
            target = NoticeStatus.APPROVED if approved else NoticeStatus.DRAFT
            await NoticeService(self.db).transition(tenant_id, info.entity.id, actor, target)
        elif info.entity_type == ACCESS:
            return                              # la decisione resta sull'approvazione: l'HelpDesk la rilegge
        else:  # pragma: no cover - i tipi ammessi sono filtrati a monte
            raise ApprovalError(f"Tipo di entità non gestito: {info.entity_type}")
