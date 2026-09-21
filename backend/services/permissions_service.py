"""
Permissions service - Business logic for permission management.

Handles role and permission CRUD operations, audit logging, and permission checks.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc

from backend.models.permissions_models import AppRole, UserAppRole, AppPermission, AppAuditLog
from backend.auth.permissions import (
    is_global_admin, is_tenant_admin, is_app_admin,
    get_user_id, get_user_tenant_id
)


class PermissionsService:
    """Service for managing app-level permissions."""
    
    # ========================================================================
    # App Roles CRUD
    # ========================================================================
    
    @staticmethod
    def get_all_roles(tenant_id: Optional[str], db: Session) -> List[Dict[str, Any]]:
        """
        Get all app roles.
        
        If tenant_id is provided, includes both global roles (tenant_id=NULL)
        and tenant-specific roles.
        """
        query = db.query(AppRole)
        
        if tenant_id:
            query = query.filter(
                or_(
                    AppRole.tenant_id == tenant_id,
                    AppRole.tenant_id.is_(None)
                )
            )
        else:
            query = query.filter(AppRole.tenant_id.is_(None))
        
        roles = query.all()
        
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "tenant_id": r.tenant_id,
                "can_create": r.can_create,
                "can_read": r.can_read,
                "can_update": r.can_update,
                "can_delete": r.can_delete,
                "can_assign": r.can_assign,
                "can_configure": r.can_configure,
                "permissions_json": r.permissions_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in roles
        ]
    
    @staticmethod
    def create_role(
        role_id: str,
        name: str,
        description: Optional[str],
        permissions: Dict[str, Any],
        tenant_id: Optional[str],
        db: Session
    ) -> Dict[str, Any]:
        """
        Create a new app role.
        
        Args:
            role_id: Unique role identifier (e.g., "operator")
            name: Display name
            description: Role description
            permissions: Dict with permission flags and permissions_json
            tenant_id: Tenant context (NULL for global roles)
            db: Database session
        
        Returns:
            Created role as dict
        """
        # Check if role already exists
        existing = db.query(AppRole).filter(
            and_(
                AppRole.id == role_id,
                AppRole.tenant_id == tenant_id if tenant_id else AppRole.tenant_id.is_(None)
            )
        ).first()
        
        if existing:
            raise ValueError(f"Role '{role_id}' already exists for this tenant")
        
        role = AppRole(
            id=role_id,
            name=name,
            description=description,
            tenant_id=tenant_id,
            can_create=permissions.get("can_create", False),
            can_read=permissions.get("can_read", True),
            can_update=permissions.get("can_update", False),
            can_delete=permissions.get("can_delete", False),
            can_assign=permissions.get("can_assign", False),
            can_configure=permissions.get("can_configure", False),
            permissions_json=permissions.get("permissions_json")
        )
        
        db.add(role)
        db.commit()
        db.refresh(role)
        
        return {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "tenant_id": role.tenant_id,
            "can_create": role.can_create,
            "can_read": role.can_read,
            "can_update": role.can_update,
            "can_delete": role.can_delete,
            "can_assign": role.can_assign,
            "can_configure": role.can_configure,
            "permissions_json": role.permissions_json,
        }
    
    @staticmethod
    def update_role(
        role_id: str,
        updates: Dict[str, Any],
        tenant_id: Optional[str],
        db: Session
    ) -> Dict[str, Any]:
        """Update an existing role."""
        role = db.query(AppRole).filter(
            and_(
                AppRole.id == role_id,
                AppRole.tenant_id == tenant_id if tenant_id else AppRole.tenant_id.is_(None)
            )
        ).first()
        
        if not role:
            raise ValueError(f"Role '{role_id}' not found")
        
        # Update fields
        if "name" in updates:
            role.name = updates["name"]
        if "description" in updates:
            role.description = updates["description"]
        if "can_create" in updates:
            role.can_create = updates["can_create"]
        if "can_read" in updates:
            role.can_read = updates["can_read"]
        if "can_update" in updates:
            role.can_update = updates["can_update"]
        if "can_delete" in updates:
            role.can_delete = updates["can_delete"]
        if "can_assign" in updates:
            role.can_assign = updates["can_assign"]
        if "can_configure" in updates:
            role.can_configure = updates["can_configure"]
        if "permissions_json" in updates:
            role.permissions_json = updates["permissions_json"]
        
        role.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(role)
        
        return {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "tenant_id": role.tenant_id,
            "can_create": role.can_create,
            "can_read": role.can_read,
            "can_update": role.can_update,
            "can_delete": role.can_delete,
            "can_assign": role.can_assign,
            "can_configure": role.can_configure,
            "permissions_json": role.permissions_json,
        }
    
    @staticmethod
    def delete_role(role_id: str, tenant_id: Optional[str], db: Session) -> bool:
        """Delete a role (and cascade delete all user assignments)."""
        role = db.query(AppRole).filter(
            and_(
                AppRole.id == role_id,
                AppRole.tenant_id == tenant_id if tenant_id else AppRole.tenant_id.is_(None)
            )
        ).first()
        
        if not role:
            return False
        
        db.delete(role)
        db.commit()
        return True
    
    # ========================================================================
    # User Role Assignments
    # ========================================================================
    
    @staticmethod
    def get_user_roles(user_id: str, tenant_id: str, db: Session) -> List[Dict[str, Any]]:
        """Get all roles assigned to a user."""
        user_roles = db.query(UserAppRole).filter(
            and_(
                UserAppRole.user_id == user_id,
                UserAppRole.tenant_id == tenant_id
            )
        ).all()
        
        return [
            {
                "role_id": ur.role_id,
                "role_name": ur.role.name if ur.role else ur.role_id,
                "granted_at": ur.granted_at.isoformat() if ur.granted_at else None,
                "granted_by": ur.granted_by,
            }
            for ur in user_roles
        ]
    
    @staticmethod
    def assign_role_to_user(
        user_id: str,
        tenant_id: str,
        role_id: str,
        granted_by_user_id: str,
        db: Session
    ) -> Dict[str, Any]:
        """Assign a role to a user."""
        # Verify role exists
        role = db.query(AppRole).filter(AppRole.id == role_id).first()
        if not role:
            raise ValueError(f"Role '{role_id}' not found")
        
        # Check if already assigned
        existing = db.query(UserAppRole).filter(
            and_(
                UserAppRole.user_id == user_id,
                UserAppRole.tenant_id == tenant_id,
                UserAppRole.role_id == role_id
            )
        ).first()
        
        if existing:
            return {
                "user_id": user_id,
                "role_id": role_id,
                "granted_at": existing.granted_at.isoformat() if existing.granted_at else None,
                "granted_by": existing.granted_by,
                "message": "Role already assigned"
            }
        
        # Create assignment
        user_role = UserAppRole(
            user_id=user_id,
            role_id=role_id,
            tenant_id=tenant_id,
            granted_by=granted_by_user_id
        )
        db.add(user_role)
        
        # Log action
        audit_log = AppAuditLog(
            tenant_id=tenant_id,
            action="grant_role",
            actor_user_id=granted_by_user_id,
            target_user_id=user_id,
            details={"role_id": role_id}
        )
        db.add(audit_log)
        
        db.commit()
        db.refresh(user_role)
        
        return {
            "user_id": user_id,
            "role_id": role_id,
            "granted_at": user_role.granted_at.isoformat() if user_role.granted_at else None,
            "granted_by": user_role.granted_by,
            "message": "Role assigned successfully"
        }
    
    @staticmethod
    def revoke_role_from_user(
        user_id: str,
        tenant_id: str,
        role_id: str,
        revoked_by_user_id: str,
        db: Session
    ) -> bool:
        """Revoke a role from a user."""
        user_role = db.query(UserAppRole).filter(
            and_(
                UserAppRole.user_id == user_id,
                UserAppRole.tenant_id == tenant_id,
                UserAppRole.role_id == role_id
            )
        ).first()
        
        if not user_role:
            return False
        
        db.delete(user_role)
        
        # Log action
        audit_log = AppAuditLog(
            tenant_id=tenant_id,
            action="revoke_role",
            actor_user_id=revoked_by_user_id,
            target_user_id=user_id,
            details={"role_id": role_id}
        )
        db.add(audit_log)
        
        db.commit()
        return True
    
    # ========================================================================
    # Granular Permissions
    # ========================================================================
    
    @staticmethod
    def get_user_permissions(
        user_id: str,
        tenant_id: str,
        resource_type: Optional[str],
        db: Session
    ) -> List[Dict[str, Any]]:
        """Get all granular permissions for a user."""
        query = db.query(AppPermission).filter(
            and_(
                AppPermission.user_id == user_id,
                AppPermission.tenant_id == tenant_id
            )
        )
        
        if resource_type:
            query = query.filter(AppPermission.resource_type == resource_type)
        
        perms = query.all()
        
        return [
            {
                "resource_type": p.resource_type,
                "resource_id": p.resource_id,
                "permission": p.permission,
                "is_allowed": p.is_allowed,
                "granted_at": p.granted_at.isoformat() if p.granted_at else None,
                "granted_by": p.granted_by,
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in perms
        ]
    
    # ========================================================================
    # Audit Logs
    # ========================================================================
    
    @staticmethod
    def get_audit_logs(
        tenant_id: str,
        limit: int,
        offset: int,
        user_id: Optional[str],
        db: Session
    ) -> List[Dict[str, Any]]:
        """Get audit logs for permission changes."""
        query = db.query(AppAuditLog).filter(
            AppAuditLog.tenant_id == tenant_id
        )
        
        if user_id:
            query = query.filter(
                or_(
                    AppAuditLog.actor_user_id == user_id,
                    AppAuditLog.target_user_id == user_id
                )
            )
        
        logs = query.order_by(desc(AppAuditLog.timestamp)).limit(limit).offset(offset).all()
        
        return [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "action": log.action,
                "actor_user_id": log.actor_user_id,
                "target_user_id": log.target_user_id,
                "details": log.details,
                "ip_address": log.ip_address,
                "reason": log.reason,
            }
            for log in logs
        ]
