"""
PrivacyPA — Policy di ente: matrice dei permessi (§10.1) e segregazione dei compiti (§166).
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.privacypa_permissions import get_policy, validate_overrides
from backend.models.privacypa_approvals import TenantPolicy
from backend.services.privacypa_audit_service import AuditService

# Ogni ente decide se applicare la separazione: nessuna regola è imposta a tutti (§166).
DEFAULT_SEGREGATION = {"approver_not_requester": False, "approver_not_editor": False}


class PolicyConflict(Exception):
    """La policy è stata modificata da un altro utente (409)."""

    def __init__(self, current_version: int):
        super().__init__("La policy è stata modificata da un altro utente. Ricaricare e riprovare.")
        self.current_version = current_version


async def get_segregation(db: AsyncSession, tenant_id: str) -> dict[str, bool]:
    policy = await get_policy(db, tenant_id)
    stored = (policy.segregation if policy else None) or {}
    return {key: bool(stored.get(key, default)) for key, default in DEFAULT_SEGREGATION.items()}


class PolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def _locked(self, tenant_id: str, version: int) -> TenantPolicy | None:
        """La riga da modificare, dopo il controllo di versione (0 = non ancora creata)."""
        policy = await get_policy(self.db, tenant_id)
        current = policy.row_version if policy else 0
        if current != version:
            raise PolicyConflict(current)
        return policy

    async def _save(self, tenant_id: str, policy: TenantPolicy | None, actor: str, **fields) -> TenantPolicy:
        try:
            if policy is None:
                policy = TenantPolicy(tenant_id=tenant_id)
                self.db.add(policy)
            for key, value in fields.items():
                setattr(policy, key, value)
            policy.updated_by = actor
            policy.updated_at = datetime.utcnow()
            await self.db.flush()
        except IntegrityError:
            # Due primi salvataggi concorrenti: il secondo trova la riga creata dall'altro.
            raise PolicyConflict(1) from None
        await self.db.refresh(policy)
        return policy

    async def update_permissions(
        self, tenant_id: str, actor: str, overrides: dict[str, list[str]], version: int
    ) -> TenantPolicy:
        clean = validate_overrides(overrides)
        policy = await self._locked(tenant_id, version)
        old = dict(policy.permission_overrides or {}) if policy else {}
        policy = await self._save(tenant_id, policy, actor, permission_overrides=clean)
        await self.audit.log(
            tenant_id, "tenant_policy", "update_permissions", event_type="policy.permissions.updated",
            entity_id=tenant_id, actor_user_id=actor, old_values={"permission_overrides": old},
            new_values={"permission_overrides": clean},
        )
        return policy

    async def update_confidentiality(self, tenant_id: str, actor: str, data: dict, version: int) -> TenantPolicy:
        """Regole per livello di riservatezza e step-up (§134-§136); gli amministratori restano sempre inclusi nei ruoli."""
        from backend.auth.privacypa_confidentiality import validate_config

        clean = validate_config(data)
        policy = await self._locked(tenant_id, version)
        old = dict(policy.confidentiality or {}) if policy else {}
        policy = await self._save(tenant_id, policy, actor, confidentiality=clean)
        await self.audit.log(
            tenant_id, "tenant_policy", "update_confidentiality", event_type="policy.confidentiality.updated",
            entity_id=tenant_id, actor_user_id=actor, old_values={"confidentiality": old}, new_values={"confidentiality": clean},
        )
        return policy

    async def update_deadlines(self, tenant_id: str, actor: str, values: dict[str, int], version: int) -> TenantPolicy:
        from backend.services.privacypa_deadlines import DEADLINE_LIMITS, DEFAULT_DEADLINES, get_deadlines

        unknown = set(values) - set(DEFAULT_DEADLINES)
        if unknown:
            raise ValueError(f"Termini sconosciuti: {', '.join(sorted(unknown))}")
        for key, value in values.items():
            low, high = DEADLINE_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} deve essere un intero tra {low} e {high}")
        policy = await self._locked(tenant_id, version)
        old = await get_deadlines(self.db, tenant_id)
        new = {**old, **values}
        policy = await self._save(tenant_id, policy, actor, deadlines=new)
        await self.audit.log(
            tenant_id, "tenant_policy", "update_deadlines", event_type="policy.deadlines.updated", entity_id=tenant_id,
            actor_user_id=actor, old_values={"deadlines": old}, new_values={"deadlines": new},
        )
        return policy

    async def update_segregation(
        self, tenant_id: str, actor: str, rules: dict[str, bool], version: int
    ) -> TenantPolicy:
        unknown = set(rules) - set(DEFAULT_SEGREGATION)
        if unknown:
            raise ValueError(f"Regole di segregazione sconosciute: {', '.join(sorted(unknown))}")
        policy = await self._locked(tenant_id, version)
        old = await get_segregation(self.db, tenant_id)
        new = {**old, **{k: bool(v) for k, v in rules.items()}}
        policy = await self._save(tenant_id, policy, actor, segregation=new)
        await self.audit.log(
            tenant_id, "tenant_policy", "update_segregation", event_type="policy.segregation.updated",
            entity_id=tenant_id, actor_user_id=actor, old_values={"segregation": old},
            new_values={"segregation": new},
        )
        return policy
