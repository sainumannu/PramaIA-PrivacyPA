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
from backend.auth.privacypa_permissions import require_permission
from backend.db.database import get_db
from backend.models.privacypa_identity import PrivacyRoleCode, ScopeType
from backend.services.privacypa_identity_service import IdentityService

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Identity"])


class RoleAssignRequest(BaseModel):
    user_id: str
    role_code: PrivacyRoleCode
    scope_type: ScopeType = ScopeType.TENANT
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
    current_id = user.sub or user.email
    if user_id != current_id and user.tenant_role not in ("admin", "global_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Non autorizzato a visualizzare i ruoli di un altro utente",
        )
    svc = IdentityService(db)
    assignments = await svc.list_user_assignments(tenant_id, user_id)
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
