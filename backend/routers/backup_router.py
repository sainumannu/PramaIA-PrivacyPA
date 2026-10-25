"""
Backup e ripristino (§57) — solo App Admin.

GET    /api/backup/config, PUT /api/backup/config      configurazione (ora, copie da conservare)
GET    /api/backup/list                                elenco dei backup (con cifratura e archivio dei documenti abbinato)
GET    /api/backup/logs                                registro delle operazioni
POST   /api/backup/create                              backup manuale
POST   /api/backup/verify                              prova di ripristino in una copia temporanea (non tocca il database in uso)
POST   /api/backup/restore                             ripristino del database (verifica + backup di sicurezza)
POST   /api/backup/restore-documents                   ripristino dei documenti senza sovrascrivere file diversi
GET    /api/backup/{filename}, DELETE /api/backup/{filename}
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.permissions import require_app_admin
from backend.auth.portal_jwt import TokenPayload
from backend.db.database import get_db
from backend.services.backup_crypto import BackupCryptoError
from backend.services.backup_scheduler import reload_backup_schedule
from backend.services.backup_service import DB_NAME, DOCS_NAME, BackupError, BackupService
from backend.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/backup", tags=["Backup"])


# ==================== MODELS ====================

class BackupConfigResponse(BaseModel):
    enabled: bool
    schedule_hour: int
    retention_count: int
    last_backup_at: Optional[str] = None
    encryption: bool = False
    offsite: bool = False


class BackupConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule_hour: Optional[int] = Field(None, ge=0, le=23)
    retention_count: Optional[int] = Field(None, ge=1, le=30)


class BackupInfoResponse(BaseModel):
    filename: str
    size: int
    created_at: str
    type: str
    encrypted: bool = False
    documents_filename: Optional[str] = None
    offsite: Optional[dict] = None


class BackupCreateResponse(BaseModel):
    success: bool
    message: str
    backup: BackupInfoResponse


class FilenameRequest(BaseModel):
    filename: str = Field(..., max_length=120)


class BackupRestoreRequest(FilenameRequest):
    confirm: bool = Field(..., description="Deve essere true per confermare il ripristino")


class BackupRestoreResponse(BaseModel):
    success: bool
    message: str
    filename: str
    safety_backup: str
    warnings: List[str] = []


class BackupLogResponse(BaseModel):
    id: int
    operation: str
    status: str
    filename: Optional[str]
    file_size: Optional[int]
    error_message: Optional[str]
    created_by: Optional[str]
    created_at: str


def _config_response(config) -> BackupConfigResponse:
    import os

    return BackupConfigResponse(
        enabled=config.enabled, schedule_hour=config.schedule_hour, retention_count=config.retention_count,
        last_backup_at=config.last_backup_at.isoformat() if config.last_backup_at else None,
        encryption=bool(os.getenv("BACKUP_ENCRYPTION_KEY")), offsite=bool(os.getenv("BACKUP_OFFSITE_DIR")),
    )


def _errors(e: Exception) -> HTTPException:
    if isinstance(e, FileNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    if isinstance(e, (BackupError, BackupCryptoError)):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    logger.error(f"Errore di backup: {e}", exc_info=True)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Operazione di backup fallita (dettagli nel registro)")


# ==================== ENDPOINTS ====================
# Le rotte fisse vengono prima di `/{filename}`, altrimenti quest'ultima le intercetterebbe.

@router.get("/config", response_model=BackupConfigResponse)
async def get_backup_config(db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    return _config_response(await BackupService(db).get_config())


@router.put("/config", response_model=BackupConfigResponse)
async def update_backup_config(config_update: BackupConfigUpdate, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    try:
        config = await BackupService(db).update_config(config_update.enabled, config_update.schedule_hour, config_update.retention_count)
    except Exception as e:
        raise _errors(e)
    await reload_backup_schedule()
    logger.info(f"Configurazione backup aggiornata da {user.email}")
    return _config_response(config)


@router.get("/list", response_model=List[BackupInfoResponse])
async def list_backups(db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    return [BackupInfoResponse(**b) for b in BackupService(db).list_backups()]


@router.get("/logs", response_model=List[BackupLogResponse])
async def get_backup_logs(limit: int = 50, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    logs = await BackupService(db).get_backup_logs(limit=max(1, min(limit, 200)))
    return [BackupLogResponse(id=l.id, operation=l.operation, status=l.status, filename=l.filename, file_size=l.file_size,
                              error_message=l.error_message, created_by=l.created_by, created_at=l.created_at.isoformat() if l.created_at else "") for l in logs]


@router.post("/create", response_model=BackupCreateResponse)
async def create_backup(db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    try:
        info = await BackupService(db).create_backup(user_email=user.email, scheduled=False)
    except Exception as e:
        raise _errors(e)
    logger.info(f"Backup manuale creato da {user.email}: {info['filename']}")
    return BackupCreateResponse(success=True, message=f"Backup creato: {info['filename']}", backup=BackupInfoResponse(**info))


@router.post("/verify")
async def verify_backup(body: FilenameRequest, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    """Prova di ripristino: decifra, controlla integrità, schema, dati e coerenza dei documenti in una copia temporanea."""
    try:
        return await BackupService(db).verify_backup(body.filename, user_email=user.email)
    except Exception as e:
        raise _errors(e)


@router.post("/restore", response_model=BackupRestoreResponse)
async def restore_backup(body: BackupRestoreRequest, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    """⚠️ Sostituisce il database. Il backup viene verificato prima, e si crea un backup di sicurezza dello stato attuale."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Il ripristino richiede la conferma esplicita (confirm=true)")
    try:
        info = await BackupService(db).restore_backup(body.filename, user_email=user.email)
    except Exception as e:
        raise _errors(e)
    logger.warning(f"Database ripristinato da {user.email} con {body.filename}")
    return BackupRestoreResponse(success=True, message="Database ripristinato. Riavviare l'applicazione.", filename=info["filename"],
                                 safety_backup=info["safety_backup"], warnings=info["warnings"])


@router.post("/restore-documents")
async def restore_documents(body: BackupRestoreRequest, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    """Estrae l'archivio dei documenti senza sovrascrivere i file già presenti e diversi (i conflitti sono elencati)."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Il ripristino richiede la conferma esplicita (confirm=true)")
    try:
        return await BackupService(db).restore_documents(body.filename, user_email=user.email)
    except Exception as e:
        raise _errors(e)


@router.get("/{filename}")
async def download_backup(filename: str, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    """Scarica un file di backup (database o archivio dei documenti). Il nome è validato: niente percorsi arbitrari."""
    service = BackupService(db)
    try:
        path = service._path(filename, DB_NAME if DB_NAME.match(filename) else DOCS_NAME)
    except Exception as e:
        raise _errors(e)
    logger.info(f"Backup scaricato da {user.email}: {filename}")
    return FileResponse(path=str(path), filename=filename, media_type="application/octet-stream")


@router.delete("/{filename}")
async def delete_backup(filename: str, db: AsyncSession = Depends(get_db), user: TokenPayload = Depends(require_app_admin())):
    """Elimina un backup e l'archivio dei documenti abbinato."""
    try:
        await BackupService(db).delete_backup(filename, user_email=user.email)
    except Exception as e:
        raise _errors(e)
    return {"success": True, "message": f"Backup eliminato: {filename}"}
