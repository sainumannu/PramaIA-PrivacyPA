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


class PartyUpsert(BaseModel):
    legal_name: str = Field(..., min_length=1, max_length=255)
    party_type: PartyType = PartyType.SUPPLIER
    tax_id: str | None = None
    vat_id: str | None = None
    pec: str | None = None
    email: str | None = None
    country_code: str | None = Field(None, max_length=2)
    website: str | None = None
    notes: str | None = None
    processor_profile: ProcessorProfileIn | None = None


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
    items = await PartyService(db).list_parties(tenant_id, party_type=party_type, search=search)
    return [p.to_dict(include_profile=True) for p in items]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_party(
    body: PartyUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("party.create")),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    if data.get("processor_profile"):
        data["processor_profile"] = body.processor_profile.model_dump() if body.processor_profile else None
    party = await PartyService(db).create(tenant_id, user.sub or user.email, **data)
    return party.to_dict(include_profile=True)


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
    party = await PartyService(db).get(tenant_id, party_id)
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    response.headers["ETag"] = etag_of(party)
    return party.to_dict(include_profile=True)


@router.patch("/{party_id}")
async def update_party(
    party_id: str,
    body: PartyUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("party.edit")),
    db: AsyncSession = Depends(get_db),
):
    current = await PartyService(db).get(tenant_id, party_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Soggetto non trovato")
    assert_version(current, version)
    try:
        data = body.model_dump()
        if data.get("processor_profile"):
            data["processor_profile"] = body.processor_profile.model_dump()
        party = await PartyService(db).update(tenant_id, party_id, user.sub or user.email, **data)
        return party.to_dict(include_profile=True)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


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
