"""
Local permissions models for app-level authorization.

This system complements Portal's tenant/app permissions with
app-specific granular permissions (e.g., per-category, per-module).
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, Text, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.db.database import Base


class AppRole(Base):
    """
    Application-specific roles.
    
    Extends Portal roles (Global Admin, Tenant Admin, App Admin) with
    custom app-level roles (e.g., "Operator", "Viewer", "Manager").
    """
    __tablename__ = "app_roles"

    id = Column(String(100), primary_key=True)  # e.g., "operator", "viewer"
    name = Column(String(100), nullable=False)  # Display name
    description = Column(Text, nullable=True)
    tenant_id = Column(String(100), nullable=True, index=True)  # NULL = global role
    
    # Permissions flags (customize per app needs)
    can_create = Column(Boolean, default=False)
    can_read = Column(Boolean, default=True)
    can_update = Column(Boolean, default=False)
    can_delete = Column(Boolean, default=False)
    can_assign = Column(Boolean, default=False)  # Can assign items to others
    can_configure = Column(Boolean, default=False)  # Can configure app settings
    
    # Additional permissions (JSON for flexibility)
    permissions_json = Column(JSON, nullable=True)  # { "module": ["action1", "action2"], ... }
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user_roles = relationship("UserAppRole", back_populates="role", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('id', 'tenant_id', name='uix_role_tenant'),
    )


class UserAppRole(Base):
    """
    Maps users to app-specific roles.
    
    Example: user "mario@acme.com" has role "operator" in this app for tenant "acme".
    """
    __tablename__ = "user_app_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)  # Portal user ID
    role_id = Column(String(100), ForeignKey("app_roles.id"), nullable=False)
    tenant_id = Column(String(100), nullable=False, index=True)
    
    # Metadata
    granted_at = Column(DateTime, default=datetime.utcnow)
    granted_by = Column(String(100), nullable=True)  # User ID che ha assegnato il ruolo
    
    # Relationships
    role = relationship("AppRole", back_populates="user_roles")

    __table_args__ = (
        UniqueConstraint('user_id', 'role_id', 'tenant_id', name='uix_user_role_tenant'),
    )


class AppPermission(Base):
    """
    Granular permissions for specific resources.
    
    Example: user can only see categories [1, 2, 5] but not [3, 4].
    This allows fine-grained access control beyond role-based permissions.
    """
    __tablename__ = "app_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    
    # Resource specification
    resource_type = Column(String(50), nullable=False)  # e.g., "category", "module", "group"
    resource_id = Column(String(100), nullable=True)  # Specific resource ID (NULL = all)
    
    # Permission type
    permission = Column(String(50), nullable=False)  # e.g., "read", "write", "delete"
    
    # Allow/Deny
    is_allowed = Column(Boolean, default=True)  # True = allow, False = deny
    
    # Metadata
    granted_at = Column(DateTime, default=datetime.utcnow)
    granted_by = Column(String(100), nullable=True)
    expires_at = Column(DateTime, nullable=True)  # Optional expiration
    
    __table_args__ = (
        UniqueConstraint('user_id', 'tenant_id', 'resource_type', 'resource_id', 'permission', 
                        name='uix_user_resource_permission'),
    )


class AppAuditLog(Base):
    """
    Audit log for permission changes.
    
    Tracks who granted/revoked permissions to whom, when, and why.
    """
    __tablename__ = "app_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    
    # Action details
    action = Column(String(50), nullable=False)  # "grant_role", "revoke_role", "grant_permission", etc.
    actor_user_id = Column(String(100), nullable=False)  # Who performed the action
    target_user_id = Column(String(100), nullable=True)  # Who was affected
    
    # Details (JSON for flexibility)
    details = Column(JSON, nullable=True)  # { "role_id": "operator", "resource_type": "category", ... }
    
    # Additional context
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)  # Optional reason for change
