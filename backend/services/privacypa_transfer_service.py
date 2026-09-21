from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assessments import (
    InternationalTransfer,
    TiaOutcome,
    TransferLink,
    TransferMechanism,
    TransferStatus,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_entity, ensure_exists

EEA = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS",
    "IE", "IT", "LV", "LI", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}

_EDITABLE = {
    "title", "exporter_party_id", "importer_party_id", "country_code", "description",
    "mechanism", "adequacy_reference", "scc_module", "tia_required", "tia_outcome",
    "tia_rationale", "law_access_assessment", "supplementary_measures", "tia_document_id",
    "last_review_date", "next_review_date", "status",
}


def suggest_tia_required(country_code: str, mechanism: TransferMechanism) -> bool:
    cc = (country_code or "").upper()
    if cc in EEA:
        return False
    if mechanism == TransferMechanism.ADEQUACY:
        return False
    return True


class TransferService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(InternationalTransfer).where(
            InternationalTransfer.tenant_id == tenant_id,
            InternationalTransfer.deleted_at.is_(None),
        )

    async def list_transfers(self, tenant_id: str) -> list[InternationalTransfer]:
        result = await self.db.execute(self._q(tenant_id).order_by(InternationalTransfer.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, transfer_id: str) -> InternationalTransfer | None:
        result = await self.db.execute(self._q(tenant_id).where(InternationalTransfer.id == transfer_id))
        return result.scalar_one_or_none()

    async def list_links(self, transfer_id: str) -> list[TransferLink]:
        result = await self.db.execute(select(TransferLink).where(TransferLink.transfer_id == transfer_id))
        return list(result.scalars().all())

    async def _check_references(self, tenant_id: str, fields: dict) -> None:
        await ensure_exists(self.db, tenant_id, "party", fields.get("exporter_party_id"))
        await ensure_exists(self.db, tenant_id, "party", fields.get("importer_party_id"))
        await ensure_exists(self.db, tenant_id, "document", fields.get("tia_document_id"))

    async def check_links(self, tenant_id: str, links: list[dict] | None) -> None:
        for lnk in links or []:
            await ensure_entity(self.db, tenant_id, lnk.get("entity_type"), lnk.get("entity_id"))

    async def create(self, tenant_id: str, actor: str, title: str, country_code: str, **fields) -> InternationalTransfer:
        await self._check_references(tenant_id, fields)
        cc = (country_code or "").upper()[:2]
        mech = fields.get("mechanism") or TransferMechanism.SCC
        if "tia_required" not in fields:
            fields["tia_required"] = suggest_tia_required(cc, mech)
            if not fields["tia_required"]:
                fields.setdefault("tia_outcome", TiaOutcome.NOT_REQUIRED)
        item = InternationalTransfer(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, InternationalTransfer, tenant_id, "TF"),
            title=title.strip(),
            country_code=cc,
            created_by=actor,
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "international_transfer", "create",
            entity_id=item.id, actor_user_id=actor, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def update(self, tenant_id: str, transfer_id: str, actor: str, **fields) -> InternationalTransfer:
        item = await self.get(tenant_id, transfer_id)
        if not item:
            raise ValueError("Trasferimento non trovato")
        await self._check_references(tenant_id, fields)
        old = item.to_dict()
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(item, k, v)
        if item.country_code:
            item.country_code = item.country_code.upper()[:2]
        item.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "international_transfer", "update",
            entity_id=item.id, actor_user_id=actor, old_values=old, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def set_links(self, transfer_id: str, links: list[dict]) -> list[TransferLink]:
        existing = await self.list_links(transfer_id)
        for row in existing:
            await self.db.delete(row)
        await self.db.flush()
        created = []
        seen: set[tuple[str, str]] = set()
        for lnk in links:
            et, eid = lnk.get("entity_type"), lnk.get("entity_id")
            if not et or not eid or (et, eid) in seen:
                continue
            seen.add((et, eid))
            row = TransferLink(transfer_id=transfer_id, entity_type=et, entity_id=eid)
            self.db.add(row)
            created.append(row)
        await self.db.flush()
        return created

    async def tia_pending(self, tenant_id: str) -> list[InternationalTransfer]:
        items = await self.list_transfers(tenant_id)
        return [
            t for t in items
            if t.tia_required and t.tia_outcome == TiaOutcome.PENDING and t.status != TransferStatus.ENDED
        ]

    async def review_due(self, tenant_id: str) -> list[InternationalTransfer]:
        today = date.today()
        items = await self.list_transfers(tenant_id)
        return [
            t for t in items
            if t.next_review_date and t.next_review_date <= today and t.status == TransferStatus.ACTIVE
        ]
