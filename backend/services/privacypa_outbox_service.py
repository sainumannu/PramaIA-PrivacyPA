"""
PrivacyPA — Outbox transazionale (§130): accodamento degli eventi di dominio e API di consumo.

L'accodamento avviene in `AuditService.log`, nella stessa transazione della modifica: se la modifica viene
annullata, l'evento non esiste. Il payload contiene solo riferimenti e le date/stati utili a chi deve
reagire (scadenze, revisioni): nessun dato descrittivo o personale, che il consumatore legge dall'API.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_outbox import OutboxEvent

# Eventi di audit pubblicati sull'outbox (prefissi del tipo evento).
OUTBOX_PREFIXES = (
    "approval.", "finding.", "rule.", "processing_activity.status.", "processing_activity.relations.",
    "processing_activity.create", "processing_activity.update",
    "dpia.status_change", "contract.", "subprocessor.", "processor_agreement.", "retention_rule.", "task.",
    "privacy_notice.", "data_flow.", "corrective_action.", "data_breach.", "data_subject_request.", "audit_plan.",
    "training_course.", "authorization.", "dpo_opinion.", "annual_report.", "project.",
)
# Campi di new_values copiati nel payload.
_DATA_KEYS = (
    "status", "due_at", "expiry_date", "end_date", "renewal_date", "next_review_date", "valid_to",
    "effective_from", "effective_to", "due_date", "go_live_date", "post_review_due_date", "aware_at", "decision", "entity_version", "approval_id", "rule_code", "rule_version", "entity_id",
)
MAX_BATCH = 500


def is_published(event_type: str) -> bool:
    return event_type.startswith(OUTBOX_PREFIXES)


def enqueue(db: AsyncSession, entry: AuditLog) -> OutboxEvent | None:
    """Accoda l'evento di audit sull'outbox, se è tra quelli pubblicati (nella sessione/transazione corrente)."""
    if not is_published(entry.event_type):
        return None
    data = {k: v for k, v in (entry.new_values or {}).items() if k in _DATA_KEYS and v is not None}
    event = OutboxEvent(
        tenant_id=entry.tenant_id,
        aggregate_type=entry.entity_type,
        aggregate_id=entry.entity_id,
        event_type=entry.event_type,
        payload={
            "event_type": entry.event_type,
            "tenant_id": entry.tenant_id,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "actor": entry.actor_user_id or "system",
            "occurred_at": (entry.event_time or datetime.utcnow()).isoformat(),
            "audit_id": entry.id,
            "data": data,
        },
    )
    db.add(event)
    return event


class OutboxService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_events(
        self, tenant_id: str, *, processed: bool = False, limit: int = 100, event_type: str | None = None
    ) -> list[OutboxEvent]:
        stmt = select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id)
        stmt = stmt.where(OutboxEvent.processed_at.is_not(None) if processed else OutboxEvent.processed_at.is_(None))
        if event_type:
            stmt = stmt.where(OutboxEvent.event_type.startswith(event_type))
        result = await self.db.execute(stmt.order_by(OutboxEvent.created_at, OutboxEvent.id).limit(min(limit, MAX_BATCH)))
        return list(result.scalars().all())

    async def ack(self, tenant_id: str, ids: list[str]) -> int:
        """Segna come consegnati gli eventi indicati. Idempotente: già confermati o di altri enti non contano."""
        rows = (await self.db.execute(select(OutboxEvent).where(
            OutboxEvent.tenant_id == tenant_id, OutboxEvent.id.in_(ids), OutboxEvent.processed_at.is_(None)))).scalars().all()
        now = datetime.utcnow()
        for row in rows:
            row.processed_at = now
        await self.db.flush()
        return len(rows)

    async def mark_failed(self, tenant_id: str, event_id: str) -> OutboxEvent | None:
        row = await self.db.get(OutboxEvent, event_id)
        if row is None or row.tenant_id != tenant_id or row.processed_at is not None:
            return None
        row.retry_count += 1
        await self.db.flush()
        return row

    async def purge_processed(self, tenant_id: str, before: datetime) -> int:
        rows = (await self.db.execute(select(OutboxEvent).where(
            OutboxEvent.tenant_id == tenant_id, OutboxEvent.processed_at.is_not(None), OutboxEvent.processed_at < before))).scalars().all()
        for row in rows:
            await self.db.delete(row)
        await self.db.flush()
        return len(rows)
