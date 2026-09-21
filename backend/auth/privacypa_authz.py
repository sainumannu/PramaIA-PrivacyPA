"""
PrivacyPA — Fondamenta di autorizzazione (§9, §10 della specifica funzionale).

Combina l'identità Portal (JWT SSO, backend/auth/portal_jwt.py) con i
ruoli privacy applicativi e il relativo scope organizzativo
(backend/models/privacypa_identity.py) per implementare
RBAC + scope organizzativo + policy record-level, come richiesto da §10.

Global Admin / Tenant Admin (dal JWT Portal) bypassano sempre i controlli
di ruolo privacy locale, coerentemente con la gerarchia a 3 livelli già
in uso in backend/auth/permissions.py.

Quali ruoli hanno quali permessi (§10.1) è definito in privacypa_permissions.py.
"""
from typing import Iterable

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode, ScopeType
from backend.services.privacypa_identity_service import IdentityService
from backend.services.privacypa_organization_service import OrganizationService


def is_portal_global_or_tenant_admin(user: TokenPayload) -> bool:
    return user.tenant_role == "global_admin" or user.tenant_role == "admin"


def require_tenant(user: TokenPayload = Depends(get_current_user)) -> str:
    """Dependency: estrae il tenant_id dal JWT, 400 se l'utente non è associato a un ente."""
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utente non associato a nessun ente (tenant_id mancante nel token)",
        )
    return user.tenant_id


async def get_user_privacy_roles(
    db: AsyncSession, tenant_id: str, user_id: str
) -> list[PrivacyRoleCode]:
    svc = IdentityService(db)
    assignments = await svc.list_user_assignments(tenant_id, user_id, active_only=True)
    return [a.role_code for a in assignments]


def require_privacy_role(*role_codes: PrivacyRoleCode):
    """
    Dependency factory: richiede che l'utente abbia almeno uno dei ruoli
    privacy indicati (scope TENANT), oppure sia Global/Tenant Admin Portal.

    Per controlli con scope organizzativo (UNIT/SUBTREE) su un record
    specifico usare `user_can_access_unit` nel service/router chiamante.
    """
    allowed: set[PrivacyRoleCode] = set(role_codes)

    async def _check(
        user: TokenPayload = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> TokenPayload:
        if is_portal_global_or_tenant_admin(user):
            return user

        tenant_id = require_tenant(user)
        svc = IdentityService(db)
        assignments = await svc.list_user_assignments(tenant_id, user.sub or user.email, active_only=True)
        user_roles = {a.role_code for a in assignments}

        if not (user_roles & allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Richiesto uno dei ruoli privacy: {', '.join(r.value for r in allowed)}",
            )
        return user

    return _check


async def assert_privacy_role(
    user: TokenPayload,
    db: AsyncSession,
    *role_codes: PrivacyRoleCode,
) -> None:
    """Stesso controllo di require_privacy_role, usabile da un handler già autenticato."""
    if is_portal_global_or_tenant_admin(user):
        return
    tenant_id = require_tenant(user)
    svc = IdentityService(db)
    assignments = await svc.list_user_assignments(tenant_id, user.sub or user.email, active_only=True)
    user_roles = {a.role_code for a in assignments}
    allowed = set(role_codes)
    if not (user_roles & allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Richiesto uno dei ruoli privacy: {', '.join(r.value for r in allowed)}",
        )


async def get_accessible_unit_ids(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    is_privileged: bool,
    roles: Iterable[PrivacyRoleCode] | None = None,
) -> set[str] | None:
    """
    Calcola l'insieme di organizational_unit_id visibili all'utente in base
    alle sue assegnazioni di ruolo (§10 esempio: scope su struttura/sottoalbero).

    Se `roles` è indicato, considera solo le assegnazioni con quei ruoli: così
    uno scope ottenuto con un ruolo di sola lettura non abilita scritture o
    approvazioni (il ruolo è legato al proprio scope, §9.3).

    Ritorna None se l'utente ha visibilità sull'intero tenant (scope TENANT,
    Global/Tenant Admin), altrimenti l'insieme esplicito di unit_id ammesse.
    """
    if is_privileged:
        return None

    identity_svc = IdentityService(db)
    org_svc = OrganizationService(db)
    assignments = await identity_svc.list_user_assignments(tenant_id, user_id, active_only=True)
    if roles is not None:
        allowed_roles = set(roles)
        assignments = [a for a in assignments if a.role_code in allowed_roles]

    if any(a.scope_type == ScopeType.TENANT for a in assignments):
        return None

    accessible: set[str] = set()
    for a in assignments:
        if a.scope_type == ScopeType.UNIT and a.organizational_unit_id:
            accessible.add(a.organizational_unit_id)
        elif a.scope_type == ScopeType.SUBTREE and a.organizational_unit_id:
            accessible |= await org_svc.get_subtree_unit_ids(tenant_id, a.organizational_unit_id)

    return accessible


async def assert_effective_role(
    user: TokenPayload,
    db: AsyncSession,
    unit_id: str | None,
    *role_codes: PrivacyRoleCode,
) -> None:
    """
    Come `assert_role_for_unit` ma senza la scorciatoia degli amministratori del Portal: serve un'assegnazione
    di ruolo reale che copra la struttura. Per atti che non si delegano (es. il parere del DPO).
    """
    tenant_id = require_tenant(user)
    accessible = await get_accessible_unit_ids(db, tenant_id, user.sub or user.email, False, roles=role_codes)
    if accessible is None or (unit_id and unit_id in accessible):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Serve il ruolo assegnato sulla struttura del record: gli amministratori non lo sostituiscono",
    )


async def assert_role_for_unit(
    user: TokenPayload,
    db: AsyncSession,
    unit_id: str | None,
    *role_codes: PrivacyRoleCode,
) -> None:
    """
    Richiede che l'utente abbia uno dei ruoli indicati con uno scope che copre
    `unit_id`. A differenza di `assert_privacy_role`, il ruolo deve valere
    proprio su quella struttura (un DPO sull'ufficio B non approva sull'ufficio A).
    """
    if is_portal_global_or_tenant_admin(user):
        return
    tenant_id = require_tenant(user)
    accessible = await get_accessible_unit_ids(
        db, tenant_id, user.sub or user.email, False, roles=role_codes
    )
    if accessible is None:
        return
    if not unit_id or unit_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ruolo non abilitato sulla struttura organizzativa del record",
        )
