"""
Permissions router - API endpoints for permission management.

All endpoints require appropriate admin privileges (Global Admin, Tenant Admin, or App Admin).
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.db.database import get_db
from backend.auth.permissions import (
    require_app_admin, require_tenant_admin,
    get_current_user, get_user_id, get_user_tenant_id,
    is_global_admin, is_tenant_admin, is_app_admin
)
from backend.services.permissions_service import PermissionsService


router = APIRouter(prefix="/api/permissions", tags=["permissions"])


# ============================================================================
# Request/Response Models
# ============================================================================

class RoleCreateRequest(BaseModel):
    """Request body for creating a new role."""
    role_id: str = Field(..., description="Unique role identifier (e.g., 'operator')")
    name: str = Field(..., description="Display name for the role")
    description: Optional[str] = Field(None, description="Role description")
    can_create: bool = Field(False, description="Can create resources")
    can_read: bool = Field(True, description="Can read resources")
    can_update: bool = Field(False, description="Can update resources")
    can_delete: bool = Field(False, description="Can delete resources")
    can_assign: bool = Field(False, description="Can assign resources to others")
    can_configure: bool = Field(False, description="Can configure app settings")
    permissions_json: Optional[Dict[str, Any]] = Field(None, description="Additional permissions as JSON")


class RoleUpdateRequest(BaseModel):
    """Request body for updating a role."""
    name: Optional[str] = None
    description: Optional[str] = None
    can_create: Optional[bool] = None
    can_read: Optional[bool] = None
    can_update: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_assign: Optional[bool] = None
    can_configure: Optional[bool] = None
    permissions_json: Optional[Dict[str, Any]] = None


class AssignRoleRequest(BaseModel):
    """Request body for assigning a role to a user."""
    user_id: str = Field(..., description="User ID (email)")
    role_id: str = Field(..., description="Role to assign")


# ============================================================================
# Role Management Endpoints
# ============================================================================

@router.get("/roles")
def get_roles(
    user_data: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Get all available app roles.
    
    Returns global roles + tenant-specific roles (if user belongs to a tenant).
    """
    tenant_id = get_user_tenant_id(user_data)
    roles = PermissionsService.get_all_roles(tenant_id, db)
    return roles


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(
    request: RoleCreateRequest,
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Create a new app role.
    
    Requires App Admin privileges.
    """
    tenant_id = get_user_tenant_id(user_data)
    
    try:
        permissions = {
            "can_create": request.can_create,
            "can_read": request.can_read,
            "can_update": request.can_update,
            "can_delete": request.can_delete,
            "can_assign": request.can_assign,
            "can_configure": request.can_configure,
            "permissions_json": request.permissions_json
        }
        
        role = PermissionsService.create_role(
            role_id=request.role_id,
            name=request.name,
            description=request.description,
            permissions=permissions,
            tenant_id=tenant_id,
            db=db
        )
        
        return role
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/roles/{role_id}")
def update_role(
    role_id: str,
    request: RoleUpdateRequest,
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Update an existing role.
    
    Requires App Admin privileges.
    """
    tenant_id = get_user_tenant_id(user_data)
    
    try:
        updates = {k: v for k, v in request.dict().items() if v is not None}
        role = PermissionsService.update_role(role_id, updates, tenant_id, db)
        return role
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: str,
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
):
    """
    Delete a role.
    
    Requires App Admin privileges.
    WARNING: This will revoke the role from all users who have it.
    """
    tenant_id = get_user_tenant_id(user_data)
    
    success = PermissionsService.delete_role(role_id, tenant_id, db)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")


# ============================================================================
# User Role Assignment Endpoints
# ============================================================================

@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: str,
    user_data: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Get all roles assigned to a specific user.
    
    Users can view their own roles. Admins can view any user's roles.
    """
    current_user_id = get_user_id(user_data)
    tenant_id = get_user_tenant_id(user_data)
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not associated with any tenant"
        )
    
    # Check permissions: user can view their own roles, or must be admin
    if user_id != current_user_id:
        if not (is_global_admin(user_data) or is_tenant_admin(user_data) or is_app_admin(user_data)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges to view other users' roles"
            )
    
    roles = PermissionsService.get_user_roles(user_id, tenant_id, db)
    return roles


@router.post("/users/assign-role", status_code=status.HTTP_201_CREATED)
def assign_role(
    request: AssignRoleRequest,
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Assign a role to a user.
    
    Requires App Admin privileges.
    """
    tenant_id = get_user_tenant_id(user_data)
    granted_by = get_user_id(user_data)
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not associated with any tenant"
        )
    
    try:
        result = PermissionsService.assign_role_to_user(
            user_id=request.user_id,
            tenant_id=tenant_id,
            role_id=request.role_id,
            granted_by_user_id=granted_by,
            db=db
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/users/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_role(
    user_id: str,
    role_id: str,
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
):
    """
    Revoke a role from a user.
    
    Requires App Admin privileges.
    """
    tenant_id = get_user_tenant_id(user_data)
    revoked_by = get_user_id(user_data)
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not associated with any tenant"
        )
    
    success = PermissionsService.revoke_role_from_user(
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=role_id,
        revoked_by_user_id=revoked_by,
        db=db
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role assignment not found"
        )


# ============================================================================
# User Permissions Endpoints
# ============================================================================

@router.get("/users/{user_id}/permissions")
def get_user_permissions(
    user_id: str,
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    user_data: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Get all granular permissions for a user.
    
    Users can view their own permissions. Admins can view any user's permissions.
    """
    current_user_id = get_user_id(user_data)
    tenant_id = get_user_tenant_id(user_data)
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not associated with any tenant"
        )
    
    # Check permissions
    if user_id != current_user_id:
        if not (is_global_admin(user_data) or is_tenant_admin(user_data) or is_app_admin(user_data)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges to view other users' permissions"
            )
    
    perms = PermissionsService.get_user_permissions(user_id, tenant_id, resource_type, db)
    return perms


# ============================================================================
# Audit Log Endpoints
# ============================================================================

@router.get("/audit-logs")
def get_audit_logs(
    limit: int = Query(50, ge=1, le=500, description="Number of logs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    user_data: dict = Depends(require_app_admin()),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Get audit logs for permission changes.
    
    Requires App Admin privileges.
    """
    tenant_id = get_user_tenant_id(user_data)
    
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not associated with any tenant"
        )
    
    logs = PermissionsService.get_audit_logs(tenant_id, limit, offset, user_id, db)
    return logs


# ============================================================================
# Current User Info (helper endpoint)
# ============================================================================

@router.get("/me")
def get_my_permissions_info(
    user_data: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get current user's permission information.
    
    Returns: JWT roles + app-specific roles + granular permissions.
    """
    user_id = get_user_id(user_data)
    tenant_id = get_user_tenant_id(user_data)
    
    # JWT-based roles
    jwt_roles = {
        "is_global_admin": is_global_admin(user_data),
        "is_tenant_admin": is_tenant_admin(user_data, tenant_id),
        "is_app_admin": is_app_admin(user_data),
        "tenant_role": user_data.get("tenant_role"),
        "app_admin_for": user_data.get("app_admin_for", [])
    }
    
    # App-specific roles
    app_roles = []
    if tenant_id:
        app_roles = PermissionsService.get_user_roles(user_id, tenant_id, db)
    
    # Granular permissions
    granular_permissions = []
    if tenant_id:
        granular_permissions = PermissionsService.get_user_permissions(user_id, tenant_id, None, db)
    
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "jwt_roles": jwt_roles,
        "app_roles": app_roles,
        "granular_permissions": granular_permissions
    }
