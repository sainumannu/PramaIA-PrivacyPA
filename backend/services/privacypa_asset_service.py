from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assets import Asset, AssetRole, ProcessingAsset
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_exists

_EDITABLE = {
    "name", "asset_type", "description", "supplier_id", "owner_unit_id", "hosting_type",
    "cloud_service", "hosting_country", "data_location_notes", "authentication_type",
    "mfa_supported", "logging_supported", "encryption_at_rest", "encryption_in_transit",
    "backup_enabled", "lifecycle_status", "go_live_date", "retirement_date", "confidentiality_level",
}


class AssetService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Asset).where(Asset.tenant_id == tenant_id, Asset.deleted_at.is_(None))

    async def list_assets(self, tenant_id: str, search: str | None = None) -> list[Asset]:
        stmt = self._q(tenant_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(Asset.name.ilike(like) | Asset.code.ilike(like))
        result = await self.db.execute(stmt.order_by(Asset.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, asset_id: str) -> Asset | None:
        result = await self.db.execute(self._q(tenant_id).where(Asset.id == asset_id))
        return result.scalar_one_or_none()

    async def _check_references(self, tenant_id: str, fields: dict) -> None:
        await ensure_exists(self.db, tenant_id, "party", fields.get("supplier_id"))
        await ensure_exists(self.db, tenant_id, "unit", fields.get("owner_unit_id"))

    async def create(self, tenant_id: str, actor: str, name: str, **fields) -> Asset:
        await self._check_references(tenant_id, fields)
        asset = Asset(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, Asset, tenant_id, "AS"),
            name=name.strip(),
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(asset)
        await self.db.flush()
        await self.audit.log(tenant_id, "asset", "create", entity_id=asset.id, actor_user_id=actor, new_values=asset.to_dict())
        await self.db.refresh(asset)
        return asset

    async def update(self, tenant_id: str, asset_id: str, actor: str, **fields) -> Asset:
        asset = await self.get(tenant_id, asset_id)
        if not asset:
            raise ValueError("Sistema/asset non trovato")
        await self._check_references(tenant_id, fields)
        old = asset.to_dict()
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(asset, k, v)
        asset.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "asset", "update", entity_id=asset.id, actor_user_id=actor, old_values=old, new_values=asset.to_dict())
        await self.db.refresh(asset)
        return asset

    async def soft_delete(self, tenant_id: str, asset_id: str, actor: str) -> None:
        asset = await self.get(tenant_id, asset_id)
        if not asset:
            raise ValueError("Sistema/asset non trovato")
        asset.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "asset", "delete", entity_id=asset.id, actor_user_id=actor, old_values=asset.to_dict())

    async def list_without_owner(self, tenant_id: str) -> list[Asset]:
        result = await self.db.execute(
            self._q(tenant_id).where(Asset.owner_unit_id.is_(None))
        )
        return list(result.scalars().all())

    async def list_links(self, processing_activity_id: str) -> list[ProcessingAsset]:
        result = await self.db.execute(
            select(ProcessingAsset).where(ProcessingAsset.processing_activity_id == processing_activity_id)
        )
        return list(result.scalars().all())

    async def set_links(self, processing_activity_id: str, links: list[dict]) -> list[ProcessingAsset]:
        existing = await self.list_links(processing_activity_id)
        for row in existing:
            await self.db.delete(row)
        await self.db.flush()
        created: list[ProcessingAsset] = []
        seen: set[tuple[str, str]] = set()
        for item in links:
            asset_id = item.get("asset_id")
            role = item.get("role") or AssetRole.PROCESSING.value
            if not asset_id:
                continue
            key = (asset_id, role)
            if key in seen:
                continue
            seen.add(key)
            row = ProcessingAsset(
                processing_activity_id=processing_activity_id,
                asset_id=asset_id,
                role=AssetRole(role),
                notes=item.get("notes"),
            )
            self.db.add(row)
            created.append(row)
        await self.db.flush()
        return created
