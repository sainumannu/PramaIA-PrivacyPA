"""
Permission helper functions and decorators.

Implements 3-level permission hierarchy:
1. Global Admin (from Portal JWT) - Full access to everything
2. Tenant Admin (from Portal JWT) - Manage tenant users and settings
3. App Admin (from Portal JWT) - Manage app-specific permissions

Plus app-local granular permissions (AppRole, AppPermission).
"""
from typing import Optional, List, Dict, Any
from functools import wraps
from fastapi import HTTPException, status, Depends
from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.auth.portal_jwt import get_current_user
from backend.db.database import get_db
from backend.models.permissions_models import UserAppRole, AppPermission, AppRole


# ============================================================================
# JWT Permission Extraction
# ============================================================================

def is_global_admin(user_data: Dict[str, Any]) -> bool:
    """
    Check if user is a Global Admin (Platform Admin).
    
    Global Admins have unrestricted access to all tenants and apps.
    """
    return user_data.get("tenant_role") == "global_admin"


def is_tenant_admin(user_data: Dict[str, Any], tenant_id: Optional[str] = None) -> bool:
    """
    Check if user is Tenant Admin.
    
    If tenant_id is provided, checks if user is admin of that specific tenant.
    Otherwise, checks if user is admin of ANY tenant.
    """
    role = user_data.get("tenant_role", "").lower()
    
    # Global admin is also tenant admin
    if role == "global_admin":
        return True
    
    # Check tenant admin role
    if role == "admin":
        if tenant_id is None:
            return True  # Admin of at least one tenant
        # Verify tenant_id matches user's tenant
        user_tenant_id = user_data.get("tenant_id")
        return user_tenant_id == tenant_id
    
    return False


def is_app_admin(user_data: Dict[str, Any], app_id: Optional[str] = None) -> bool:
    """
    Check if user is App Admin.
    
    If app_id is provided, checks if user is admin of that specific app.
    Otherwise, checks if user is admin of ANY app.
    
    JWT contains 'app_admin_for': ["app1", "app2", ...] array.
    """
    # Global admin is also app admin
    if is_global_admin(user_data):
        return True
    
    app_admin_for = user_data.get("app_admin_for", [])
    
    if app_id is None:
        return len(app_admin_for) > 0
    
    return app_id in app_admin_for


def get_user_tenant_id(user_data: Dict[str, Any]) -> Optional[str]:
    """Extract tenant_id from JWT user data."""
    return user_data.get("tenant_id")


def get_user_id(user_data: Dict[str, Any]) -> str:
    """Extract user_id from JWT user data."""
    return user_data.get("sub") or user_data.get("user_id") or user_data.get("email", "")


# ============================================================================
# Local App Role Checks
# ============================================================================

def get_user_app_roles(
    user_id: str, 
    tenant_id: str, 
    db: Session
) -> List[AppRole]:
    """
    Get all app-specific roles assigned to a user.
    
    Returns list of AppRole objects.
    """
    user_roles = db.query(UserAppRole).filter(
        and_(
            UserAppRole.user_id == user_id,
            UserAppRole.tenant_id == tenant_id
        )
    ).all()
    
    return [ur.role for ur in user_roles if ur.role is not None]


def has_app_role(
    user_id: str, 
    tenant_id: str, 
    role_id: str, 
    db: Session
) -> bool:
    """
    Check if user has a specific app role.
    
    Example: has_app_role("mario@acme.com", "acme", "operator", db)
    """
    exists = db.query(UserAppRole).filter(
        and_(
            UserAppRole.user_id == user_id,
            UserAppRole.tenant_id == tenant_id,
            UserAppRole.role_id == role_id
        )
    ).first()
    
    return exists is not None


def user_can(
    user_id: str,
    tenant_id: str,
    permission: str,
    db: Session,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None
) -> bool:
    """
    Check if user has a specific permission.
    
    Checks both role-based permissions (via AppRole) and granular permissions (via AppPermission).
    
    Args:
        user_id: User identifier
        tenant_id: Tenant identifier
        permission: Permission name (e.g., "can_create", "can_delete")
        db: Database session
        resource_type: Optional resource type (e.g., "category", "module")
        resource_id: Optional specific resource ID
    
    Returns:
        True if user has the permission, False otherwise
    """
    # 1. Check role-based permissions
    roles = get_user_app_roles(user_id, tenant_id, db)
    for role in roles:
        # Check for permission flag on role
        if hasattr(role, permission) and getattr(role, permission):
            return True
        
        # Check permissions_json for complex permissions
        if role.permissions_json and resource_type:
            resource_perms = role.permissions_json.get(resource_type, [])
            if permission in resource_perms:
                return True
    
    # 2. Check granular permissions
    if resource_type:
        # Check for specific resource permission
        perm = db.query(AppPermission).filter(
            and_(
                AppPermission.user_id == user_id,
                AppPermission.tenant_id == tenant_id,
                AppPermission.resource_type == resource_type,
                AppPermission.resource_id == resource_id if resource_id else True,
                AppPermission.permission == permission,
                AppPermission.is_allowed == True
            )
        ).first()
        
        if perm:
            return True
    
    return False


# ============================================================================
# FastAPI Dependency Decorators
# ============================================================================

def require_global_admin(user_data: dict = Depends(get_current_user)) -> dict:
    """
    FastAPI dependency: Require Global Admin role.
    
    Usage:
        @router.get("/admin/users")
        def list_all_users(user: dict = Depends(require_global_admin)):
            ...
    """
    if not is_global_admin(user_data):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global Admin privileges required"
        )
    return user_data


