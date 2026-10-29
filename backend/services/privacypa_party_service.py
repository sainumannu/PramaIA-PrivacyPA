from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_parties import (
    AgreementStatus,
    CommunicationType,
    Contract,
    ContractStatus,
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
from backend.utils.tax_codes import check_identifiers

_EDITABLE = {
    "party_type", "legal_name", "natural_person", "tax_id", "vat_id", "pec", "email",
    "country_code", "address", "website", "notes",
}
# La cessazione non è tra i campi modificabili: si registra e si annulla con set_cessation.
_PROFILE_FIELDS = {
    "dpo_contact", "security_contact", "privacy_contact", "status",
    "last_assessment_date", "next_assessment_date",
    "appointment_start", "appointment_end", "appointment_contract_id",
}
# Soggetti con un incarico a termine: responsabili (art. 28) e contitolari (art. 26)
APPOINTED_TYPES = (PartyType.PROCESSOR, PartyType.JOINT_CONTROLLER)
EXPIRING_DAYS = 60


def is_identified(party: Party) -> bool:
    """Il soggetto ha gli identificativi che oggi si chiedono a chi entra in anagrafica."""
    try:
        check_identifiers(party.country_code, bool(party.natural_person), party.tax_id, party.vat_id)
    except ValueError:
        return False
    return True


def appointment_of(profile: ProcessorProfile | None, contract: Contract | None, today: date) -> dict | None:
    """Durata e stato dell'incarico. La fine è quella del contratto collegato, se c'è, altrimenti la data indicata.

    Stati: ACTIVE, EXPIRING (finisce entro EXPIRING_DAYS giorni), CEASED (scaduto o cessato prima) e UNDEFINED (durata
    non indicata: soggetti registrati prima che fosse obbligatoria).
    """
    if profile is None:
        return None
    by_contract = bool(profile.appointment_contract_id)
    until = (contract.end_date if contract else None) if by_contract else profile.appointment_end
    if profile.ceased_on and profile.ceased_on <= today:
        state = "CEASED"
    elif until is None:
        state = "UNDEFINED"
    elif until < today:
        state = "CEASED"
    elif until <= today + timedelta(days=EXPIRING_DAYS):
        state = "EXPIRING"
    else:
        state = "ACTIVE"
    return {
        "start": profile.appointment_start.isoformat() if profile.appointment_start else None,
        "until": until.isoformat() if until else None,
        "source": "CONTRACT" if by_contract else "DATE" if profile.appointment_end else None,
        "contract_id": profile.appointment_contract_id,
        "contract_code": contract.code if contract else None,
        "contract_title": contract.title if contract else None,
        "ceased_on": profile.ceased_on.isoformat() if profile.ceased_on else None,
        "cessation_note": profile.cessation_note,
        "state": state,
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

    async def describe(self, tenant_id: str, parties: list[Party]) -> list[dict]:
        """I soggetti come li vede l'interfaccia: con l'esito dell'identificazione e la durata dell'incarico."""
        ids = {p.processor_profile.appointment_contract_id for p in parties
               if p.processor_profile and p.processor_profile.appointment_contract_id}
        contracts = {c.id: c for c in (await self.db.execute(select(Contract).where(
            Contract.tenant_id == tenant_id, Contract.id.in_(ids)))).scalars().all()} if ids else {}
        today = date.today()
        out = []
        for p in parties:
            data = p.to_dict(include_profile=True)
            data["identified"] = is_identified(p)
            profile = p.processor_profile
            data["appointment"] = appointment_of(
                profile, contracts.get(profile.appointment_contract_id) if profile else None, today,
            ) if p.party_type in APPOINTED_TYPES else None
            out.append(data)
        return out

    async def check_input(self, tenant_id: str, party_id: str | None, data: dict) -> dict:
        """Regole per chi inserisce o modifica un soggetto dall'interfaccia o dall'API.

        Il soggetto deve essere identificabile senza ambiguità (codice fiscale valido o, all'estero, un identificativo
        fiscale). Responsabili e contitolari hanno un incarico a termine: fino alla fine di un contratto (esistente o
        registrato insieme) oppure fino a una data.
        """
        data["tax_id"], data["vat_id"] = check_identifiers(
            data.get("country_code"), bool(data.get("natural_person")), data.get("tax_id"), data.get("vat_id"))
        data["country_code"] = (data.get("country_code") or "").upper() or None
        if data.get("party_type") not in APPOINTED_TYPES:
            data.pop("new_contract", None)
            return data
        profile = data.get("processor_profile") or {}
        new_contract = data.get("new_contract")
        start = profile.get("appointment_start")
        if new_contract:
            profile["appointment_contract_id"], profile["appointment_end"] = None, None
            end = new_contract.get("end_date")
        elif profile.get("appointment_contract_id"):
            if party_id is None:
                raise ValueError("Un soggetto nuovo non ha ancora contratti: registrare il contratto insieme al soggetto")
            await self.check_contract(tenant_id, party_id, profile["appointment_contract_id"])
            contract = await self.db.get(Contract, profile["appointment_contract_id"])
            if contract.end_date is None:
                raise ValueError("Il contratto scelto non ha una data di fine: indicarla nel contratto o scegliere una data")
            profile["appointment_end"] = None
            end = contract.end_date
        else:
            end = profile.get("appointment_end")
            if end is None:
                raise ValueError("Indicare la durata dell'incarico: fino alla fine di un contratto oppure fino a una data")
        if start and end and end < start:
            raise ValueError("La fine dell'incarico precede l'inizio")
        data["processor_profile"] = profile
        return data

    async def _attach_contract(self, tenant_id: str, actor: str, party: Party, new_contract: dict | None) -> None:
        """Il contratto registrato insieme al soggetto, a cui si lega la durata dell'incarico."""
        if not new_contract:
            return
        from backend.services.privacypa_supply_chain_service import ContractService, SupplyChainError

        fields = {k: v for k, v in new_contract.items() if k != "title"}
        fields.setdefault("status", ContractStatus.ACTIVE)
        try:
            contract = await ContractService(self.db).create(tenant_id, actor, party.id, new_contract["title"], **fields)
        except SupplyChainError as e:
            raise ValueError(str(e))
        if party.processor_profile is None:
            party.processor_profile = ProcessorProfile(party_id=party.id)
        party.processor_profile.appointment_contract_id = contract.id
        party.processor_profile.appointment_end = None
        await self.db.flush()

    async def set_cessation(self, tenant_id: str, party_id: str, actor: str, ceased_on: date | None, note: str | None) -> Party:
        """Cessazione anticipata dell'incarico (il soggetto passa in archivio) o suo annullamento (ceased_on vuoto)."""
        party = await self.get(tenant_id, party_id)
        if not party:
            raise ValueError("Soggetto non trovato")
        if party.party_type not in APPOINTED_TYPES:
            raise ValueError("Solo responsabili e contitolari hanno un incarico da cessare")
        if party.processor_profile is None:
            party.processor_profile = ProcessorProfile(party_id=party.id)
        old = party.processor_profile.to_dict()
        party.processor_profile.ceased_on = ceased_on
        party.processor_profile.cessation_note = ((note or "").strip() or None) if ceased_on else None
        party.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "party", "cease" if ceased_on else "reinstate", entity_id=party.id, actor_user_id=actor,
                             old_values=old, new_values=party.processor_profile.to_dict())
        return await self.get(tenant_id, party_id)

    async def create(self, tenant_id: str, actor: str, legal_name: str, **fields) -> Party:
        profile_data = fields.pop("processor_profile", None)
        new_contract = fields.pop("new_contract", None)
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
        await self._attach_contract(tenant_id, actor, party, new_contract)
        return await self.get(tenant_id, party.id)

    async def update(self, tenant_id: str, party_id: str, actor: str, **fields) -> Party:
        party = await self.get(tenant_id, party_id)
        if not party:
            raise ValueError("Soggetto non trovato")
        profile_data = fields.pop("processor_profile", None)
        new_contract = fields.pop("new_contract", None)
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
        await self._attach_contract(tenant_id, actor, party, new_contract)
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
