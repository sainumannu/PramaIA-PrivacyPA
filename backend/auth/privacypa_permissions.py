"""
PrivacyPA — Matrice dei permessi (§10.1, AC-REG-003).

Ogni permesso è `<modulo>.<azione>` (es. `processing.approve`) e corrisponde a un insieme di ruoli
(§9.2). I default riproducono le regole storiche dell'applicazione; ogni ente può sostituire i ruoli di
un permesso (`TenantPolicy.permission_overrides`), con due garanzie: SUPER_ADMIN e TENANT_ADMIN restano
sempre inclusi e i permessi di amministrazione delle policy non sono modificabili.

Il ruolo resta legato al proprio scope: per i controlli su un record usare
`assert_permission_for_unit`, non un controllo di ruolo a livello ente.
"""
from typing import Iterable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import (
    assert_role_for_unit,
    is_portal_global_or_tenant_admin,
    tenant_of,
)
from backend.db.database import get_db
from backend.models.privacypa_approvals import TenantPolicy
from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.services.privacypa_identity_service import IdentityService

ACTIONS = ("view", "create", "edit", "submit", "approve", "reopen", "archive", "export", "delete", "administer")

ADMINS = (R.SUPER_ADMIN, R.TENANT_ADMIN)
GOVERNANCE = ADMINS + (R.PRIVACY_MANAGER, R.DPO)
# Registro trattamenti: storicamente senza ICT e RTD.
REGISTER_WRITERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.OPERATOR)
WRITERS = REGISTER_WRITERS + (R.ICT, R.RTD)
# Gli account di servizio non sono utenti: vedono solo i moduli a cui sono destinati (`integration.*` per i connettori,
# `access.*` e il registro degli autorizzati per l'HelpDesk), mai gli altri. Non si aggiungono ad altri permessi per ente.
SERVICE_ROLES = (R.INTEGRATION, R.INTEGRATION_HELPDESK)
EVERYONE = tuple(r for r in R if r not in SERVICE_ROLES)
IMPORTERS = GOVERNANCE
AUDIT_READERS = GOVERNANCE + (R.AUDITOR,)
# Violazioni e richieste degli interessati sono dati sensibili: non li vedono operatori e sola lettura (§10).
BREACH_VIEWERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.ICT, R.RTD, R.AUDITOR)
BREACH_WRITERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.ICT, R.RTD)
DSR_VIEWERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.OPERATOR, R.AUDITOR)
DSR_WRITERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.OPERATOR)
AUDIT_PLANNERS = GOVERNANCE + (R.AUDITOR,)
TRAINING_MANAGERS = GOVERNANCE + (R.PRIVACY_REFERENT,)
CUSTOMFIELD_ADMINS = ADMINS + (R.PRIVACY_MANAGER,)
COMMENTERS = tuple(r for r in EVERYONE if r != R.READ_ONLY)
AUTHORIZATION_WRITERS = GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT, R.INTEGRATION_HELPDESK)
AUTHORIZATION_VIEWERS = AUTHORIZATION_WRITERS + (R.ICT, R.RTD, R.AUDITOR)
# Profili di accesso: li gestisce il privacy manager, non chi registra le concessioni (l'HelpDesk non allarga un profilo).
PROFILE_MANAGERS = ADMINS + (R.PRIVACY_MANAGER,)
DPO_VIEWERS = ADMINS + (R.DPO, R.PRIVACY_MANAGER, R.AUDITOR)
QUERY_EXPORTERS = AUDIT_READERS + (R.MANAGER,)
INTEGRATION_READERS = GOVERNANCE + (R.INTEGRATION,)
INTEGRATION_WRITERS = ADMINS + (R.INTEGRATION,)


def _module(view: Iterable[R], **actions: Iterable[R]) -> dict[str, tuple[R, ...]]:
    rows = {"view": tuple(view)}
    rows.update({name: tuple(roles) for name, roles in actions.items()})
    return rows


