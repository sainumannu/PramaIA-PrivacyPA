from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_parties import (
    AgreementStatus,
    CommunicationType,
    Contract,
    Party,
    PartyType,
    ProcessingProcessor,
    ProcessingRecipient,
    ProcessorAgreement,
    ProcessorProfile,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_exists

_EDITABLE = {
    "party_type", "legal_name", "tax_id", "vat_id", "pec", "email",
    "country_code", "address", "website", "notes",
}
_PROFILE_FIELDS = {
    "dpo_contact", "security_contact", "privacy_contact", "status",
    "last_assessment_date", "next_assessment_date",
}
_AGREEMENT_FIELDS = {
    "contract_id", "signed_date", "effective_from", "expiry_date", "document_id", "status",
    "instructions_present", "confidentiality_clause", "security_clause",
    "subprocessor_clause", "rights_assistance_clause", "breach_assistance_clause",
    "dpia_assistance_clause", "deletion_return_clause", "audit_clause", "assessment_notes",
}


class PartyService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Party).where(Party.tenant_id == tenant_id, Party.deleted_at.is_(None))

    async def list_parties(self, tenant_id: str, party_type: PartyType | None = None, search: str | None = None) -> list[Party]:
        stmt = self._q(tenant_id).options(selectinload(Party.processor_profile))
        if party_type:
            stmt = stmt.where(Party.party_type == party_type)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(Party.legal_name.ilike(like) | Party.code.ilike(like))
        result = await self.db.execute(stmt.order_by(Party.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, party_id: str) -> Party | None:
        result = await self.db.execute(
            self._q(tenant_id).where(Party.id == party_id).options(selectinload(Party.processor_profile))
        )
        return result.scalar_one_or_none()

    async def create(self, tenant_id: str, actor: str, legal_name: str, **fields) -> Party:
        profile_data = fields.pop("processor_profile", None)
        party = Party(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, Party, tenant_id, "FO"),
            legal_name=legal_name.strip(),
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(party)
        await self.db.flush()
        if profile_data or party.party_type in (PartyType.PROCESSOR, PartyType.JOINT_CONTROLLER):
            pdata = profile_data or {}
            party.processor_profile = ProcessorProfile(
                party_id=party.id,
                **{k: v for k, v in pdata.items() if k in _PROFILE_FIELDS},
            )
        await self.db.flush()
        await self.audit.log(tenant_id, "party", "create", entity_id=party.id, actor_user_id=actor, new_values=party.to_dict())
        return await self.get(tenant_id, party.id)

    async def update(self, tenant_id: str, party_id: str, actor: str, **fields) -> Party:
        party = await self.get(tenant_id, party_id)
        if not party:
            raise ValueError("Soggetto non trovato")
        profile_data = fields.pop("processor_profile", None)
        old = party.to_dict(include_profile=True)
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(party, k, v)
        party.updated_at = datetime.utcnow()
        if profile_data is not None:
            if party.processor_profile is None:
                party.processor_profile = ProcessorProfile(party_id=party.id)
            for k, v in profile_data.items():
                if k in _PROFILE_FIELDS:
                    setattr(party.processor_profile, k, v)
        await self.db.flush()
        await self.audit.log(tenant_id, "party", "update", entity_id=party.id, actor_user_id=actor, old_values=old, new_values=party.to_dict(include_profile=True))
        return await self.get(tenant_id, party_id)

    async def soft_delete(self, tenant_id: str, party_id: str, actor: str) -> None:
        party = await self.get(tenant_id, party_id)
        if not party:
            raise ValueError("Soggetto non trovato")
        party.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "party", "delete", entity_id=party.id, actor_user_id=actor)

    async def list_agreements(self, tenant_id: str, party_id: str | None = None) -> list[ProcessorAgreement]:
        stmt = select(ProcessorAgreement).where(ProcessorAgreement.tenant_id == tenant_id)
        if party_id:
            stmt = stmt.where(ProcessorAgreement.party_id == party_id)
        result = await self.db.execute(stmt.order_by(ProcessorAgreement.created_at.desc()))
        return list(result.scalars().all())

    async def check_contract(self, tenant_id: str, party_id: str, contract_id: str | None) -> None:
        """Il contratto collegato deve esistere nell'ente ed essere del fornitore indicato."""
        if not contract_id:
            return
        found = (
            await self.db.execute(
                select(Contract.id).where(
                    Contract.id == contract_id,
                    Contract.tenant_id == tenant_id,
                    Contract.party_id == party_id,
                    Contract.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise ValueError("Contratto non trovato per questo soggetto")

    async def create_agreement(self, tenant_id: str, actor: str, party_id: str, **fields) -> ProcessorAgreement:
        if not await self.get(tenant_id, party_id):
            raise ValueError("Soggetto non trovato")
        await self.check_contract(tenant_id, party_id, fields.get("contract_id"))
        await ensure_exists(self.db, tenant_id, "document", fields.get("document_id"))
        agr = ProcessorAgreement(
            tenant_id=tenant_id,
            party_id=party_id,
            **{k: v for k, v in fields.items() if k in _AGREEMENT_FIELDS},
        )
        self.db.add(agr)
        await self.db.flush()
        await self.audit.log(tenant_id, "processor_agreement", "create", entity_id=agr.id, actor_user_id=actor, new_values=agr.to_dict())
        await self.db.refresh(agr)
        return agr

    async def update_agreement(self, tenant_id: str, agreement_id: str, actor: str, **fields) -> ProcessorAgreement:
        result = await self.db.execute(
            select(ProcessorAgreement).where(
                ProcessorAgreement.tenant_id == tenant_id, ProcessorAgreement.id == agreement_id
            )
        )
        agr = result.scalar_one_or_none()
        if not agr:
            raise ValueError("Atto art. 28 non trovato")
        await self.check_contract(tenant_id, agr.party_id, fields.get("contract_id"))
        await ensure_exists(self.db, tenant_id, "document", fields.get("document_id"))
        old = agr.to_dict()
        for k, v in fields.items():
            if k in _AGREEMENT_FIELDS:
                setattr(agr, k, v)
        await self.db.flush()
        await self.audit.log(tenant_id, "processor_agreement", "update", entity_id=agr.id, actor_user_id=actor, old_values=old, new_values=agr.to_dict())
        await self.db.refresh(agr)
        return agr

    async def agreements_to_review(self, tenant_id: str) -> list[ProcessorAgreement]:
        today = date.today()
        result = await self.db.execute(
            select(ProcessorAgreement).where(
                ProcessorAgreement.tenant_id == tenant_id,
                (ProcessorAgreement.status == AgreementStatus.TO_REVIEW)
                | (ProcessorAgreement.status == AgreementStatus.DRAFT)
                | (
                    ProcessorAgreement.expiry_date.is_not(None)
                    & (ProcessorAgreement.expiry_date <= today)
                ),
            )
        )
        return list(result.scalars().all())

    async def set_processors(self, processing_activity_id: str, links: list[dict]) -> list[ProcessingProcessor]:
        result = await self.db.execute(
            select(ProcessingProcessor).where(ProcessingProcessor.processing_activity_id == processing_activity_id)
        )
        for row in result.scalars().all():
            await self.db.delete(row)
        await self.db.flush()
        created = []
        seen: set[str] = set()
        for item in links:
            pid = item.get("party_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            row = ProcessingProcessor(
                processing_activity_id=processing_activity_id,
                party_id=pid,
                processor_agreement_id=item.get("processor_agreement_id"),
                contract_id=item.get("contract_id"),
                notes=item.get("notes"),
            )
            self.db.add(row)
            created.append(row)
        await self.db.flush()
        return created

    async def set_recipients(self, processing_activity_id: str, links: list[dict]) -> list[ProcessingRecipient]:
        result = await self.db.execute(
            select(ProcessingRecipient).where(ProcessingRecipient.processing_activity_id == processing_activity_id)
        )
        for row in result.scalars().all():
            await self.db.delete(row)
        await self.db.flush()
        created = []
        for item in links:
            pid = item.get("party_id")
            if not pid:
                continue
            ctype = item.get("communication_type") or CommunicationType.COMMUNICATION.value
            row = ProcessingRecipient(
                processing_activity_id=processing_activity_id,
                party_id=pid,
                communication_type=CommunicationType(ctype),
                notes=item.get("notes"),
            )
            self.db.add(row)
            created.append(row)
        await self.db.flush()
        return created

    async def list_processors(self, processing_activity_id: str) -> list[ProcessingProcessor]:
        result = await self.db.execute(
            select(ProcessingProcessor).where(ProcessingProcessor.processing_activity_id == processing_activity_id)
        )
        return list(result.scalars().all())

    async def list_recipients(self, processing_activity_id: str) -> list[ProcessingRecipient]:
        result = await self.db.execute(
            select(ProcessingRecipient).where(ProcessingRecipient.processing_activity_id == processing_activity_id)
        )
        return list(result.scalars().all())
