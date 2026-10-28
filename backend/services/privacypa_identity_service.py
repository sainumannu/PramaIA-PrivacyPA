from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_identity import (
    PrivacyRoleCatalog,
    UserRoleAssignment,
    PrivacyRoleCode,
    ScopeType,
)
from backend.services.privacypa_audit_service import AuditService
from backend.utils.request_context import is_service_account


class IdentityService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def list_role_catalog(self) -> list[PrivacyRoleCatalog]:
        result = await self.db.execute(select(PrivacyRoleCatalog))
        return list(result.scalars().all())

    async def list_user_assignments(
        self, tenant_id: str, user_id: str, *, active_only: bool = True
    ) -> list[UserRoleAssignment]:
        query = select(UserRoleAssignment).where(
            UserRoleAssignment.tenant_id == tenant_id,
            UserRoleAssignment.user_id == user_id,
        )
        result = await self.db.execute(query)
        assignments = list(result.scalars().all())
        if not active_only:
            return assignments
        now = datetime.utcnow()
        return [
            a for a in assignments
            if a.valid_from <= now and (a.valid_to is None or a.valid_to >= now)
        ]

    async def assign_role(
        self,
        tenant_id: str,
        user_id: str,
        role_code: PrivacyRoleCode,
        granted_by: str,
        scope_type: ScopeType = ScopeType.TENANT,
        organizational_unit_id: str | None = None,
        valid_to: datetime | None = None,
    ) -> UserRoleAssignment:
        if scope_type in (ScopeType.UNIT, ScopeType.SUBTREE) and not organizational_unit_id:
            raise ValueError(f"scope_type={scope_type.value} richiede organizational_unit_id")
        if role_code == PrivacyRoleCode.INTEGRATION_HELPDESK:
            # Ruolo tecnico dell'HelpDesk: mai a una persona, sempre sull'intero ente.
            if not is_service_account(user_id):
                raise ValueError(f"Il ruolo {role_code.value} si assegna solo a un account di servizio del Portal (app:...)")
            if scope_type != ScopeType.TENANT:
                raise ValueError(f"Il ruolo {role_code.value} vale sull'intero ente")

        role = await self.db.get(PrivacyRoleCatalog, role_code)
        if role is None:
            raise ValueError(f"Ruolo privacy '{role_code}' non trovato nel catalogo")

        existing = await self.db.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.tenant_id == tenant_id,
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.role_code == role_code,
                UserRoleAssignment.organizational_unit_id == organizational_unit_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Assegnazione già esistente per questo utente/ruolo/scope")

        assignment = UserRoleAssignment(
            tenant_id=tenant_id,
            user_id=user_id,
            role_code=role_code,
            scope_type=scope_type,
            organizational_unit_id=organizational_unit_id,
            granted_by=granted_by,
            valid_to=valid_to,
        )
        self.db.add(assignment)
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="user_role_assignment",
            entity_id=assignment.id,
            action="grant",
            actor_user_id=granted_by,
            new_values=assignment.to_dict(),
        )
        await self.db.refresh(assignment)
        return assignment

    async def revoke_role(
        self, tenant_id: str, assignment_id: str, revoked_by: str
    ) -> bool:
        assignment = await self.db.get(UserRoleAssignment, assignment_id)
        if assignment is None or assignment.tenant_id != tenant_id:
            return False

        old_values = assignment.to_dict()
        await self.db.delete(assignment)
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="user_role_assignment",
            entity_id=assignment_id,
            action="revoke",
            actor_user_id=revoked_by,
            old_values=old_values,
        )
        return True
