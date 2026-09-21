# Permission System - Developer Guide

## Overview

The PramaIA AppTemplate includes a **3-level role-based permission system** with granular permissions support. This system complements Portal's tenant/app permissions with application-specific authorization.

---

## 🏗️ Architecture

### Three Permission Levels

1. **Portal-Level Permissions** (via JWT)
   - **Global Admin**: Platform-wide superuser (Portal administrator)
   - **Tenant Admin**: Manage users and settings within their tenant
   - **App Admin**: Manage app-specific permissions for users in this app

2. **App-Level Roles** (local to this app)
   - Custom roles defined per-app (e.g., "Operator", "Viewer", "Manager")
   - Each role has permission flags: `can_create`, `can_read`, `can_update`, `can_delete`, `can_assign`, `can_configure`
   - Can be global (tenant_id=NULL) or tenant-specific

3. **Granular Permissions** (resource-specific)
   - Fine-grained access control for specific resources
   - Example: User can only access categories [1, 2, 5] but not [3, 4]
   - Supports resource types, resource IDs, permission types, allow/deny

---

## 📚 Database Models

### `AppRole`
Application-specific roles with permission flags.

```python
class AppRole(Base):
    __tablename__ = "app_roles"
    
    id: str                # e.g., "operator", "viewer"
    name: str              # Display name
    description: str       # Role description
    tenant_id: str | None  # NULL = global role, otherwise tenant-specific
    
    # Permission flags
    can_create: bool
    can_read: bool
    can_update: bool
    can_delete: bool
    can_assign: bool       # Can assign resources to others
    can_configure: bool    # Can configure app settings
    
    permissions_json: dict # Additional permissions as JSON
```

### `UserAppRole`
Maps users to app-specific roles.

```python
class UserAppRole(Base):
    __tablename__ = "user_app_roles"
    
    user_id: str           # Portal user ID (email)
    role_id: str           # FK to AppRole.id
    tenant_id: str         # Tenant context
    granted_at: datetime
    granted_by: str        # User ID who granted the role
```

### `AppPermission`
Granular permissions for specific resources.

```python
class AppPermission(Base):
    __tablename__ = "app_permissions"
    
    user_id: str           # Portal user ID
    tenant_id: str
    resource_type: str     # e.g., "category", "module"
    resource_id: str       # Specific resource ID (NULL = all)
    permission: str        # e.g., "read", "write", "delete"
    is_allowed: bool       # True = allow, False = deny
    granted_by: str
    expires_at: datetime   # Optional expiration
```

### `AppAuditLog`
Audit trail for permission changes.

```python
class AppAuditLog(Base):
    __tablename__ = "app_audit_logs"
    
    timestamp: datetime
    tenant_id: str
    action: str            # e.g., "grant_role", "revoke_role"
    actor_user_id: str     # Who performed the action
    target_user_id: str    # Who was affected
    details: dict          # Action details as JSON
    reason: str            # Optional reason
```

---

## 🔑 Authentication & Authorization

### JWT Fields (from Portal)

The JWT token issued by Portal contains:

```json
{
  "sub": "user@example.com",
  "tenant_id": "acme",
  "tenant_role": "admin",           // "global_admin" | "admin" | "member"
  "app_admin_for": ["app1", "app2"] // List of apps user is admin of
}
```

### Permission Helper Functions

Located in `backend/auth/permissions.py`:

#### JWT-Based Checks

```python
from backend.auth.permissions import (
    is_global_admin,
    is_tenant_admin,
    is_app_admin,
    get_user_id,
    get_user_tenant_id
)

# Check if user is Global Admin
if is_global_admin(user_data):
    # Full access to everything
    pass

# Check if user is Tenant Admin (of their tenant)
if is_tenant_admin(user_data):
    # Can manage tenant users and settings
    pass

# Check if user is Tenant Admin of a specific tenant
if is_tenant_admin(user_data, tenant_id="acme"):
    # Can manage users in "acme" tenant
    pass

# Check if user is App Admin (of this app)
if is_app_admin(user_data, app_id="PramaIA-PrivacyManager"):
    # Can manage permissions for this app
    pass
```

