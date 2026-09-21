from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_audit import AuditLog, ActorType
from backend.services.privacypa_outbox_service import enqueue
from backend.services.privacypa_webhook_events import fan_out_event
from backend.utils.request_context import get_request_id


class AuditService:
    """
    Scrittura e lettura dell'audit trail di dominio PrivacyPA.

    Append-only per design (§33.2): non esiste update/delete.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        tenant_id: str,
        entity_type: str,
        action: str,
        event_type: str | None = None,
        entity_id: str | None = None,
        actor_user_id: str | None = None,
        actor_type: ActorType = ActorType.USER,
        old_values: dict | None = None,
        new_values: dict | None = None,
        metadata: dict | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            event_type=event_type or f"{entity_type}.{action}",
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            old_values=old_values,
            new_values=new_values,
            event_metadata=metadata,
            request_id=request_id or get_request_id(),
        )
        self.db.add(entry)
        await self.db.flush()
        await self.db.refresh(entry)
        # Outbox (§130): lo stesso commit che scrive l'audit pubblica l'evento per i consumatori esterni.
        outbox_event = enqueue(self.db, entry)
        if outbox_event is not None:
            await self.db.flush()
            await fan_out_event(self.db, outbox_event)          # webhook (§70): stessa transazione
        return entry

    async def list_for_entity(
        self, tenant_id: str, entity_type: str, entity_id: str
    ) -> list[AuditLog]:
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.entity_type == entity_type,
                AuditLog.entity_id == entity_id,
            )
            .order_by(desc(AuditLog.event_time))
        )
        return list(result.scalars().all())

    async def list_recent(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        result = await self.db.execute(
            stmt.order_by(desc(AuditLog.event_time)).limit(limit).offset(offset)
        )
        return list(result.scalars().all())
