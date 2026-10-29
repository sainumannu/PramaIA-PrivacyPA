from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_parties import AgreementStatus, PartyType, ProcessorStatus
from backend.services.privacypa_party_service import PartyService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/parties",
    tags=["PrivacyPA - Fornitori"],
    dependencies=[Depends(require_module_view("party"))],
)


class ProcessorProfileIn(BaseModel):
    dpo_contact: str | None = None
    security_contact: str | None = None
    privacy_contact: str | None = None
    status: ProcessorStatus = ProcessorStatus.TO_REVIEW
    last_assessment_date: date | None = None
    next_assessment_date: date | None = None
    # Durata dell'incarico: fino alla fine di un contratto del soggetto oppure fino a una data
    appointment_start: date | None = None
    appointment_end: date | None = None
    appointment_contract_id: str | None = None


class NewContractIn(BaseModel):
    """Contratto registrato insieme al soggetto: l'incarico dura quanto il contratto."""

    title: str = Field(..., min_length=1, max_length=255)
    cig: str | None = None
    start_date: date | None = None
    end_date: date


class PartyUpsert(BaseModel):
    """Soggetto esterno. Deve essere identificabile: codice fiscale valido (all'estero un identificativo fiscale).

    Responsabili e contitolari indicano la durata dell'incarico in `processor_profile` (data di fine o contratto
    esistente) oppure con `new_contract`.
    """

    legal_name: str = Field(..., min_length=1, max_length=255)
    party_type: PartyType = PartyType.SUPPLIER
    natural_person: bool = False
    tax_id: str | None = None
    vat_id: str | None = None
    pec: str | None = None
    email: str | None = None
    country_code: str | None = Field(None, max_length=2)
    website: str | None = None
    notes: str | None = None
    processor_profile: ProcessorProfileIn | None = None
    new_contract: NewContractIn | None = None


class CessationIn(BaseModel):
    """Cessazione anticipata dell'incarico; `ceased_on` vuoto la annulla."""

    ceased_on: date | None = None
    note: str | None = Field(None, max_length=2000)


class AgreementUpsert(BaseModel):
    party_id: str
    contract_id: str | None = None
    signed_date: date | None = None
    effective_from: date | None = None
    expiry_date: date | None = None
    document_id: str | None = None
    status: AgreementStatus = AgreementStatus.DRAFT
    instructions_present: bool | None = None
    confidentiality_clause: bool | None = None
    security_clause: bool | None = None
    subprocessor_clause: bool | None = None
    rights_assistance_clause: bool | None = None
    breach_assistance_clause: bool | None = None
    dpia_assistance_clause: bool | None = None
    deletion_return_clause: bool | None = None
    audit_clause: bool | None = None
    assessment_notes: str | None = None


@router.get("")
async def list_parties(
    party_type: PartyType | None = None,
    search: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    svc = PartyService(db)
    return await svc.describe(tenant_id, await svc.list_parties(tenant_id, party_type=party_type, search=search))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_party(
    body: PartyUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.create")),
    db: AsyncSession = Depends(get_db),
):
    svc = PartyService(db)
    try:
        data = await svc.check_input(tenant_id, None, body.model_dump())
        party = await svc.create(tenant_id, user.sub or user.email, **data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return (await svc.describe(tenant_id, [party]))[0]


@router.get("/agreements")
async def list_agreements(
    party_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [a.to_dict() for a in await PartyService(db).list_agreements(tenant_id, party_id)]


@router.post("/agreements", status_code=status.HTTP_201_CREATED)
async def create_agreement(
    body: AgreementUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = body.model_dump()
        party_id = data.pop("party_id")
        agr = await PartyService(db).create_agreement(tenant_id, user.sub or user.email, party_id, **data)
        return agr.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.patch("/agreements/{agreement_id}")
async def update_agreement(
    agreement_id: str,
    body: AgreementUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = body.model_dump(exclude={"party_id"})
        agr = await PartyService(db).update_agreement(tenant_id, agreement_id, user.sub or user.email, **data)
        return agr.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/{party_id}")
async def get_party(
    party_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    svc = PartyService(db)
    party = await svc.get(tenant_id, party_id)
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    response.headers["ETag"] = etag_of(party)
    return (await svc.describe(tenant_id, [party]))[0]


@router.patch("/{party_id}")
async def update_party(
    party_id: str,
    body: PartyUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    svc = PartyService(db)
    current = await svc.get(tenant_id, party_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    assert_version(current, version)
    try:
        data = await svc.check_input(tenant_id, party_id, body.model_dump())
        party = await svc.update(tenant_id, party_id, user.sub or user.email, **data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return (await svc.describe(tenant_id, [party]))[0]


@router.put("/{party_id}/cessation")
async def set_cessation(
    party_id: str,
    body: CessationIn,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Cessazione anticipata dell'incarico di un responsabile o contitolare: il soggetto passa in archivio."""
    svc = PartyService(db)
    current = await svc.get(tenant_id, party_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    assert_version(current, version)
    try:
        party = await svc.set_cessation(tenant_id, party_id, user.sub or user.email, body.ceased_on, body.note)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return (await svc.describe(tenant_id, [party]))[0]


@router.delete("/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_party(
    party_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await PartyService(db).soft_delete(tenant_id, party_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