# modulo -> azione -> ruoli di default
DEFAULT_MODULES: dict[str, dict[str, tuple[R, ...]]] = {
    "processing": _module(
        EVERYONE,
        create=REGISTER_WRITERS, edit=REGISTER_WRITERS, submit=REGISTER_WRITERS, reopen=REGISTER_WRITERS,
        delete=REGISTER_WRITERS, approve=GOVERNANCE, archive=GOVERNANCE, export=EVERYONE,
    ),
    "dpia": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS, approve=GOVERNANCE),
    "asset": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "party": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "notice": _module(EVERYONE, create=REGISTER_WRITERS, edit=REGISTER_WRITERS, submit=REGISTER_WRITERS,
                       delete=REGISTER_WRITERS, approve=GOVERNANCE, archive=GOVERNANCE),
    "dataflow": _module(EVERYONE, create=REGISTER_WRITERS, edit=REGISTER_WRITERS, delete=REGISTER_WRITERS),
    "legalref": _module(EVERYONE, create=REGISTER_WRITERS, edit=REGISTER_WRITERS, delete=GOVERNANCE),
    "action": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=GOVERNANCE),
    "breach": _module(BREACH_VIEWERS, create=BREACH_WRITERS, edit=BREACH_WRITERS, approve=GOVERNANCE, delete=GOVERNANCE),
    "dsr": _module(DSR_VIEWERS, create=DSR_WRITERS, edit=DSR_WRITERS, approve=GOVERNANCE, delete=GOVERNANCE),
    "auditplan": _module(GOVERNANCE + (R.AUDITOR, R.MANAGER), create=AUDIT_PLANNERS, edit=AUDIT_PLANNERS, delete=GOVERNANCE),
    "tag": _module(EVERYONE, create=REGISTER_WRITERS, edit=GOVERNANCE, delete=GOVERNANCE, assign=WRITERS),
    "customfield": _module(EVERYONE, create=CUSTOMFIELD_ADMINS, edit=CUSTOMFIELD_ADMINS, delete=CUSTOMFIELD_ADMINS),
    "comment": _module(EVERYONE, create=COMMENTERS, moderate=GOVERNANCE),
    "project": _module(
        EVERYONE, create=REGISTER_WRITERS, edit=REGISTER_WRITERS, submit=REGISTER_WRITERS, approve=GOVERNANCE, delete=GOVERNANCE,
        generate=GOVERNANCE, review_privacy=GOVERNANCE, review_ict=ADMINS + (R.ICT, R.RTD), review_dpo=(R.DPO,),
    ),
    "training": _module(EVERYONE, create=TRAINING_MANAGERS, edit=TRAINING_MANAGERS),
    "authorization": _module(AUTHORIZATION_VIEWERS, create=AUTHORIZATION_WRITERS, edit=AUTHORIZATION_WRITERS, delete=GOVERNANCE,
                             administer=PROFILE_MANAGERS),
    # Richieste di accesso dall'HelpDesk: catalogo e valutazioni (view), invio (create), decisione sui casi non standard (approve).
    "access": _module(GOVERNANCE + (R.INTEGRATION_HELPDESK,), create=ADMINS + (R.INTEGRATION_HELPDESK,), approve=ADMINS + (R.PRIVACY_MANAGER,)),
    "opinion": _module(EVERYONE, create=WRITERS, edit=WRITERS, approve=(R.DPO,)),
    "dpo": _module(DPO_VIEWERS),
    "report": _module(AUDIT_READERS, edit=GOVERNANCE, approve=GOVERNANCE, reopen=GOVERNANCE),
    "retention": _module(EVERYONE, create=REGISTER_WRITERS, edit=REGISTER_WRITERS, delete=REGISTER_WRITERS),
    "finding": _module(EVERYONE, edit=WRITERS, approve=GOVERNANCE, administer=GOVERNANCE),
    "transfer": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "risk": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "task": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "document": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS, administer=GOVERNANCE),
    "catalog": _module(EVERYONE, create=WRITERS, edit=WRITERS, delete=WRITERS),
    "organization": _module(EVERYONE, administer=ADMINS + (R.PRIVACY_MANAGER,)),
    "import": _module(IMPORTERS, create=IMPORTERS),
    "query": _module(EVERYONE, export=QUERY_EXPORTERS),
    "audit": _module(AUDIT_READERS),
    "approval": _module(EVERYONE),
    "roles": _module(ADMINS, administer=ADMINS),
    "policy": _module(ADMINS, administer=ADMINS),
    "outbox": _module(ADMINS, administer=ADMINS),
    "webhook": _module(ADMINS, administer=ADMINS),
    "integration": _module(INTEGRATION_READERS, create=INTEGRATION_WRITERS),
    "externalevent": _module(GOVERNANCE + (R.MANAGER, R.PRIVACY_REFERENT), edit=GOVERNANCE),
    "export": _module(ADMINS, administer=ADMINS),
}

DEFAULT_MATRIX: dict[str, tuple[R, ...]] = {
    f"{module}.{action}": roles for module, actions in DEFAULT_MODULES.items() for action, roles in actions.items()
}

