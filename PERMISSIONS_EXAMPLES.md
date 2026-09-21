# Permission System - Usage Examples

Quick reference for common permission scenarios.

---

## 📋 Table of Contents

1. [Protecting Endpoints](#protecting-endpoints)
2. [Role Management](#role-management)
3. [User Permission Checks](#user-permission-checks)
4. [Granular Permissions](#granular-permissions)
5. [Frontend Guards](#frontend-guards)

---

## 🛡️ Protecting Endpoints

### Example 1: Only Global Admins

```python
from fastapi import APIRouter, Depends
from backend.auth.permissions import require_global_admin

router = APIRouter()

@router.get("/api/platform/config")
def get_platform_config(
    user: dict = Depends(require_global_admin)
):
    """
    This endpoint is ONLY accessible to Global Admins (Platform Admins).
    Regular Tenant Admins or App Admins will get 403 Forbidden.
    """
    return {"config": "platform-wide settings"}
```

### Example 2: Tenant Admins Only

```python
from backend.auth.permissions import require_tenant_admin

@router.get("/api/tenants/{tenant_id}/settings")
def get_tenant_settings(
    tenant_id: str,
    user: dict = Depends(require_tenant_admin())
):
    """
    Accessible by:
    - Global Admins (can access any tenant)
    - Tenant Admin of the specified tenant
    
    Regular users will get 403 Forbidden.
    """
    return {"tenant_id": tenant_id}
```

### Example 3: App Admins Only

```python
from backend.auth.permissions import require_app_admin

@router.post("/api/permissions/grant")
def grant_permission(
    user: dict = Depends(require_app_admin())
):
    """
    Accessible by:
    - Global Admins
    - Tenant Admins
    - App Admins (of this specific app)
    
    Regular app users will get 403 Forbidden.
    """
    return {"message": "Permission granted"}
```

### Example 4: Specific Permission Required

```python
from backend.auth.permissions import require_permission

@router.delete("/api/tickets/{ticket_id}")
def delete_ticket(
    ticket_id: str,
    user: dict = Depends(require_permission("can_delete", "ticket"))
):
    """
    Accessible by:
    - Global/Tenant/App Admins (bypass permission checks)
    - Users with a role that has can_delete=True
    - Users with granular permission "can_delete" for resource type "ticket"
    
    Will check in this order:
    1. Is user an admin? → Allow
    2. Does user's role have can_delete=True? → Allow
    3. Does user have granular permission? → Allow
    4. Otherwise → 403 Forbidden
    """
    return {"message": f"Ticket {ticket_id} deleted"}
```

### Example 5: Resource-Specific Permission

```python
@router.put("/api/categories/{category_id}")
def update_category(
    category_id: str,
    user: dict = Depends(require_permission("can_update", "category"))
):
    """
    Checks if user can update resources of type "category".
    
    This is more flexible than hardcoding role checks since you can:
    - Grant "can_update" for category 5 but not for category 10
    - Revoke permissions dynamically without changing code
    """
    return {"message": f"Category {category_id} updated"}
```

---

## 🎭 Role Management

### Example 6: Create a Custom Role

```python
from backend.services.permissions_service import PermissionsService
from sqlalchemy.orm import Session

def create_support_role(db: Session):
    """Create a 'Support' role for customer service team."""
    
    role = PermissionsService.create_role(
        role_id="support",
        name="Support Team",
        description="Can view and update tickets, but not delete",
        permissions={
            "can_create": True,
            "can_read": True,
            "can_update": True,
            "can_delete": False,
            "can_assign": True,  # Can assign tickets to others
            "can_configure": False,
        },
        tenant_id=None,  # Global role (available to all tenants)
        db=db
    )
    
    print(f"Created role: {role['id']}")
```

### Example 7: Create a Tenant-Specific Role

```python
def create_premium_role(tenant_id: str, db: Session):
    """Create a tenant-specific role for premium customers."""
    
    role = PermissionsService.create_role(
        role_id="premium_user",
        name="Premium User",
        description="Premium tier with advanced features",
        permissions={
            "can_create": True,
            "can_read": True,
            "can_update": True,
            "can_delete": False,
            "can_assign": False,
            "can_configure": True,  # Can configure app settings
            "permissions_json": {
                "advanced_analytics": ["view", "export"],
                "api_access": ["read", "write"]
            }
        },
        tenant_id=tenant_id,  # Only for this tenant
        db=db
    )
    
    print(f"Created tenant-specific role: {role['id']} for tenant {tenant_id}")
```

---

## 👤 User Permission Checks

### Example 8: Check User Permissions in Service Layer

```python
from backend.auth.permissions import user_can, get_user_id, get_user_tenant_id

def delete_resource(resource_id: str, user_data: dict, db: Session):
    """Delete a resource if user has permission."""
    
    user_id = get_user_id(user_data)
    tenant_id = get_user_tenant_id(user_data)
    
    # Check if user can delete this resource type
    if not user_can(user_id, tenant_id, "can_delete", db, "resource"):
        raise PermissionError(f"User {user_id} cannot delete resources")
    
    # Proceed with deletion
    print(f"Deleting resource {resource_id}")
    # ... deletion logic ...
```

### Example 9: Conditional Logic Based on Permissions

```python
from backend.auth.permissions import is_global_admin, user_can

def get_resources(user_data: dict, db: Session):
    """Get resources, filtered by user permissions."""
    
    user_id = get_user_id(user_data)
    tenant_id = get_user_tenant_id(user_data)
    
    # Global Admin sees everything
    if is_global_admin(user_data):
        return db.query(Resource).all()
    
    # Regular users see only resources they can read
    resource_ids = []
    for resource in db.query(Resource).filter_by(tenant_id=tenant_id).all():
        if user_can(user_id, tenant_id, "can_read", db, "resource", str(resource.id)):
            resource_ids.append(resource)
    
    return resource_ids
```

### Example 10: Assign Role to User

```python
from backend.services.permissions_service import PermissionsService

def promote_user_to_operator(user_email: str, admin_email: str, db: Session):
    """Promote a user to the 'operator' role."""
    
    result = PermissionsService.assign_role_to_user(
        user_id=user_email,
        tenant_id="acme",
        role_id="operator",
        granted_by_user_id=admin_email,
        db=db
    )
    
    print(f"Promoted {user_email} to operator: {result['message']}")
```

---

## 🔍 Granular Permissions

### Example 11: Grant Category-Specific Read Permission

```python
from backend.auth.permissions import grant_permission

def grant_category_access(user_email: str, category_id: str, admin_email: str, db: Session):
    """Grant a user read access to a specific category."""
    
    perm = grant_permission(
        user_id=user_email,
        tenant_id="acme",
        resource_type="category",
        permission="read",
        granted_by=admin_email,
        db=db,
        resource_id=category_id  # ONLY this category
    )
    
    print(f"Granted {user_email} read access to category {category_id}")
```

### Example 12: Grant Time-Limited Permission

```python
from datetime import datetime, timedelta

def grant_temporary_access(user_email: str, admin_email: str, db: Session):
    """Grant temporary admin access (expires in 7 days)."""
    
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    perm = grant_permission(
        user_id=user_email,
        tenant_id="acme",
        resource_type="admin_tools",
        permission="configure",
        granted_by=admin_email,
        db=db,
        expires_at=expires_at
    )
    
    print(f"Granted temporary access until {expires_at}")
```

### Example 13: Check Resource-Specific Permission

```python
def can_user_delete_category(user_email: str, category_id: str, db: Session):
    """Check if user can delete a specific category."""
    
    tenant_id = "acme"
    
    can_delete = user_can(
        user_id=user_email,
        tenant_id=tenant_id,
        permission="can_delete",
        db=db,
        resource_type="category",
        resource_id=category_id
    )
    
    if can_delete:
        print(f"{user_email} can delete category {category_id}")
    else:
        print(f"{user_email} CANNOT delete category {category_id}")
    
    return can_delete
```

---

## 🖥️ Frontend Guards

### Example 14: Conditional UI Rendering

```typescript
import { isAppAdmin, isTenantAdmin, isGlobalAdmin } from '../services/authGuard';

const MyComponent = () => {
  return (
    <div>
      <h1>Dashboard</h1>
      
      {/* Only show to admins */}
      {(isGlobalAdmin() || isTenantAdmin() || isAppAdmin()) && (
        <Button onClick={() => navigate('/settings')}>
          Admin Settings
        </Button>
      )}
      
      {/* Only show to Global Admins */}
      {isGlobalAdmin() && (
        <Button onClick={() => navigate('/platform-config')}>
          Platform Config
        </Button>
      )}
    </div>
  );
};
```

### Example 15: Protect Route with Custom Hook

```typescript
import { Navigate } from 'react-router-dom';
import { canAccessSettings } from '../services/authGuard';

const SettingsPage = () => {
  // Redirect non-admins
  if (!canAccessSettings()) {
    return <Navigate to="/" replace />;
  }
  
  return <div>Settings Page</div>;
};
```

### Example 16: Fetch User Permissions

```typescript
import { apiClient } from '../services/apiClient';

const loadUserPermissions = async () => {
  try {
    // Get current user's permission info
    const data = await apiClient.get('/api/permissions/me');
    
    console.log('JWT Roles:', data.jwt_roles);
    console.log('App Roles:', data.app_roles);
    console.log('Granular Permissions:', data.granular_permissions);
    
    // Check if user is App Admin
    if (data.jwt_roles.is_app_admin) {
      console.log('User is App Admin!');
    }
  } catch (err) {
    console.error('Failed to load permissions:', err);
  }
};
```

---

## 🧪 Testing Examples

### Example 17: Test Permission Check

```python
import pytest
from backend.auth.permissions import user_can

def test_operator_can_create(db):
    """Test that operator role has can_create permission."""
    
    # Assign operator role to user
    PermissionsService.assign_role_to_user(
        user_id="test@acme.com",
        tenant_id="acme",
        role_id="operator",
        granted_by_user_id="admin@acme.com",
        db=db
    )
    
    # Verify permission
    assert user_can("test@acme.com", "acme", "can_create", db) == True
    assert user_can("test@acme.com", "acme", "can_delete", db) == False  # Operator cannot delete
```

### Example 18: Test Granular Permission Override

```python
def test_granular_permission_overrides_role(db):
    """Test that granular permission takes precedence."""
    
    # User has viewer role (can_delete=False)
    PermissionsService.assign_role_to_user(
        user_id="test@acme.com",
        tenant_id="acme",
        role_id="viewer",
        granted_by_user_id="admin@acme.com",
        db=db
    )
    
    # But we grant delete permission for category 5
    grant_permission(
        user_id="test@acme.com",
        tenant_id="acme",
        resource_type="category",
        permission="can_delete",
        granted_by="admin@acme.com",
        db=db,
        resource_id="5"
    )
    
    # Verify: user can delete category 5, but not other categories
    assert user_can("test@acme.com", "acme", "can_delete", db, "category", "5") == True
    assert user_can("test@acme.com", "acme", "can_delete", db, "category", "10") == False
```

---

## 📚 Additional Resources

- Full documentation: [PERMISSIONS_SYSTEM.md](PERMISSIONS_SYSTEM.md)
- Portal integration: [PORTAL_INTEGRATION.md](PORTAL_INTEGRATION.md)
- API reference: `/api/docs` (Swagger UI)

---

For more examples, see:
- `backend/routers/permissions_router.py` - API endpoint implementations
- `backend/services/permissions_service.py` - Service layer logic
- `frontend/src/components/PermissionsPanel.tsx` - Admin UI
