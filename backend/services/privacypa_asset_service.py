from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assets import Asset, AssetRole, AssetType, ProcessingAsset
from backend.models.privacypa_risk import ImplementationStatus, MeasureCategory, MeasureImplementation, SecurityMeasure
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import InvalidReference, ensure_exists

# Campi che hanno senso solo per un archivio cartaceo: per gli altri tipi si azzerano.
_PAPER_FIELDS = {
    "location_site", "location_room", "archive_phase", "archive_extent", "custodian_user_id", "environmental_risks",
}
# Stati in cui una misura conta come attuata (spuntata nella scheda)
_DONE = (ImplementationStatus.IMPLEMENTED, ImplementationStatus.PARTIAL)
_EDITABLE = _PAPER_FIELDS | {
    "name", "asset_type", "description", "supplier_id", "owner_unit_id", "hosting_type",
    "cloud_service", "hosting_country", "data_location_notes", "authentication_type",
    "mfa_supported", "logging_supported", "encryption_at_rest", "encryption_in_transit",
    "backup_enabled", "lifecycle_status", "go_live_date", "retirement_date", "confidentiality_level",
    "data_owner_name", "data_owner_role", "data_owner_user_id",
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

    @staticmethod
    def _clean(fields: dict) -> dict:
        if fields.get("asset_type", AssetType.APPLICATION) != AssetType.PAPER_ARCHIVE:
            fields.update({k: None for k in _PAPER_FIELDS})
        elif fields.get("environmental_risks") is not None:
            fields["environmental_risks"] = sorted({getattr(r, "value", r) for r in fields["environmental_risks"]})
        for k in ("location_site", "location_room", "archive_extent", "custodian_user_id"):
            if isinstance(fields.get(k), str):
                fields[k] = fields[k].strip() or None
        return fields

    async def physical_catalog(self, tenant_id: str) -> list[SecurityMeasure]:
        """Le misure fisiche che si possono spuntare: quelle del catalogo globale e quelle dell'ente."""
        result = await self.db.execute(select(SecurityMeasure).where(
            SecurityMeasure.category == MeasureCategory.PHYSICAL,
            (SecurityMeasure.tenant_id == tenant_id) | SecurityMeasure.tenant_id.is_(None),
        ).order_by(SecurityMeasure.tenant_id.is_not(None), SecurityMeasure.name))
        return list(result.scalars().all())

    async def _physical_links(self, tenant_id: str, asset_ids: list[str] | None = None) -> list[MeasureImplementation]:
        stmt = select(MeasureImplementation).join(SecurityMeasure, SecurityMeasure.id == MeasureImplementation.measure_id).where(
            MeasureImplementation.tenant_id == tenant_id, MeasureImplementation.entity_type == "asset",
            SecurityMeasure.category == MeasureCategory.PHYSICAL,
        )
        if asset_ids is not None:
            stmt = stmt.where(MeasureImplementation.entity_id.in_(asset_ids))
        return list((await self.db.execute(stmt)).scalars().all())

    async def describe(self, tenant_id: str, assets: list[Asset]) -> list[dict]:
        """Gli asset come li vede l'interfaccia, con le misure fisiche attuate (anche in parte)."""
        done: dict[str, set[str]] = {}
        for impl in await self._physical_links(tenant_id, [a.id for a in assets]):
            if impl.implementation_status in _DONE:
                done.setdefault(impl.entity_id, set()).add(impl.measure_id)
        return [{**a.to_dict(), "physical_measure_ids": sorted(done.get(a.id, ()))} for a in assets]

    async def _set_physical_measures(self, tenant_id: str, actor: str, asset: Asset, measure_ids: list[str] | None) -> None:
        """Le misure fisiche spuntate risultano attuate; quelle tolte, se erano attuate, si scollegano.

        Le misure pianificate o non applicabili e quelle di altre categorie (collegate dai rischi o dall'API) restano.
        """
        if measure_ids is None:
            return
        wanted = {m for m in measure_ids if m}
        if wanted - {m.id for m in await self.physical_catalog(tenant_id)}:
            raise InvalidReference("Misura di sicurezza fisica non trovata")
        current = await self._physical_links(tenant_id, [asset.id])
        before = sorted({i.measure_id for i in current if i.implementation_status in _DONE})
        kept: set[str] = set()
        for impl in current:
            if impl.measure_id in wanted and impl.measure_id not in kept:
                if impl.implementation_status not in _DONE:
                    impl.implementation_status = ImplementationStatus.IMPLEMENTED
                kept.add(impl.measure_id)
            elif impl.implementation_status in _DONE or impl.measure_id in kept:
                await self.db.delete(impl)
        for measure_id in sorted(wanted - kept):
            self.db.add(MeasureImplementation(
                tenant_id=tenant_id, measure_id=measure_id, entity_type="asset", entity_id=asset.id,
                implementation_status=ImplementationStatus.IMPLEMENTED,
            ))
        await self.db.flush()
        if before != sorted(wanted):
            await self.audit.log(tenant_id, "asset", "measures", entity_id=asset.id, actor_user_id=actor,
                                 old_values={"physical_measure_ids": before}, new_values={"physical_measure_ids": sorted(wanted)})

    async def create(self, tenant_id: str, actor: str, name: str, **fields) -> Asset:
        measure_ids = fields.pop("physical_measure_ids", None)
        fields = self._clean(fields)
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
        await self._set_physical_measures(tenant_id, actor, asset, measure_ids)
        await self.db.refresh(asset)
        return asset

    async def update(self, tenant_id: str, asset_id: str, actor: str, **fields) -> Asset:
        asset = await self.get(tenant_id, asset_id)
        if not asset:
            raise ValueError("Sistema/asset non trovato")
        measure_ids = fields.pop("physical_measure_ids", None)
        fields = self._clean(fields)
        await self._check_references(tenant_id, fields)
        await self._set_physical_measures(tenant_id, actor, asset, measure_ids)
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
