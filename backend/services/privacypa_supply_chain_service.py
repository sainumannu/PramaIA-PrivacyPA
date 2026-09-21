"""
PrivacyPA — Contratti (§18.3) e sub-responsabili (§19).

Contratti e sub-responsabili sono anagrafiche di filiera condivise a livello ente, come i fornitori
(§2.1): non hanno uno scope per struttura.
"""
import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_documents import Document
from backend.models.privacypa_organization import OrganizationalUnit
from backend.models.privacypa_parties import (
    Contract,
    ContractStatus,
    Party,
    ProcessingProcessor,
    ProcessorAgreement,
    Subprocessor,
    SubprocessorStatus,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code

_CONTRACT_FIELDS = {
    "title", "cig", "cup", "start_date", "end_date", "renewal_date",
    "organizational_unit_id", "status", "document_id",
}
_SUBPROCESSOR_FIELDS = {
    "service_description", "authorization_type", "authorization_date", "valid_from", "valid_to",
    "countries", "status",
}
_CIG = re.compile(r"^[A-Z0-9]{10}$")
_CUP = re.compile(r"^[A-Z0-9]{15}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
MAX_TREE_DEPTH = 10


class SupplyChainError(ValueError):
    """Dati non validi o riferimenti non coerenti (400)."""


def _ordered(start: date | None, end: date | None, label: str) -> None:
    if start and end and end < start:
        raise SupplyChainError(f"{label}: la data finale precede quella iniziale")


class ContractService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Contract).where(Contract.tenant_id == tenant_id, Contract.deleted_at.is_(None))

    async def list(
        self, tenant_id: str, party_id: str | None = None, status: ContractStatus | None = None
    ) -> list[Contract]:
        stmt = self._q(tenant_id)
        if party_id:
            stmt = stmt.where(Contract.party_id == party_id)
        if status:
            stmt = stmt.where(Contract.status == status)
        return list((await self.db.execute(stmt.order_by(Contract.code))).scalars().all())

    async def get(self, tenant_id: str, contract_id: str) -> Contract | None:
        return (await self.db.execute(self._q(tenant_id).where(Contract.id == contract_id))).scalar_one_or_none()

    async def _normalize(self, tenant_id: str, data: dict) -> dict:
        for key, pattern, label in (("cig", _CIG, "CIG (10 caratteri)"), ("cup", _CUP, "CUP (15 caratteri)")):
            value = (data.get(key) or "").strip().upper() or None
            if value and not pattern.match(value):
                raise SupplyChainError(f"{label} non valido")
            data[key] = value
        _ordered(data.get("start_date"), data.get("end_date"), "Durata del contratto")
        _ordered(data.get("start_date"), data.get("renewal_date"), "Data di rinnovo")
        unit_id = data.get("organizational_unit_id")
        if unit_id:
            unit = (
                await self.db.execute(
                    select(OrganizationalUnit.id).where(
                        OrganizationalUnit.id == unit_id, OrganizationalUnit.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if unit is None:
                raise SupplyChainError("Struttura organizzativa non trovata")
        document_id = data.get("document_id")
        if document_id:
            doc = (
                await self.db.execute(
                    select(Document.id).where(
                        Document.id == document_id, Document.tenant_id == tenant_id, Document.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if doc is None:
                raise SupplyChainError("Documento non trovato")
        return data

    async def create(self, tenant_id: str, actor: str, party_id: str, title: str, **fields) -> Contract:
        party = (
            await self.db.execute(
                select(Party.id).where(Party.id == party_id, Party.tenant_id == tenant_id, Party.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if party is None:
            raise SupplyChainError("Soggetto non trovato")
        data = await self._normalize(
            tenant_id, {"title": title.strip(), **{k: v for k, v in fields.items() if k in _CONTRACT_FIELDS}}
        )
        contract = Contract(
            tenant_id=tenant_id,
            party_id=party_id,
            code=await next_prefixed_code(self.db, Contract, tenant_id, "CT"),
            **data,
        )
        self.db.add(contract)
        await self.db.flush()
        await self.audit.log(tenant_id, "contract", "create", entity_id=contract.id, actor_user_id=actor, new_values=contract.to_dict())
        return contract

    async def update(self, tenant_id: str, contract_id: str, actor: str, **fields) -> Contract:
        contract = await self.get(tenant_id, contract_id)
        if contract is None:
            raise SupplyChainError("Contratto non trovato")
        old = contract.to_dict()
        merged = {k: getattr(contract, k) for k in _CONTRACT_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _CONTRACT_FIELDS})
        for key, value in (await self._normalize(tenant_id, merged)).items():
            setattr(contract, key, value)
        contract.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "contract", "update", entity_id=contract.id, actor_user_id=actor,
            old_values=old, new_values=contract.to_dict(),
        )
        return contract

    async def soft_delete(self, tenant_id: str, contract_id: str, actor: str) -> None:
        contract = await self.get(tenant_id, contract_id)
        if contract is None:
            raise SupplyChainError("Contratto non trovato")
        in_use = (
            await self.db.execute(select(ProcessingProcessor.id).where(ProcessingProcessor.contract_id == contract_id))
        ).first() or (
            await self.db.execute(select(ProcessorAgreement.id).where(ProcessorAgreement.contract_id == contract_id))
        ).first()
        if in_use:
            raise SupplyChainError("Il contratto è collegato a trattamenti o atti art. 28: scollegarlo prima di eliminarlo")
        contract.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "contract", "delete", entity_id=contract.id, actor_user_id=actor, old_values=contract.to_dict())


class SubprocessorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Subprocessor).where(Subprocessor.tenant_id == tenant_id, Subprocessor.deleted_at.is_(None))

    async def list(
        self, tenant_id: str, processor_party_id: str | None = None, subprocessor_party_id: str | None = None
    ) -> list[Subprocessor]:
        stmt = self._q(tenant_id)
        if processor_party_id:
            stmt = stmt.where(Subprocessor.processor_party_id == processor_party_id)
        if subprocessor_party_id:
            stmt = stmt.where(Subprocessor.subprocessor_party_id == subprocessor_party_id)
        return list((await self.db.execute(stmt.order_by(Subprocessor.created_at, Subprocessor.id))).scalars().all())

    async def get(self, tenant_id: str, subprocessor_id: str) -> Subprocessor | None:
        return (await self.db.execute(self._q(tenant_id).where(Subprocessor.id == subprocessor_id))).scalar_one_or_none()

    async def _party_exists(self, tenant_id: str, party_id: str) -> bool:
        return (
            await self.db.execute(
                select(Party.id).where(Party.id == party_id, Party.tenant_id == tenant_id, Party.deleted_at.is_(None))
            )
        ).scalar_one_or_none() is not None

    async def _reaches(self, tenant_id: str, start: str, target: str) -> bool:
        """True se da `start` si arriva a `target` seguendo i sub-responsabili (rilevazione di cicli)."""
        seen, frontier = {start}, [start]
        while frontier:
            current = frontier.pop()
            if current == target:
                return True
            for edge in await self.list(tenant_id, processor_party_id=current):
                if edge.subprocessor_party_id not in seen:
                    seen.add(edge.subprocessor_party_id)
                    frontier.append(edge.subprocessor_party_id)
        return False

    @staticmethod
    def _normalize(data: dict) -> dict:
        countries = []
        for c in data.get("countries") or []:
            code = str(c).strip().upper()
            if not _COUNTRY.match(code):
                raise SupplyChainError(f"Codice paese non valido: {c}")
            if code not in countries:
                countries.append(code)
        data["countries"] = countries
        if not (data.get("service_description") or "").strip():
            raise SupplyChainError("La descrizione del servizio è obbligatoria")
        _ordered(data.get("valid_from"), data.get("valid_to"), "Validità")
        if data.get("status") == SubprocessorStatus.ACTIVE and not data.get("authorization_date"):
            raise SupplyChainError("Un sub-responsabile attivo richiede la data di autorizzazione del titolare")
        return data

    async def create(
        self, tenant_id: str, actor: str, processor_party_id: str, subprocessor_party_id: str, **fields
    ) -> Subprocessor:
        if processor_party_id == subprocessor_party_id:
            raise SupplyChainError("Un responsabile non può essere sub-responsabile di sé stesso")
        for party_id in (processor_party_id, subprocessor_party_id):
            if not await self._party_exists(tenant_id, party_id):
                raise SupplyChainError("Soggetto non trovato")
        existing = (
            await self.db.execute(
                self._q(tenant_id).where(
                    Subprocessor.processor_party_id == processor_party_id,
                    Subprocessor.subprocessor_party_id == subprocessor_party_id,
                )
            )
        ).first()
        if existing:
            raise SupplyChainError("Il sub-responsabile è già registrato per questo responsabile")
        if await self._reaches(tenant_id, subprocessor_party_id, processor_party_id):
            raise SupplyChainError("La relazione creerebbe un ciclo nella catena dei sub-responsabili")
        data = self._normalize({k: v for k, v in fields.items() if k in _SUBPROCESSOR_FIELDS})
        row = Subprocessor(
            tenant_id=tenant_id, processor_party_id=processor_party_id, subprocessor_party_id=subprocessor_party_id, **data
        )
        self.db.add(row)
        await self.db.flush()
        await self.audit.log(tenant_id, "subprocessor", "create", entity_id=row.id, actor_user_id=actor, new_values=row.to_dict())
        return row

    async def update(self, tenant_id: str, subprocessor_id: str, actor: str, **fields) -> Subprocessor:
        row = await self.get(tenant_id, subprocessor_id)
        if row is None:
            raise SupplyChainError("Sub-responsabile non trovato")
        old = row.to_dict()
        merged = {k: getattr(row, k) for k in _SUBPROCESSOR_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _SUBPROCESSOR_FIELDS})
        for key, value in self._normalize(merged).items():
            setattr(row, key, value)
        row.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "subprocessor", "update", entity_id=row.id, actor_user_id=actor,
            old_values=old, new_values=row.to_dict(),
        )
        return row

    async def soft_delete(self, tenant_id: str, subprocessor_id: str, actor: str) -> None:
        row = await self.get(tenant_id, subprocessor_id)
        if row is None:
            raise SupplyChainError("Sub-responsabile non trovato")
        row.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "subprocessor", "delete", entity_id=row.id, actor_user_id=actor, old_values=row.to_dict())

    async def tree(self, tenant_id: str, root_party_id: str) -> dict:
        """Albero dei sub-responsabili di un responsabile (§19); la profondità è limitata."""
        names = {
            p.id: p.legal_name
            for p in (
                await self.db.execute(select(Party).where(Party.tenant_id == tenant_id, Party.deleted_at.is_(None)))
            ).scalars().all()
        }
        edges: dict[str, list[Subprocessor]] = {}
        for row in await self.list(tenant_id):
            edges.setdefault(row.processor_party_id, []).append(row)

        def build(party_id: str, depth: int, path: frozenset) -> dict:
            children = []
            if depth < MAX_TREE_DEPTH:
                for row in edges.get(party_id, []):
                    if row.subprocessor_party_id in path:
                        continue
                    children.append({
                        **row.to_dict(),
                        "party_name": names.get(row.subprocessor_party_id),
                        "children": build(row.subprocessor_party_id, depth + 1, path | {row.subprocessor_party_id})["children"],
                    })
            return {"party_id": party_id, "party_name": names.get(party_id), "children": children}

        return build(root_party_id, 0, frozenset({root_party_id}))
