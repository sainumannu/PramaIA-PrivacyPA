"""
Esempio di router con endpoint protetto da verifica licenza.

Questo file dimostra come proteggere funzionalità con il license guard.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.auth.license_guard import require_license
from backend.db.database import get_db

router = APIRouter(prefix="/api/protected", tags=["protected-example"])


class ProtectedData(BaseModel):
    """Esempio di dato accessibile solo con modulo licenziato."""
    
    message: str
    module: str
    user: str


@router.get("/albo", response_model=ProtectedData)
@require_license(required_module="albo")
async def get_albo_data(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint accessibile solo se il modulo "albo" è licenziato.
    
    Il decorator @require_license verifica automaticamente:
    - Firma valida del file licenza
    - Date di validità e manutenzione
    - Fingerprint ambiente
    - Presenza modulo "albo" negli entitlements
    - Limiti utenti e istanze
    
    Se una verifica fallisce, ritorna HTTP 403 con dettagli.
    """
    return ProtectedData(
        message="Dati del modulo Albo - accessibile solo con licenza valida",
        module="albo",
        user=user.email,
    )


@router.get("/protocollo", response_model=ProtectedData)
@require_license(required_module="protocollo")
async def get_protocollo_data(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint accessibile solo se il modulo "protocollo" è licenziato.
    """
    return ProtectedData(
        message="Dati del modulo Protocollo - richiede licenza specifica",
        module="protocollo",
        user=user.email,
    )


@router.get("/any-module", response_model=ProtectedData)
@require_license()  # Verifica licenza generica senza modulo specifico
async def get_generic_data(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint con verifica licenza generica (senza modulo specifico).
    
    Verifica solo:
    - Validità firma e date
    - Ambiente corretto
    - Limiti utenti/istanze
    
    Non richiede un modulo specifico.
    """
    return ProtectedData(
        message="Dati generici - richiede solo licenza valida",
        module="generic",
        user=user.email,
    )
