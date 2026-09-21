from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_assets import AssetLifecycle, AssetType, HostingType
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.services.privacypa_asset_service import AssetService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/assets",
    tags=["PrivacyPA - Sistemi"],
    dependencies=[Depends(require_module_view("asset"))],
)


class AssetUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    asset_type: AssetType = AssetType.APPLICATION
    description: str | None = None
    supplier_id: str | None = None
    owner_unit_id: str | None = None
    hosting_type: HostingType = HostingType.ON_PREM
    cloud_service: bool = False
    hosting_country: str | None = None
    data_location_notes: str | None = None
    authentication_type: str | None = None
    mfa_supported: bool | None = None
    logging_supported: bool | None = None
    encryption_at_rest: bool | None = None
    encryption_in_transit: bool | None = None
    backup_enabled: bool | None = None
    lifecycle_status: AssetLifecycle = AssetLifecycle.ACTIVE
    go_live_date: date | None = None
    retirement_date: date | None = None
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.INTERNAL

    @field_validator("confidentiality_level")
    @classmethod
    def _not_highly_restricted(cls, value):
        if value == ConfidentialityLevel.HIGHLY_RESTRICTED:
            raise ValueError("Il livello HIGHLY_RESTRICTED vale per documenti, violazioni, DPIA e progetti")
        return value


@router.get("")
async def list_assets(
    search: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [a.to_dict() for a in await AssetService(db).list_assets(tenant_id, search=search)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_asset(
    body: AssetUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("asset.create")),
    db: AsyncSession = Depends(get_db),
):
    asset = await AssetService(db).create(tenant_id, user.sub or user.email, **body.model_dump())
    return asset.to_dict()


@router.get("/{asset_id}")
async def get_asset(
    asset_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    asset = await AssetService(db).get(tenant_id, asset_id)
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sistema/asset non trovato")
    response.headers["ETag"] = etag_of(asset)
    return asset.to_dict()


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: str,
    body: AssetUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("asset.edit")),
    db: AsyncSession = Depends(get_db),
):
    current = await AssetService(db).get(tenant_id, asset_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sistema/asset non trovato")
    assert_version(current, version)
    try:
        asset = await AssetService(db).update(tenant_id, asset_id, user.sub or user.email, **body.model_dump())
        return asset.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("asset.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await AssetService(db).soft_delete(tenant_id, asset_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
