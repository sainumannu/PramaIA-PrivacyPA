from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_organization import Organization, OrganizationalUnit
from backend.services.privacypa_audit_service import AuditService


class OrganizationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    # ------------------------------------------------------------------
    # Organization (l'ente)
    # ------------------------------------------------------------------

    async def get_organization(self, tenant_id: str) -> Organization | None:
        result = await self.db.execute(
            select(Organization).where(Organization.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def create_or_update_organization(
        self, tenant_id: str, actor_user_id: str, **fields
    ) -> Organization:
        org = await self.get_organization(tenant_id)
        if org is None:
            org = Organization(tenant_id=tenant_id, **fields)
            self.db.add(org)
            await self.db.flush()
            await self.audit.log(
                tenant_id=tenant_id,
                entity_type="organization",
                entity_id=org.id,
                action="create",
                actor_user_id=actor_user_id,
                new_values=org.to_dict(),
            )
        else:
            old_values = org.to_dict()
            for key, value in fields.items():
                setattr(org, key, value)
            await self.db.flush()
            await self.audit.log(
                tenant_id=tenant_id,
                entity_type="organization",
                entity_id=org.id,
                action="update",
                actor_user_id=actor_user_id,
                old_values=old_values,
                new_values=org.to_dict(),
            )
        await self.db.refresh(org)
        return org

    # ------------------------------------------------------------------
    # Organizational units (struttura gerarchica)
    # ------------------------------------------------------------------

    async def list_units(self, tenant_id: str) -> list[OrganizationalUnit]:
        result = await self.db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    async def get_unit(self, tenant_id: str, unit_id: str) -> OrganizationalUnit | None:
        result = await self.db.execute(
            select(OrganizationalUnit).where(
                OrganizationalUnit.tenant_id == tenant_id,
                OrganizationalUnit.id == unit_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_unit(
        self,
        tenant_id: str,
        organization_id: str,
        actor_user_id: str,
        **fields,
    ) -> OrganizationalUnit:
        if fields.get("parent_id"):
            parent = await self.get_unit(tenant_id, fields["parent_id"])
            if parent is None:
                raise ValueError(f"Struttura padre '{fields['parent_id']}' non trovata")

        unit = OrganizationalUnit(tenant_id=tenant_id, organization_id=organization_id, **fields)
        self.db.add(unit)
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="organizational_unit",
            entity_id=unit.id,
            action="create",
            actor_user_id=actor_user_id,
            new_values=unit.to_dict(),
        )
        await self.db.refresh(unit)
        return unit

    async def update_unit(
        self, tenant_id: str, unit_id: str, actor_user_id: str, **fields
    ) -> OrganizationalUnit:
        unit = await self.get_unit(tenant_id, unit_id)
        if unit is None:
            raise ValueError(f"Struttura '{unit_id}' non trovata")

        if fields.get("parent_id") == unit_id:
            raise ValueError("Una struttura non può essere padre di se stessa")

        old_values = unit.to_dict()
        for key, value in fields.items():
            setattr(unit, key, value)
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="organizational_unit",
            entity_id=unit.id,
            action="update",
            actor_user_id=actor_user_id,
            old_values=old_values,
            new_values=unit.to_dict(),
        )
        await self.db.refresh(unit)
        return unit

    async def get_subtree_unit_ids(self, tenant_id: str, root_unit_id: str) -> set[str]:
        """Espande un sottoalbero organizzativo (per lo scope_type=SUBTREE, §9.3)."""
        units = await self.list_units(tenant_id)
        children_by_parent: dict[str | None, list[str]] = {}
        for u in units:
            children_by_parent.setdefault(u.parent_id, []).append(u.id)

        result: set[str] = {root_unit_id}
        stack = [root_unit_id]
        while stack:
            current = stack.pop()
            for child_id in children_by_parent.get(current, []):
                if child_id not in result:
                    result.add(child_id)
                    stack.append(child_id)
        return result
