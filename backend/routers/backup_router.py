"""
Backup router for database backup and restoration.

Endpoints:
- GET /api/backup/config - Get backup configuration
- PUT /api/backup/config - Update backup configuration  
- GET /api/backup/list - List available backups
- POST /api/backup/create - Create manual backup
- POST /api/backup/restore - Restore from backup
- DELETE /api/backup/{filename} - Delete backup
- GET /api/backup/logs - Get backup operation logs
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.auth.permissions import require_app_admin
from backend.services.backup_service import BackupService
from backend.services.backup_scheduler import reload_backup_schedule
from backend.utils.logging import get_logger

# Nome file backup atteso, es. backup_manual_20260917_231045.db — usato
# per validare il parametro {filename} ed evitare path traversal.
_BACKUP_FILENAME_RE = re.compile(r"^backup_(manual|scheduled)_[0-9]{8}_[0-9]{6}\.db$")

logger = get_logger(__name__)
router = APIRouter(prefix="/api/backup", tags=["Backup"])


# ==================== MODELS ====================

class BackupConfigResponse(BaseModel):
    """Backup configuration response."""
    enabled: bool
    schedule_hour: int
    retention_count: int
    last_backup_at: Optional[str] = None


class BackupConfigUpdate(BaseModel):
    """Backup configuration update request."""
    enabled: Optional[bool] = None
    schedule_hour: Optional[int] = Field(None, ge=0, le=23)
    retention_count: Optional[int] = Field(None, ge=1, le=30)


class BackupInfoResponse(BaseModel):
    """Backup file information."""
    filename: str
    size: int
    created_at: str
    type: str


class BackupCreateResponse(BaseModel):
    """Backup creation response."""
    success: bool
    message: str
    backup: BackupInfoResponse


class BackupRestoreRequest(BaseModel):
    """Backup restore request."""
    filename: str
    confirm: bool = Field(..., description="Must be true to confirm restore")


class BackupRestoreResponse(BaseModel):
    """Backup restore response."""
    success: bool
    message: str
    filename: str
    safety_backup: str


class BackupLogResponse(BaseModel):
    """Backup log entry."""
    id: int
    operation: str
    status: str
    filename: Optional[str]
    file_size: Optional[int]
    error_message: Optional[str]
    created_by: Optional[str]
    created_at: str


# ==================== ENDPOINTS ====================

@router.get("/config", response_model=BackupConfigResponse)
async def get_backup_config(
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Get backup configuration.
    
    Only accessible to App Admins.
    """
    service = BackupService(db)
    config = service.get_config()
    
    return BackupConfigResponse(
        enabled=config.enabled,
        schedule_hour=config.schedule_hour,
        retention_count=config.retention_count,
        last_backup_at=config.last_backup_at.isoformat() if config.last_backup_at else None
    )


@router.put("/config", response_model=BackupConfigResponse)
async def update_backup_config(
    config_update: BackupConfigUpdate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Update backup configuration.
    
    Only accessible to App Admins.
    """
    service = BackupService(db)
    
    try:
        config = service.update_config(
            enabled=config_update.enabled,
            schedule_hour=config_update.schedule_hour,
            retention_count=config_update.retention_count
        )
        
        # Reload scheduler to apply new configuration
        await reload_backup_schedule()
        
        logger.info(f"Backup config updated by {user.email}")
        
        return BackupConfigResponse(
            enabled=config.enabled,
            schedule_hour=config.schedule_hour,
            retention_count=config.retention_count,
            last_backup_at=config.last_backup_at.isoformat() if config.last_backup_at else None
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/list", response_model=List[BackupInfoResponse])
async def list_backups(
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    List all available backups.
    
    Only accessible to App Admins.
    """
    service = BackupService(db)
    backups = service.list_backups()
    
    return [
        BackupInfoResponse(
            filename=b['filename'],
            size=b['size'],
            created_at=b['created_at'],
            type=b['type']
        )
        for b in backups
    ]


@router.get("/{filename}")
async def download_backup(
    filename: str,
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Download a backup file.

    Only accessible to App Admins. `filename` is validated against the
    backup naming pattern to prevent path traversal.
    """
    if not _BACKUP_FILENAME_RE.match(filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid backup filename")

    backup_path = BackupService.BACKUP_DIR / filename
    if not backup_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")

    logger.info(f"Backup downloaded by {user.email}: {filename}")

    return FileResponse(
        path=str(backup_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.post("/create", response_model=BackupCreateResponse)
async def create_backup(
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Create a manual backup.
    
    Only accessible to App Admins.
    """
    service = BackupService(db)
    
    try:
        backup_info = service.create_backup(user_email=user.email, scheduled=False)
        
        logger.info(f"Manual backup created by {user.email}: {backup_info['filename']}")
        
        return BackupCreateResponse(
            success=True,
            message=f"Backup created successfully: {backup_info['filename']}",
            backup=BackupInfoResponse(
                filename=backup_info['filename'],
                size=backup_info['size'],
                created_at=backup_info['created_at'],
                type=backup_info['type']
            )
        )
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup failed: {str(e)}"
        )


@router.post("/restore", response_model=BackupRestoreResponse)
async def restore_backup(
    restore_request: BackupRestoreRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Restore database from backup.
    
    ⚠️ WARNING: This will replace the current database!
    A safety backup of the current database is created before restore.
    
    Only accessible to App Admins.
    """
    if not restore_request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Restore requires explicit confirmation (confirm=true)"
        )
    if not _BACKUP_FILENAME_RE.match(restore_request.filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid backup filename")

    service = BackupService(db)
    
    try:
        restore_info = service.restore_backup(
            filename=restore_request.filename,
            user_email=user.email
        )
        
        logger.warning(f"Database restored by {user.email} from {restore_request.filename}")
        
        return BackupRestoreResponse(
            success=True,
            message=f"Database restored successfully. Application restart recommended.",
            filename=restore_info['filename'],
            safety_backup=restore_info['safety_backup']
        )
        
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Restore failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Restore failed: {str(e)}"
        )


@router.delete("/{filename}")
async def delete_backup(
    filename: str,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Delete a backup file.

    Only accessible to App Admins.
    """
    if not _BACKUP_FILENAME_RE.match(filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid backup filename")

    service = BackupService(db)

    try:
        service.delete_backup(filename, user_email=user.email)
        
        logger.info(f"Backup deleted by {user.email}: {filename}")
        
        return {"success": True, "message": f"Backup deleted: {filename}"}
        
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.get("/logs", response_model=List[BackupLogResponse])
async def get_backup_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_app_admin())
):
    """
    Get backup operation logs.
    
    Only accessible to App Admins.
    """
    service = BackupService(db)
    logs = service.get_backup_logs(limit=limit)
    
    return [
        BackupLogResponse(
            id=log.id,
            operation=log.operation,
            status=log.status,
            filename=log.filename,
            file_size=log.file_size,
            error_message=log.error_message,
            created_by=log.created_by,
            created_at=log.created_at.isoformat()
        )
        for log in logs
    ]
