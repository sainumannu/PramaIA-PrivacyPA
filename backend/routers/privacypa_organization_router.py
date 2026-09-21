"""
PrivacyPA — Organizzazione: CRUD ente e strutture organizzative (§8).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_permission
from backend.db.database import get_db
from backend.models.privacypa_organization import OrganizationalUnitType
from backend.services.privacypa_organization_service import OrganizationService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Organizzazione"])


class OrganizationUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    tax_code: str | None = None
    ipa_code: str | None = None
    pec: str | None = None
    address: dict | None = None
    dpo_name: str | None = None
    dpo_contact: str | None = None


class OrganizationalUnitCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    unit_type: OrganizationalUnitType
    parent_id: str | None = None
    manager_user_id: str | None = None
    valid_from: date | None = None


class OrganizationalUnitUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    unit_type: OrganizationalUnitType | None = None
    parent_id: str | None = None
    manager_user_id: str | None = None
    active: bool | None = None
    valid_to: date | None = None


@router.get("/organization")
async def get_organization(
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    svc = OrganizationService(db)
    org = await svc.get_organization(tenant_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ente non ancora configurato")
    return org.to_dict()


@router.put("/organization")
async def upsert_organization(
    body: OrganizationUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("organization.administer")),
    db: AsyncSession = Depends(get_db),
):
    svc = OrganizationService(db)
    org = await svc.create_or_update_organization(
        tenant_id, actor_user_id=user.sub or user.email, **body.model_dump()
    )
    return org.to_dict()


@router.get("/organizational-units")
async def list_units(
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    svc = OrganizationService(db)
    units = await svc.list_units(tenant_id)
    return [u.to_dict() for u in units]


@router.post("/organizational-units", status_code=status.HTTP_201_CREATED)
async def create_unit(
    body: OrganizationalUnitCreate,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("organization.administer")),
    db: AsyncSession = Depends(get_db),
):
    svc = OrganizationService(db)
    org = await svc.get_organization(tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configurare prima l'ente (PUT /api/privacypa/organization)",
        )
    try:
        fields = body.model_dump(exclude_none=True)
        unit = await svc.create_unit(
            tenant_id, organization_id=org.id, actor_user_id=user.sub or user.email, **fields
        )
        return unit.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/organizational-units/{unit_id}")
async def update_unit(
    unit_id: str,
    body: OrganizationalUnitUpdate,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("organization.administer")),
    db: AsyncSession = Depends(get_db),
):
    svc = OrganizationService(db)
    current = await svc.get_unit(tenant_id, unit_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Struttura non trovata")
    assert_version(current, version)
    try:
        fields = body.model_dump(exclude_none=True)
        unit = await svc.update_unit(tenant_id, unit_id, actor_user_id=user.sub or user.email, **fields)
        return unit.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