#### App-Level Role Checks

```python
from backend.auth.permissions import (
    get_user_app_roles,
    has_app_role,
    user_can
)

# Get all app roles for a user
roles = get_user_app_roles("user@example.com", "acme", db)

# Check if user has a specific role
if has_app_role("user@example.com", "acme", "operator", db):
    # User has "operator" role
    pass

# Check if user has a specific permission
if user_can("user@example.com", "acme", "can_delete", db):
    # User can delete resources
    pass

# Check resource-specific permission
if user_can("user@example.com", "acme", "read", db, 
            resource_type="category", resource_id="5"):
    # User can read category 5
    pass
```

---

## 🛡️ FastAPI Dependency Decorators

Use these decorators to protect endpoints:

### Require Global Admin

```python
from backend.auth.permissions import require_global_admin

@router.get("/admin/global-settings")
def get_global_settings(
    user: dict = Depends(require_global_admin)
):
    # Only Global Admins can access this
    return {"message": "Global settings"}
```

### Require Tenant Admin

```python
from backend.auth.permissions import require_tenant_admin

@router.get("/tenants/{tenant_id}/settings")
def get_tenant_settings(
    tenant_id: str,
    user: dict = Depends(require_tenant_admin())
):
    # User must be Tenant Admin of their own tenant
    return {"tenant_id": tenant_id}
```

### Require App Admin

```python
from backend.auth.permissions import require_app_admin

@router.post("/permissions/grant")
def grant_permission(
    user: dict = Depends(require_app_admin("PramaIA-PrivacyManager"))
):
    # User must be App Admin of this app
    return {"message": "Permission granted"}
```

### Require Specific Permission

```python
from backend.auth.permissions import require_permission

@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    user: dict = Depends(require_permission("can_delete", "item"))
):
    # User must have "can_delete" permission for "item" resource type
    return {"message": f"Item {item_id} deleted"}
```

---

## 🌐 API Endpoints

All endpoints are prefixed with `/api/permissions`.

### Roles

- `GET /api/permissions/roles` - List all roles
- `POST /api/permissions/roles` - Create a role (App Admin only)
- `PATCH /api/permissions/roles/{role_id}` - Update a role (App Admin only)
- `DELETE /api/permissions/roles/{role_id}` - Delete a role (App Admin only)

### User Role Assignments

- `GET /api/permissions/users/{user_id}/roles` - Get roles for a user
- `POST /api/permissions/users/assign-role` - Assign role to user (App Admin only)
- `DELETE /api/permissions/users/{user_id}/roles/{role_id}` - Revoke role (App Admin only)

### User Permissions

- `GET /api/permissions/users/{user_id}/permissions` - Get granular permissions

### Audit Logs

- `GET /api/permissions/audit-logs` - Get audit logs (App Admin only)

### Current User Info

- `GET /api/permissions/me` - Get current user's permission info (JWT + app roles + granular permissions)

---

## 🖥️ Frontend Integration

### Admin Panel

The permissions management UI is in the **Settings** page, under the **Permissions** tab.

Location: `frontend/src/components/PermissionsPanel.tsx`

Features:
- **Roles Tab**: Create, edit, delete roles
- **User Roles Tab**: Assign/revoke roles to users
- **Audit Logs Tab**: View permission change history

### Accessing the UI

Only users with **Global Admin**, **Tenant Admin**, or **App Admin** privileges can access the Settings page.

This is enforced in `frontend/src/services/authGuard.ts`:

```typescript
export function canAccessSettings(): boolean {
  const userData = getUserData();
  if (!userData) return false;

  return (
    userData.tenant_role === 'global_admin' ||
    userData.tenant_role === 'admin' ||
    (userData.app_admin_for && userData.app_admin_for.length > 0)
  );
}
```

---

## 🔧 Usage Examples

### Example 1: Create a "Viewer" Role

```python
from backend.services.permissions_service import PermissionsService

role = PermissionsService.create_role(
    role_id="viewer",
    name="Viewer",
    description="Can only view resources",
    permissions={
        "can_create": False,
        "can_read": True,
        "can_update": False,
        "can_delete": False,
        "can_assign": False,
        "can_configure": False,
    },
    tenant_id=None,  # Global role
    db=db
)
```

