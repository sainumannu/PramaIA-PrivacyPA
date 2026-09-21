import os
import httpx
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from backend.models.team_member import AppTeamMember

logger = logging.getLogger("PramaIA-PrivacyManager.settings")

PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://localhost:8080")
APP_ID = os.getenv("APP_ID", "PramaIA-PrivacyManager")


class SettingsService:
    """
    Service per la gestione delle impostazioni dell'applicazione.
    Include gestione team/utenti e configurazioni.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ========================
    # TEAM MANAGEMENT
    # ========================

    async def get_team_members(self, tenant_id: Optional[str] = None, active_only: bool = True) -> list[AppTeamMember]:
        """Recupera i membri del team dell'applicazione."""
        query = select(AppTeamMember)
        
        if tenant_id:
            query = query.where(AppTeamMember.tenant_id == tenant_id)
        
        if active_only:
            query = query.where(AppTeamMember.is_active == True)
        
        result = await self.db.execute(query.order_by(AppTeamMember.added_at.desc()))
        return list(result.scalars().all())

    async def get_team_member_by_user_id(self, user_id: str) -> Optional[AppTeamMember]:
        """Recupera un membro del team per user_id."""
        result = await self.db.execute(
            select(AppTeamMember).where(AppTeamMember.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def add_team_member(
        self,
        user_id: str,
        email: str,
        app_role: str,
        display_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        added_by: Optional[str] = None,
    ) -> AppTeamMember:
        """Aggiunge un utente al team dell'applicazione."""
        # Verifica se esiste già
        existing = await self.get_team_member_by_user_id(user_id)
        if existing:
            # Riattiva se disattivato
            existing.is_active = True
            existing.app_role = app_role
            if display_name:
                existing.display_name = display_name
            await self.db.flush()
            await self.db.refresh(existing)
            return existing
        
        member = AppTeamMember(
            user_id=user_id,
            email=email,
            display_name=display_name,
            app_role=app_role,
            tenant_id=tenant_id,
            added_by=added_by,
        )
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def remove_team_member(self, member_id: int) -> bool:
        """Rimuove (disattiva) un membro dal team."""
        result = await self.db.execute(
            select(AppTeamMember).where(AppTeamMember.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            return False
        
        member.is_active = False
        await self.db.flush()
        return True

    async def delete_team_member(self, member_id: int) -> bool:
        """Elimina definitivamente un membro dal team."""
        result = await self.db.execute(
            select(AppTeamMember).where(AppTeamMember.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            return False
        
        await self.db.delete(member)
        return True

    # ========================
    # PORTAL USERS
    # ========================

    async def get_portal_users(self, authorization: str) -> list[dict]:
        """
        Recupera gli utenti dal Portal che hanno accesso a questa app.
        Filtra per app_id in apps[].
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{PORTAL_API_URL}/api/users/",
                    headers={"Authorization": authorization},
                    timeout=10.0
                )
                response.raise_for_status()
                all_users = response.json()
                
                # Filtra utenti con accesso a questa app
                app_users = [
                    {
                        "id": user.get("id"),
                        "email": user.get("email"),
                        "display_name": user.get("display_name", user.get("email")),
                        "roles": user.get("roles", []),
                        "is_admin": user.get("is_admin", False),
                    }
                    for user in all_users
                    if APP_ID in user.get("apps", [])
                ]
                
                logger.info(f"Recuperati {len(app_users)}/{len(all_users)} utenti con accesso a {APP_ID}")
                return app_users
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Portal API errore {e.response.status_code}: {e.response.text}")
                raise
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.error(f"Impossibile contattare Portal API: {e}")
                raise
