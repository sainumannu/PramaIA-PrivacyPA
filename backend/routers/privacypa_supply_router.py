"""
PrivacyPA — Contratti (§18.3) e sub-responsabili (§19).

Anagrafiche condivise a livello ente, come i fornitori: i permessi sono quelli del modulo `party`.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_parties import ContractStatus, SubprocessorAuthorization, SubprocessorStatus
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_supply_chain_service import ContractService, SubprocessorService, SupplyChainError
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Filiera"])
_view = [Depends(require_module_view("party"))]


class ContractIn(BaseModel):
    party_id: str
    title: str = Field(..., min_length=1, max_length=255)
    cig: str | None = None
    cup: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    renewal_date: date | None = None
    organizational_unit_id: str | None = None
    status: ContractStatus = ContractStatus.DRAFT
    document_id: str | None = None


class SubprocessorIn(BaseModel):
    processor_party_id: str
    subprocessor_party_id: str
    service_description: str = Field(..., min_length=1)
    authorization_type: SubprocessorAuthorization
    authorization_date: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    countries: list[str] = Field(default_factory=list)
    status: SubprocessorStatus = SubprocessorStatus.TO_REVIEW


def _bad(e: SupplyChainError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ---------------------------------------------------------------- contratti

@router.get("/contracts", dependencies=_view)
async def list_contracts(
    party_id: str | None = None,
    status_filter: ContractStatus | None = Query(None, alias="status"),
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [c.to_dict() for c in await ContractService(db).list(tenant_id, party_id, status_filter)]


@router.post("/contracts", status_code=status.HTTP_201_CREATED)
async def create_contract(
    body: ContractIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.create")),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    try:
        contract = await ContractService(db).create(
            tenant_id, user.sub or user.email, data.pop("party_id"), data.pop("title"), **data
        )
    except SupplyChainError as e:
        raise _bad(e)
    return contract.to_dict()


@router.get("/contracts/{contract_id}", dependencies=_view)
async def get_contract(
    contract_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    contract = await ContractService(db).get(tenant_id, contract_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contratto non trovato")
    response.headers["ETag"] = etag_of(contract)
    return contract.to_dict()


@router.patch("/contracts/{contract_id}")
async def update_contract(
    contract_id: str,
    body: ContractIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = ContractService(db)
    current = await svc.get(tenant_id, contract_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contratto non trovato")
    assert_version(current, version)
    if body.party_id != current.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Il fornitore di un contratto non si può cambiare")
    try:
        return (await svc.update(tenant_id, contract_id, user.sub or user.email, **body.model_dump(exclude={"party_id"}))).to_dict()
    except SupplyChainError as e:
        raise _bad(e)


@router.delete("/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ContractService(db).soft_delete(tenant_id, contract_id, user.sub or user.email)
    except SupplyChainError as e:
        raise _bad(e)


# ---------------------------------------------------------------- sub-responsabili

@router.get("/subprocessors", dependencies=_view)
async def list_subprocessors(
    processor_party_id: str | None = None,
    subprocessor_party_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [s.to_dict() for s in await SubprocessorService(db).list(tenant_id, processor_party_id, subprocessor_party_id)]


@router.get("/subprocessors/tree/{party_id}", dependencies=_view)
async def subprocessor_tree(
    party_id: str,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if await PartyService(db).get(tenant_id, party_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    return await SubprocessorService(db).tree(tenant_id, party_id)


@router.post("/subprocessors", status_code=status.HTTP_201_CREATED)
async def create_subprocessor(
    body: SubprocessorIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.create")),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    try:
        row = await SubprocessorService(db).create(
            tenant_id, user.sub or user.email, data.pop("processor_party_id"), data.pop("subprocessor_party_id"), **data
        )
    except SupplyChainError as e:
        raise _bad(e)
    return row.to_dict()


@router.get("/subprocessors/{subprocessor_id}", dependencies=_view)
async def get_subprocessor(
    subprocessor_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    row = await SubprocessorService(db).get(tenant_id, subprocessor_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sub-responsabile non trovato")
    response.headers["ETag"] = etag_of(row)
    return row.to_dict()


@router.patch("/subprocessors/{subprocessor_id}")
async def update_subprocessor(
    subprocessor_id: str,
    body: SubprocessorIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = SubprocessorService(db)
    current = await svc.get(tenant_id, subprocessor_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sub-responsabile non trovato")
    assert_version(current, version)
    if (body.processor_party_id, body.subprocessor_party_id) != (current.processor_party_id, current.subprocessor_party_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La coppia responsabile / sub-responsabile non si può cambiare")
    try:
        data = body.model_dump(exclude={"processor_party_id", "subprocessor_party_id"})
        return (await svc.update(tenant_id, subprocessor_id, user.sub or user.email, **data)).to_dict()
    except SupplyChainError as e:
        raise _bad(e)


@router.delete("/subprocessors/{subprocessor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subprocessor(
    subprocessor_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await SubprocessorService(db).soft_delete(tenant_id, subprocessor_id, user.sub or user.email)
    except SupplyChainError as e:
        raise _bad(e)