### Example 2: Assign "Viewer" Role to a User

```python
result = PermissionsService.assign_role_to_user(
    user_id="john@acme.com",
    tenant_id="acme",
    role_id="viewer",
    granted_by_user_id="admin@acme.com",
    db=db
)
```

### Example 3: Check Permission in Code

```python
from backend.auth.permissions import user_can

if user_can("john@acme.com", "acme", "can_delete", db):
    # User can delete
    delete_resource(resource_id)
else:
    raise HTTPException(status_code=403, detail="Permission denied")
```

### Example 4: Protect an Endpoint

```python
from backend.auth.permissions import require_permission

@router.post("/tickets")
def create_ticket(
    ticket: TicketCreate,
    user: dict = Depends(require_permission("can_create", "ticket"))
):
    # Only users with "can_create" permission for "ticket" can create tickets
    return create_new_ticket(ticket)
```

---

## 🔄 Permission Hierarchy

The system evaluates permissions in this order:

1. **Global Admin** → Full access (bypass all checks)
2. **Tenant Admin** → Can manage tenant users and settings
3. **App Admin** → Can manage app permissions
4. **App-Level Roles** → Check role-based permissions
5. **Granular Permissions** → Check resource-specific permissions

If a user has **Global Admin**, they bypass all other checks.

---

## 📖 Best Practices

### ✅ DO

- Use **JWT-based roles** (Global Admin, Tenant Admin, App Admin) for high-level access control
- Use **App-Level Roles** for role-based permissions within the app
- Use **Granular Permissions** for fine-grained resource-specific access control
- Always log permission changes using `AppAuditLog`
- Use dependency decorators (`require_global_admin`, `require_app_admin`, etc.) to protect endpoints
- Check permissions at the **service layer** for complex business logic
- Document custom roles and their purposes

### ❌ DON'T

- Hardcode user emails or roles in the code
- Skip permission checks by assuming "admin can do everything"
- Grant overly broad permissions (e.g., giving everyone `can_delete`)
- Store sensitive data in `permissions_json` (use granular permissions instead)
- Forget to check tenant isolation (always filter by `tenant_id`)

---

## 🧪 Testing Permissions

### Test Global Admin Access

```python
user_data = {
    "sub": "admin@platform.com",
    "tenant_role": "global_admin"
}

assert is_global_admin(user_data) == True
```

### Test App Role Assignment

```python
# Assign role
PermissionsService.assign_role_to_user(
    user_id="user@acme.com",
    tenant_id="acme",
    role_id="operator",
    granted_by_user_id="admin@acme.com",
    db=db
)

# Verify
assert has_app_role("user@acme.com", "acme", "operator", db) == True
```

### Test Granular Permission

```python
# Grant permission
grant_permission(
    user_id="user@acme.com",
    tenant_id="acme",
    resource_type="category",
    permission="read",
    granted_by="admin@acme.com",
    db=db,
    resource_id="5"
)

# Verify
assert user_can("user@acme.com", "acme", "read", db, "category", "5") == True
```

---

## 🚀 Next Steps

1. **Define Your App Roles**: Create roles specific to your app's needs
2. **Protect Endpoints**: Add permission decorators to sensitive routes
3. **Test Authorization**: Verify permission checks work correctly
4. **Document Roles**: Update this guide with your custom roles
5. **Monitor Audit Logs**: Regularly review permission changes

---

## 📝 Notes

- **Tenant Isolation**: All permissions are scoped to `tenant_id` → Users can only access resources in their tenant
- **App Admin Delegation**: Tenant Admins can promote users to App Admin via Portal
- **Permission Inheritance**: Global Admins are also Tenant Admins and App Admins
- **Granular Permissions are Additive**: If a user has a role with `can_create=True`, they can create even if they don't have a granular permission for it

---

For questions or issues, contact the Platform Team or refer to the [Portal JWT documentation](PORTAL_INTEGRATION.md).
