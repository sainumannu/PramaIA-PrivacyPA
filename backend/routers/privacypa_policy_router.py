"""
PrivacyPA — Policy di ente e permessi dell'utente corrente (§10, §10.1, §166).

- `GET /me/permissions`: cosa può fare l'utente (per menu e pulsanti dinamici) e con quale scope.
- `GET/PUT /policy/permissions`: matrice dei permessi per ente (solo amministratori).
- `GET/PUT /policy/segregation`: regole di segregazione dei compiti (solo amministratori).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import (
    get_accessible_unit_ids,
    is_portal_global_or_tenant_admin,
    require_tenant,
)
from backend.auth.privacypa_confidentiality import CONFIGURABLE_LEVELS, WATERMARK_MODES, effective_config
from backend.auth.privacypa_permissions import (
    DEFAULT_MATRIX,
    LOCKED_PERMISSIONS,
    UnknownPermission,
    effective_roles,
    get_policy,
    require_permission,
)
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode
from backend.services.privacypa_identity_service import IdentityService
from backend.services.privacypa_policy_service import (
    DEFAULT_SEGREGATION,
    PolicyConflict,
    PolicyService,
    get_segregation,
)
from backend.utils.concurrency import require_if_match

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Policy"])


class PermissionOverridesIn(BaseModel):
    overrides: dict[str, list[str]] = Field(
        default_factory=dict, description="permesso -> ruoli; i permessi non indicati tornano al default"
    )


class DeadlinesIn(BaseModel):
    dsr_base_months: int | None = None
    dsr_extension_months: int | None = None
    processing_review_months: int | None = None
    dpia_review_months: int | None = None


class SegregationIn(BaseModel):
    approver_not_requester: bool | None = None
    approver_not_editor: bool | None = None


class LevelRuleIn(BaseModel):
    roles: list[str] | None = None
    step_up: bool = False
    watermark: str = "off"
    max_downloads_per_hour: int | None = None
    block_bulk_export: bool = False


class StepUpIn(BaseModel):
    max_age_seconds: int = 900
    admin_actions: bool = False


class ConfidentialityIn(BaseModel):
    levels: dict[str, LevelRuleIn] = Field(default_factory=dict)
    step_up: StepUpIn = Field(default_factory=StepUpIn)


def _conflict(exc: PolicyConflict) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, str(exc), headers={"ETag": f'"{exc.current_version}"'}
    )


async def _matrix_payload(db: AsyncSession, tenant_id: str) -> dict:
    policy = await get_policy(db, tenant_id)
    overrides = (policy.permission_overrides if policy else None) or {}
    permissions = []
    for code, default in DEFAULT_MATRIX.items():
        module, action = code.split(".", 1)
        roles = effective_roles(code, overrides)
        permissions.append({
            "code": code,
            "module": module,
            "action": action,
            "locked": code in LOCKED_PERMISSIONS,
            "default_roles": [r.value for r in default],
            "roles": [r.value for r in roles],
            "overridden": code in overrides,
        })
    return {
        "row_version": policy.row_version if policy else 0,
        "roles": [r.value for r in PrivacyRoleCode],
        "permissions": permissions,
    }


@router.get("/me/permissions")
async def my_permissions(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permessi effettivi dell'utente: `allowed`, se valgono sull'intero ente e, altrimenti, su quali strutture."""
    user_id = user.sub or user.email
    admin = is_portal_global_or_tenant_admin(user)
    assignments = await IdentityService(db).list_user_assignments(tenant_id, user_id, active_only=True)
    policy = await get_policy(db, tenant_id)
    overrides = (policy.permission_overrides if policy else None) or {}

    by_roles: dict[frozenset, set[str] | None] = {}
    permissions: dict[str, dict] = {}
    for code in DEFAULT_MATRIX:
        if admin:
            permissions[code] = {"allowed": True, "tenant_wide": True, "unit_ids": []}
            continue
        roles = frozenset(effective_roles(code, overrides))
        if roles not in by_roles:
            by_roles[roles] = await get_accessible_unit_ids(db, tenant_id, user_id, False, roles=roles)
        units = by_roles[roles]
        permissions[code] = {
            "allowed": units is None or bool(units),
            "tenant_wide": units is None,
            "unit_ids": sorted(units) if units else [],
        }
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "is_admin": admin,
        "assignments": [a.to_dict() for a in assignments],
        "permissions": permissions,
    }