def require_tenant_admin(
    tenant_id: Optional[str] = None
):
    """
    FastAPI dependency factory: Require Tenant Admin role.
    
    Usage:
        @router.get("/tenants/{tenant_id}/settings")
        def get_tenant_settings(
            tenant_id: str,
            user: dict = Depends(require_tenant_admin())
        ):
            ...
    
    If tenant_id is in path/query, verify user is admin of THAT tenant.
    """
    def _check_tenant_admin(user_data: dict = Depends(get_current_user)) -> dict:
        if not is_tenant_admin(user_data, tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant Admin privileges required" + 
                       (f" for tenant {tenant_id}" if tenant_id else "")
            )
        return user_data
    
    return _check_tenant_admin


def require_app_admin(
    app_id: Optional[str] = None
):
    """
    FastAPI dependency factory: Require App Admin role.
    
    Usage:
        @router.post("/apps/<app_id>/permissions")
        def grant_permission(
            user: dict = Depends(require_app_admin("<app_id>"))
        ):
            ...
    """
    def _check_app_admin(user_data: dict = Depends(get_current_user)) -> dict:
        if not is_app_admin(user_data, app_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"App Admin privileges required" + 
                       (f" for app {app_id}" if app_id else "")
            )
        return user_data
    
    return _check_app_admin


def require_permission(
    permission: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None
):
    """
    FastAPI dependency factory: Require specific app permission.
    
    Usage:
        @router.delete("/items/{item_id}")
        def delete_item(
            item_id: str,
            user: dict = Depends(require_permission("can_delete", "item", "{item_id}")),
            db: Session = Depends(get_db)
        ):
            ...
    """
    def _check_permission(
        user_data: dict = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> dict:
        # Global/Tenant/App admins bypass granular permissions
        if is_global_admin(user_data) or is_tenant_admin(user_data) or is_app_admin(user_data):
            return user_data
        
        user_id = get_user_id(user_data)
        tenant_id = get_user_tenant_id(user_data)
        
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not associated with any tenant"
            )
        
        if not user_can(user_id, tenant_id, permission, db, resource_type, resource_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required" +
                       (f" for {resource_type}" if resource_type else "")
            )
        
        return user_data
    
    return _check_permission


# ============================================================================
# Helper Functions for Permission Management
# ============================================================================

def grant_app_role(
    user_id: str,
    tenant_id: str,
    role_id: str,
    granted_by: str,
    db: Session
) -> UserAppRole:
    """
    Grant an app role to a user.
    
    Args:
        user_id: User to grant role to
        tenant_id: Tenant context
        role_id: Role identifier
        granted_by: User ID granting the role
        db: Database session
    
    Returns:
        UserAppRole instance
    
    Raises:
        ValueError: If role doesn't exist
    """
    # Verify role exists
    role = db.query(AppRole).filter(AppRole.id == role_id).first()
    if not role:
        raise ValueError(f"Role '{role_id}' not found")
    
    # Check if already exists
    existing = db.query(UserAppRole).filter(
        and_(
            UserAppRole.user_id == user_id,
            UserAppRole.role_id == role_id,
            UserAppRole.tenant_id == tenant_id
        )
    ).first()
    
    if existing:
        return existing
    
    # Create new role assignment
    user_role = UserAppRole(
        user_id=user_id,
        role_id=role_id,
        tenant_id=tenant_id,
        granted_by=granted_by
    )
    db.add(user_role)
    db.commit()
    db.refresh(user_role)
    
    return user_role


def revoke_app_role(
    user_id: str,
    tenant_id: str,
    role_id: str,
    db: Session
) -> bool:
    """
    Revoke an app role from a user.
    
    Returns:
        True if role was revoked, False if user didn't have the role
    """
    user_role = db.query(UserAppRole).filter(
        and_(
            UserAppRole.user_id == user_id,
            UserAppRole.role_id == role_id,
            UserAppRole.tenant_id == tenant_id
        )
    ).first()
    
    if not user_role:
        return False
    
    db.delete(user_role)
    db.commit()
    return True


def grant_permission(
    user_id: str,
    tenant_id: str,
    resource_type: str,
    permission: str,
    granted_by: str,
    db: Session,
    resource_id: Optional[str] = None,
    expires_at: Optional[Any] = None
) -> AppPermission:
    """
    Grant a granular permission to a user.
    """
    # Check if already exists
    existing = db.query(AppPermission).filter(
        and_(
            AppPermission.user_id == user_id,
            AppPermission.tenant_id == tenant_id,
            AppPermission.resource_type == resource_type,
            AppPermission.resource_id == resource_id,
            AppPermission.permission == permission
        )
    ).first()
    
    if existing:
        existing.is_allowed = True
        existing.granted_by = granted_by
        existing.expires_at = expires_at
        db.commit()
        db.refresh(existing)
        return existing
    
    # Create new permission
    perm = AppPermission(
        user_id=user_id,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        permission=permission,
        is_allowed=True,
        granted_by=granted_by,
        expires_at=expires_at
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)
    
    return perm


def revoke_permission(
    user_id: str,
    tenant_id: str,
    resource_type: str,
    permission: str,
    db: Session,
    resource_id: Optional[str] = None
) -> bool:
    """
    Revoke a granular permission from a user.
    
    Returns:
        True if permission was revoked, False if user didn't have it
    """
    perm = db.query(AppPermission).filter(
        and_(
            AppPermission.user_id == user_id,
            AppPermission.tenant_id == tenant_id,
            AppPermission.resource_type == resource_type,
            AppPermission.resource_id == resource_id,
            AppPermission.permission == permission
        )
    ).first()
    
    if not perm:
        return False
    
    db.delete(perm)
    db.commit()
    return True
