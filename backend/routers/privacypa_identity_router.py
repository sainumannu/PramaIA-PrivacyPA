"""
PrivacyPA — Identity: catalogo ruoli privacy e assegnazioni con scope (§9).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_confidentiality import require_admin_step_up
from backend.auth.privacypa_permissions import assert_permission, require_permission
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode, ScopeType, UserRoleAssignment
from backend.services.privacypa_identity_service import IdentityService
from backend.utils.concurrency import require_if_match

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Identity"])


class RoleAssignRequest(BaseModel):
    user_id: str
    role_code: PrivacyRoleCode
    scope_type: ScopeType = ScopeType.TENANT
    organizational_unit_id: str | None = None
    valid_to: datetime | None = None


class RoleUpdateRequest(BaseModel):
    """Oggetto completo: una scadenza omessa si toglie."""
    scope_type: ScopeType
    organizational_unit_id: str | None = None
    valid_to: datetime | None = None


@router.get("/roles/catalog")
async def get_role_catalog(user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    svc = IdentityService(db)
    roles = await svc.list_role_catalog()
    return [r.to_dict() for r in roles]


@router.get("/users/{user_id}/roles")
async def get_user_roles(
    user_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # i propri ruoli li vede chiunque; quelli degli altri chi ha il permesso di vedere i ruoli (lo stesso di chi li assegna)
    if user_id != (user.sub or user.email):
        await assert_permission(user, db, "roles.view")
    svc = IdentityService(db)
    assignments = await svc.list_user_assignments(tenant_id, user_id, all_roles=True)
    return [a.to_dict() for a in assignments]


@router.get("/roles/assignments")
async def list_assignments(
    role_code: PrivacyRoleCode | None = None,
    organizational_unit_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("roles.view")),
    db: AsyncSession = Depends(get_db),
):
    """Chi ha quale ruolo nell'ente, scadute comprese."""
    assignments = await IdentityService(db).list_tenant_assignments(
        tenant_id, role_code=role_code, organizational_unit_id=organizational_unit_id,
    )
    return [a.to_dict() for a in assignments]


@router.post("/roles/assign", status_code=status.HTTP_201_CREATED)
async def assign_role(
    body: RoleAssignRequest,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("roles.administer")),
    db: AsyncSession = Depends(get_db),
):
    if body.role_code == PrivacyRoleCode.SUPER_ADMIN:
        await require_admin_step_up(db, tenant_id, user)          # §136: assegnazione di SUPER_ADMIN
    svc = IdentityService(db)
    try:
        assignment = await svc.assign_role(
            tenant_id=tenant_id,
            user_id=body.user_id,
            role_code=body.role_code,
            granted_by=user.sub or user.email,
            scope_type=body.scope_type,
            organizational_unit_id=body.organizational_unit_id,
            valid_to=body.valid_to,
        )
        return assignment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/roles/{assignment_id}")
async def update_assignment(
    assignment_id: str,
    body: RoleUpdateRequest,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("roles.administer")),
    version: int = Depends(require_if_match),
    db: AsyncSession = Depends(get_db),
):
    svc = IdentityService(db)
    current = await db.get(UserRoleAssignment, assignment_id)
    if current is not None and current.tenant_id == tenant_id and current.role_code == PrivacyRoleCode.SUPER_ADMIN:
        await require_admin_step_up(db, tenant_id, user)          # §136: come l'assegnazione di SUPER_ADMIN
    try:
        assignment = await svc.update_assignment(
            tenant_id, assignment_id, version, user.sub or user.email,
            scope_type=body.scope_type, organizational_unit_id=body.organizational_unit_id, valid_to=body.valid_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assegnazione non trovata")
    return assignment.to_dict()


@router.delete("/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_role(
    assignment_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("roles.administer")),
    db: AsyncSession = Depends(get_db),
):
    svc = IdentityService(db)
    revoked = await svc.revoke_role(tenant_id, assignment_id, revoked_by=user.sub or user.email)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assegnazione non trovata")