# Non modificabili per ente: evitano di perdere l'accesso all'amministrazione o di delegarla.
LOCKED_PERMISSIONS = frozenset({
    "roles.administer", "policy.administer", "outbox.view", "outbox.administer", "export.view", "export.administer",
    "webhook.view", "webhook.administer", "integration.view", "integration.create", "access.create",
})


class UnknownPermission(ValueError):
    pass


def validate_overrides(overrides: dict[str, list[str]]) -> dict[str, list[str]]:
    """Normalizza un override (codici noti, ruoli validi, admin sempre inclusi); ValueError se non valido."""
    clean: dict[str, list[str]] = {}
    for code, roles in overrides.items():
        if code not in DEFAULT_MATRIX:
            raise UnknownPermission(f"Permesso sconosciuto: {code}")
        if code in LOCKED_PERMISSIONS:
            raise ValueError(f"Il permesso {code} non è modificabile")
        try:
            wanted = {R(r) for r in roles}
        except ValueError as e:
            raise ValueError(f"Ruolo non valido per {code}: {e}") from None
        extra_services = (wanted - set(DEFAULT_MATRIX[code])) & set(SERVICE_ROLES)
        if extra_services:
            raise ValueError(f"Gli account di servizio non si aggiungono al permesso {code}")
        wanted |= set(ADMINS)
        ordered = [r.value for r in R if r in wanted]
        if set(ordered) != {r.value for r in DEFAULT_MATRIX[code]}:
            clean[code] = ordered
    return clean


def effective_roles(code: str, overrides: dict | None) -> tuple[R, ...]:
    if code not in DEFAULT_MATRIX:
        raise UnknownPermission(f"Permesso sconosciuto: {code}")
    override = (overrides or {}).get(code)
    if override is None or code in LOCKED_PERMISSIONS:
        return DEFAULT_MATRIX[code]
    return tuple(r for r in R if r.value in override or r in ADMINS)


async def get_policy(db: AsyncSession, tenant_id: str) -> TenantPolicy | None:
    return (
        await db.execute(select(TenantPolicy).where(TenantPolicy.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def roles_for(db: AsyncSession, tenant_id: str, code: str) -> tuple[R, ...]:
    policy = await get_policy(db, tenant_id)
    return effective_roles(code, policy.permission_overrides if policy else None)


async def assert_permission(user: TokenPayload, db: AsyncSession, code: str) -> None:
    """403 se l'utente non ha, con nessun ruolo e a nessun scope, il permesso a livello ente."""
    if is_portal_global_or_tenant_admin(user):
        return
    tenant_id = tenant_of(user)
    allowed = set(await roles_for(db, tenant_id, code))
    assignments = await IdentityService(db).list_user_assignments(tenant_id, user.sub or user.email, active_only=True)
    if not ({a.role_code for a in assignments} & allowed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Permesso mancante: {code}")


async def assert_permission_for_unit(
    user: TokenPayload, db: AsyncSession, unit_id: str | None, code: str
) -> None:
    """Il permesso deve valere sulla struttura del record (il ruolo è legato al proprio scope, §9.3)."""
    if is_portal_global_or_tenant_admin(user):
        return
    roles = await roles_for(db, tenant_of(user), code)
    try:
        await assert_role_for_unit(user, db, unit_id, *roles)
    except HTTPException:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Permesso {code} non valido sulla struttura del record")


async def can_for_unit(user: TokenPayload, db: AsyncSession, unit_id: str | None, code: str) -> bool:
    try:
        await assert_permission_for_unit(user, db, unit_id, code)
    except HTTPException:
        return False
    return True


def require_permission(code: str):
    """Dipendenza FastAPI: richiede il permesso a livello ente (lo scope del record si verifica a parte)."""
    if code not in DEFAULT_MATRIX:
        raise UnknownPermission(code)

    async def _check(
        user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> TokenPayload:
        await assert_permission(user, db, code)
        return user

    _check.permission_code = code          # per i test che verificano l'autorizzazione di ogni rotta (§142)
    return _check


def require_module_view(module: str):
    """Dipendenza di router: le letture (GET/HEAD) richiedono `<modulo>.view`; le scritture controllano altro."""
    code = f"{module}.view"
    if code not in DEFAULT_MATRIX:
        raise UnknownPermission(code)

    async def _check(
        request: Request,
        user: TokenPayload = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if request.method in ("GET", "HEAD"):
            await assert_permission(user, db, code)

    _check.permission_code = code
    _check.reads_only = True
    return _check
