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
from backend.utils.request_context import active_role_binding, is_service_account

# Ruoli degli account di servizio: un amministratore non li sceglie come ruolo attivo.
_SERVICE_ROLES = {PrivacyRoleCode.INTEGRATION.value, PrivacyRoleCode.INTEGRATION_HELPDESK.value}


def _virtual_assignment(tenant_id: str, user_id: str, role_code: str) -> UserRoleAssignment:
    """Ruolo scelto da un amministratore del Portal senza averlo assegnato: vale sull'intero ente, non si salva."""
    return UserRoleAssignment(
        id=f"active-role:{role_code}", tenant_id=tenant_id, user_id=user_id, role_code=PrivacyRoleCode(role_code),
        scope_type=ScopeType.TENANT, organizational_unit_id=None, valid_from=datetime(2000, 1, 1), granted_by=user_id,
    )


def selectable_roles(portal_admin: bool, assigned: set[str]) -> list[str]:
    """Ruoli tra cui si sceglie il ruolo attivo: i propri, o per un amministratore del Portal tutti i ruoli privacy."""
    if portal_admin:
        return [r.value for r in PrivacyRoleCode if r.value not in _SERVICE_ROLES]
    return sorted(assigned)


class IdentityService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def list_role_catalog(self) -> list[PrivacyRoleCatalog]:
        result = await self.db.execute(select(PrivacyRoleCatalog))
        return list(result.scalars().all())

    async def list_user_assignments(
        self, tenant_id: str, user_id: str, *, active_only: bool = True, all_roles: bool = False,
        real_only: bool = False,
    ) -> list[UserRoleAssignment]:
        """
        Con un ruolo attivo nella richiesta (X-Active-Role) le assegnazioni del richiedente si limitano a quel ruolo,
        salvo `all_roles`. Un amministratore del Portal che sceglie un ruolo non assegnato lo esercita sull'intero
        ente (assegnazione virtuale, mai salvata), salvo `real_only` (atti che non si delegano, es. il parere del DPO).
        """
        query = select(UserRoleAssignment).where(
            UserRoleAssignment.tenant_id == tenant_id,
            UserRoleAssignment.user_id == user_id,
        )
        result = await self.db.execute(query)
        assignments = list(result.scalars().all())
        active = None if all_roles else active_role_binding()
        if active is not None and active.user_id in (None, user_id):
            assignments = [a for a in assignments if a.role_code.value == active.code]
            if not assignments and not real_only and active.portal_admin and active.code not in _SERVICE_ROLES:
                assignments = [_virtual_assignment(tenant_id, user_id, active.code)]
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
        if scope_type == ScopeType.RECORD:
            # Non c'è ancora il record a cui legarlo e i permessi lo ignorerebbero: il ruolo non darebbe accesso a nulla.
            raise ValueError("Un ruolo su un singolo record non è ancora gestito: scegliere l'ente, una struttura o un sottoalbero")
        if scope_type in (ScopeType.UNIT, ScopeType.SUBTREE) and not organizational_unit_id:
            raise ValueError(f"scope_type={scope_type.value} richiede organizational_unit_id")
        if scope_type == ScopeType.TENANT:
            organizational_unit_id = None
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
