"""
Settings Router - Gestione impostazioni applicazione e team utenti.

Questo router è accessibile solo a:
- Global Admin (is_admin = true)
- Tenant Admin (tenant_role = 'admin')
- App Admin (app_admin_for contiene questa app)
"""
import os
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional
import httpx

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.db.database import get_db
from backend.services.privacypa_demo_data_service import DemoDataError, DemoDataService
from backend.services.settings_service import SettingsService

router = APIRouter(prefix="/api/settings", tags=["settings"])

APP_ID = os.getenv("APP_ID", "PramaIA-PrivacyManager")


# ===== SCHEMAS =====

class TeamMemberCreate(BaseModel):
    user_id: str
    app_role: str = Field(..., pattern="^(user|operator|admin)$")


class TeamMemberResponse(BaseModel):
    id: int
    user_id: str
    email: str
    display_name: Optional[str]
    app_role: str
    is_active: bool
    added_at: Optional[str]

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    roles: list[str]
    is_admin: bool


# ===== AUTHORIZATION =====

def require_settings_access(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """
    Verifica che l'utente possa accedere alle impostazioni.
    Permesso a: Global Admin, Tenant Admin, App Admin.
    """
    # Global admin
    if user.is_admin:
        return user
    
    # Tenant admin
    tenant_role = user.raw.get("tenant_role", "member")
    if tenant_role in ("admin", "global_admin"):
        return user
    
    # App admin per questa app
    app_admin_for = user.raw.get("app_admin_for", [])
    if APP_ID in app_admin_for:
        return user
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Accesso alle impostazioni riservato ad Admin"
    )


# ===== ENDPOINTS: UTENTI DAL PORTAL =====

@router.get("/users", response_model=list[UserResponse])
async def get_available_users(
    user: TokenPayload = Depends(require_settings_access),
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Recupera gli utenti dal Portal che hanno accesso a questa app.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token di autenticazione mancante"
        )
    
    svc = SettingsService(db)
    try:
        users = await svc.get_portal_users(authorization)
        return users
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Errore Portal API: {e.response.text}"
        )
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Impossibile contattare Portal: {str(e)}"
        )


# ===== ENDPOINTS: TEAM MANAGEMENT =====

@router.get("/team", response_model=list[TeamMemberResponse])
async def list_team_members(
    active_only: bool = True,
    user: TokenPayload = Depends(require_settings_access),
    db: AsyncSession = Depends(get_db),
):
    """Lista i membri del team dell'applicazione."""
    tenant_id = user.raw.get("tenant_id")
    svc = SettingsService(db)
    members = await svc.get_team_members(tenant_id=tenant_id, active_only=active_only)
    return [TeamMemberResponse(**m.to_dict()) for m in members]


@router.post("/team", response_model=TeamMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_team_member(
    body: TeamMemberCreate,
    user: TokenPayload = Depends(require_settings_access),
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggiunge un utente al team dell'applicazione."""
    # Verifica che l'utente esista nel Portal e abbia accesso all'app
    svc = SettingsService(db)
    
    try:
        portal_users = await svc.get_portal_users(authorization)
    except Exception:
        portal_users = []
    
    # Trova l'utente nel Portal
    portal_user = next((u for u in portal_users if u["id"] == body.user_id), None)
    if not portal_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utente non trovato o non autorizzato per questa app"
        )
    
    tenant_id = user.raw.get("tenant_id")
    member = await svc.add_team_member(
        user_id=body.user_id,
        email=portal_user["email"],
        display_name=portal_user.get("display_name"),
        app_role=body.app_role,
        tenant_id=tenant_id,
        added_by=user.email,
    )
    return TeamMemberResponse(**member.to_dict())


@router.delete("/team/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    member_id: int,
    user: TokenPayload = Depends(require_settings_access),
    db: AsyncSession = Depends(get_db),
):
    """Rimuove un utente dal team dell'applicazione."""
    svc = SettingsService(db)
    removed = await svc.remove_team_member(member_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membro del team non trovato"
        )


# ===== ENDPOINTS: DATI DI ESEMPIO / RESET =====

def _tenant_id(user: TokenPayload) -> str:
    tenant_id = user.tenant_id or user.raw.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant non presente nel token")
    return str(tenant_id)


class DemoResetBody(BaseModel):
    section: str = Field(..., min_length=1, max_length=40)
    target: str = Field(..., pattern="^(data|documents|both)$")


@router.get("/demo-data")
async def list_demo_data(
    user: TokenPayload = Depends(require_settings_access),
    db: AsyncSession = Depends(get_db),
):
    """Conteggi per sezione (dati e documenti collegati) del tenant corrente."""
    svc = DemoDataService(db)
    sections = await svc.inventory(_tenant_id(user))
    return {"sections": sections}


@router.post("/demo-data/seed")
async def seed_demo_data(
    user: TokenPayload = Depends(require_settings_access),
    db: AsyncSession = Depends(get_db),
):
    """Carica o completa i dati di esempio per il tenant corrente."""
    svc = DemoDataService(db)
    try:
        return await svc.seed(_tenant_id(user), user.sub or user.email or "settings")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/demo-data/reset")
async def reset_demo_data(
    body: DemoResetBody,
    user: TokenPayload = Depends(require_settings_access),
    db: AsyncSession = Depends(get_db),
):
    """Elimina i dati o i documenti di una sezione (o di tutte) per il tenant corrente."""
    svc = DemoDataService(db)
    try:
        return await svc.reset(_tenant_id(user), user.sub or user.email or "settings", body.section, body.target)
    except DemoDataError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