@router.get("/policy/permissions")
async def get_permission_matrix(
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    payload = await _matrix_payload(db, tenant_id)
    response.headers["ETag"] = f'"{payload["row_version"]}"'
    return payload


@router.put("/policy/permissions")
async def put_permission_matrix(
    body: PermissionOverridesIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await PolicyService(db).update_permissions(tenant_id, user.sub or user.email, body.overrides, version)
    except PolicyConflict as e:
        raise _conflict(e)
    except UnknownPermission as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    payload = await _matrix_payload(db, tenant_id)
    response.headers["ETag"] = f'"{payload["row_version"]}"'
    return payload


@router.get("/policy/deadlines")
async def get_deadlines_policy(
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    from backend.services.privacypa_compliance_service import get_deadlines

    policy = await get_policy(db, tenant_id)
    version = policy.row_version if policy else 0
    response.headers["ETag"] = f'"{version}"'
    return {"row_version": version, "values": await get_deadlines(db, tenant_id)}


@router.put("/policy/deadlines")
async def put_deadlines_policy(
    body: DeadlinesIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    from backend.services.privacypa_compliance_service import get_deadlines

    values = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        policy = await PolicyService(db).update_deadlines(tenant_id, user.sub or user.email, values, version)
    except PolicyConflict as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    response.headers["ETag"] = f'"{policy.row_version}"'
    return {"row_version": policy.row_version, "values": await get_deadlines(db, tenant_id)}


class IntegrationsIn(BaseModel):
    # Categoria di dati (codice del catalogo) o classe documentale che identifica le riprese; vuoto = non configurata.
    retention_category: str | None = Field(None, max_length=100)
    # Mesi di conservazione degli eventi ricevuti da sistemi esterni (vuoto = 24).
    external_events_retention_months: int | None = Field(None, ge=1, le=120)
    # Richieste di accesso dall'HelpDesk: la formazione non valida rende la richiesta non standard.
    access_training_blocking: bool = False


@router.get("/policy/integrations")
async def get_integrations_policy(
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    from backend.services.privacypa_integration_service import get_integrations

    policy = await get_policy(db, tenant_id)
    version = policy.row_version if policy else 0
    response.headers["ETag"] = f'"{version}"'
    return {"row_version": version, "values": await get_integrations(db, tenant_id)}


@router.put("/policy/integrations")
async def put_integrations_policy(
    body: IntegrationsIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    from backend.services.privacypa_integration_service import get_integrations

    try:
        policy = await PolicyService(db).update_integrations(tenant_id, user.sub or user.email, body.model_dump(), version)
    except PolicyConflict as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    response.headers["ETag"] = f'"{policy.row_version}"'
    return {"row_version": policy.row_version, "values": await get_integrations(db, tenant_id)}


def _confidentiality_payload(policy) -> dict:
    return {
        "row_version": policy.row_version if policy else 0,
        "config": effective_config(policy.confidentiality if policy else None),
        "levels": list(CONFIGURABLE_LEVELS), "watermark_modes": list(WATERMARK_MODES), "roles": [r.value for r in PrivacyRoleCode],
    }


@router.get("/policy/confidentiality")
async def get_confidentiality_policy(
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    payload = _confidentiality_payload(await get_policy(db, tenant_id))
    response.headers["ETag"] = f'"{payload["row_version"]}"'
    return payload


@router.put("/policy/confidentiality")
async def put_confidentiality_policy(
    body: ConfidentialityIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    try:
        policy = await PolicyService(db).update_confidentiality(tenant_id, user.sub or user.email, body.model_dump(), version)
    except PolicyConflict as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    response.headers["ETag"] = f'"{policy.row_version}"'
    return _confidentiality_payload(policy)


@router.get("/policy/segregation")
async def get_segregation_policy(
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    policy = await get_policy(db, tenant_id)
    version = policy.row_version if policy else 0
    response.headers["ETag"] = f'"{version}"'
    return {"row_version": version, "rules": await get_segregation(db, tenant_id), "available": list(DEFAULT_SEGREGATION)}


@router.put("/policy/segregation")
async def put_segregation_policy(
    body: SegregationIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("policy.administer")),
    db: AsyncSession = Depends(get_db),
):
    rules = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        policy = await PolicyService(db).update_segregation(tenant_id, user.sub or user.email, rules, version)
    except PolicyConflict as e:
        raise _conflict(e)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    response.headers["ETag"] = f'"{policy.row_version}"'
    return {"row_version": policy.row_version, "rules": await get_segregation(db, tenant_id), "available": list(DEFAULT_SEGREGATION)}
