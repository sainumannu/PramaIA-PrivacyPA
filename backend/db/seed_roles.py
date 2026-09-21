"""
Seed default app roles for development/testing.

This script creates default app-level roles:
- viewer: Read-only access
- operator: Can create and update
- manager: Full CRUD permissions

Run this after database initialization to populate default roles.
"""
import asyncio
from sqlalchemy import select
from backend.db.database import get_async_session
from backend.models.permissions_models import AppRole


async def seed_default_roles():
    """Create default app roles if they don't exist."""
    
    default_roles = [
        {
            "id": "viewer",
            "name": "Viewer",
            "description": "Read-only access to all resources",
            "tenant_id": None,  # Global role
            "can_create": False,
            "can_read": True,
            "can_update": False,
            "can_delete": False,
            "can_assign": False,
            "can_configure": False,
        },
        {
            "id": "operator",
            "name": "Operator",
            "description": "Can create and update resources, but not delete",
            "tenant_id": None,
            "can_create": True,
            "can_read": True,
            "can_update": True,
            "can_delete": False,
            "can_assign": False,
            "can_configure": False,
        },
        {
            "id": "manager",
            "name": "Manager",
            "description": "Full CRUD permissions and can assign resources",
            "tenant_id": None,
            "can_create": True,
            "can_read": True,
            "can_update": True,
            "can_delete": True,
            "can_assign": True,
            "can_configure": False,
        },
    ]
    
    async for session in get_async_session():
        created_count = 0
        
        for role_data in default_roles:
            # Check if role already exists
            result = await session.execute(
                select(AppRole).where(
                    AppRole.id == role_data["id"],
                    AppRole.tenant_id.is_(None)
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                print(f"⏭️  Role '{role_data['id']}' already exists, skipping")
                continue
            
            # Create new role
            role = AppRole(**role_data)
            session.add(role)
            created_count += 1
            print(f"✅ Created role: {role_data['id']} ({role_data['name']})")
        
        if created_count > 0:
            await session.commit()
            print(f"\n🎉 Seeded {created_count} default role(s)")
        else:
            print("\n✅ All default roles already exist")


if __name__ == "__main__":
    print("🌱 Seeding default app roles...\n")
    asyncio.run(seed_default_roles())
