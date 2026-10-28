from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.db.database import get_db
from backend.services.license_service import LicenseService, LicenseCheckResult

router = APIRouter(prefix="/api/license", tags=["license"])


class LicenseStatusOut(BaseModel):
    """Response model per status licenza."""
    
    valid: bool
    code: str
    message: str
    license_id: str | None = None
    expires_at: str | None = None
    maintenance_until: str | None = None
    allowed_modules: list[str] | None = None
    payload_checksum: str | None = None
    last_sync_at: str | None = None 
    sync_status: str | None = None


@router.get("/modules")
async def get_modules():
    """
    Catalogo dei moduli licenziabili di PrivacyPA, per il Portal (standard LICENZE_MODULI_STANDARD.md).
    Pubblico: elenca solo i nomi da usare in entitlements.modules, nessun dato dell'installazione.
    """
    from backend.auth.privacypa_license import catalog

    return catalog()


@router.get("/status", response_model=LicenseStatusOut)
async def get_license_status(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint admin-only per ottenere lo stato della licenza.
    
    Mostra:
    - Validità e scadenze
    - Moduli abilitati
    - Checksum payload corrente
    - Ultimo sync con Portal
    - Status ultima sincronizzazione
    
    Accessibile solo a Global Admin, Tenant Admin, o App Admin.
    """
    # Controllo permessi: solo admin
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access license status",
        )
    
    license_svc = LicenseService(db)
    result = await license_svc.verify_license()
    
    return LicenseStatusOut(**result.to_dict())


@router.post("/sync", response_model=LicenseStatusOut)
async def sync_license(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Forza il sync della licenza dal Portal.
    
    Scarica la licenza aggiornata dal Portal se disponibile,
    salva la copia locale e verifica validità.
    
    Accessibile solo a Global Admin, Tenant Admin, o App Admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can sync license",
        )
    
    license_svc = LicenseService(db)
    result = await license_svc.verify_license(
        force_refresh=True,
        sync_from_portal=True,
    )
    
    return LicenseStatusOut(**result.to_dict())


@router.post("/refresh", response_model=LicenseStatusOut)
async def refresh_license(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    (DEPRECATO: usa /sync invece)
    
    Forza il refresh della cache licenza.
    
    Accessibile solo a Global Admin, Tenant Admin, o App Admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can refresh license",
        )
    
    license_svc = LicenseService(db)
    result = await license_svc.verify_license(force_refresh=True)
    
    return LicenseStatusOut(**result.to_dict())
