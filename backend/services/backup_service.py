"""
Backup service for database backup and restoration.

Provides:
- Manual and scheduled database backups
- Backup file management with retention policy
- Database restoration from backup
- Backup integrity verification
"""

import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from backend.models.backup_models import BackupConfig, BackupLog
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class BackupService:
    """Service for database backup and restoration operations."""
    
    BACKUP_DIR = Path("backups")
    MAX_RETENTION = 30
    
    def __init__(self, db: Session, db_path: Optional[str] = None):
        """
        Initialize backup service.
        
        Args:
            db: Database session
            db_path: Path to database file (default: from DATABASE_URL env)
        """
        self.db = db
        
        # Get database path from env or parameter
        if db_path is None:
            db_url = os.getenv("DATABASE_URL", "sqlite:///./PramaIA-PrivacyManager.db")
            # Extract file path from sqlite:/// URL
            db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
            if db_path.startswith("./"):
                db_path = db_path[2:]
        
        self.db_path = Path(db_path)
        
        # Create backup directory
        self.BACKUP_DIR.mkdir(exist_ok=True)
        
        logger.info(f"BackupService initialized: db={self.db_path}, backup_dir={self.BACKUP_DIR}")
    
    def get_config(self) -> Optional[BackupConfig]:
        """Get backup configuration."""
        config = self.db.query(BackupConfig).first()
        
        # Create default config if not exists
        if not config:
            config = BackupConfig(
                enabled=True,
                schedule_hour=2,  # 2 AM
                retention_count=7
            )
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            logger.info("Created default backup configuration")
        
        return config
    
    def update_config(
        self,
        enabled: Optional[bool] = None,
        schedule_hour: Optional[int] = None,
        retention_count: Optional[int] = None
    ) -> BackupConfig:
        """
        Update backup configuration.
        
        Args:
            enabled: Enable/disable scheduled backups
            schedule_hour: Hour of day for backups (0-23)
            retention_count: Number of backups to keep (max 30)
            
        Returns:
            Updated configuration
        """
        config = self.get_config()
        
        if enabled is not None:
            config.enabled = enabled
        
        if schedule_hour is not None:
            if not 0 <= schedule_hour <= 23:
                raise ValueError("schedule_hour must be between 0 and 23")
            config.schedule_hour = schedule_hour
        
        if retention_count is not None:
            if not 1 <= retention_count <= self.MAX_RETENTION:
                raise ValueError(f"retention_count must be between 1 and {self.MAX_RETENTION}")
            config.retention_count = retention_count
        
        self.db.commit()
        self.db.refresh(config)
        
        logger.info(f"Updated backup config: enabled={config.enabled}, hour={config.schedule_hour}, retention={config.retention_count}")
        
        return config
    
    def create_backup(self, user_email: Optional[str] = None, scheduled: bool = False) -> Dict:
        """
        Create a database backup.
        
        Args:
            user_email: Email of user who triggered backup
            scheduled: Whether this is a scheduled backup
            
        Returns:
            Dict with backup details (filename, size, etc.)
        """
        try:
            # Generate backup filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_type = "scheduled" if scheduled else "manual"
            filename = f"backup_{backup_type}_{timestamp}.db"
            backup_path = self.BACKUP_DIR / filename
            
            logger.info(f"Creating backup: {filename} (user={user_email}, scheduled={scheduled})")
            
            # Verify source database exists
            if not self.db_path.exists():
                raise FileNotFoundError(f"Database file not found: {self.db_path}")
            
            # Create backup using SQLite backup API (safer than copy)
            self._backup_database(str(self.db_path), str(backup_path))
            
            # Get file size
            file_size = backup_path.stat().st_size
            
            # Update last backup time
            config = self.get_config()
            config.last_backup_at = datetime.now()
            self.db.commit()
            
            # Log success
            log_entry = BackupLog(
                operation="backup",
                status="success",
                filename=filename,
                file_size=file_size,
                created_by=user_email
            )
            self.db.add(log_entry)
            self.db.commit()
            
            # Apply retention policy
            self._apply_retention_policy()
            
            logger.info(f"Backup created successfully: {filename} ({file_size} bytes)")
            
            return {
                "filename": filename,
                "size": file_size,
                "created_at": datetime.now().isoformat(),
                "type": backup_type
            }
            
        except Exception as e:
            logger.error(f"Backup failed: {e}", exc_info=True)
            
            # Log error
            log_entry = BackupLog(
                operation="backup",
                status="error",
                error_message=str(e),
                created_by=user_email
            )
            self.db.add(log_entry)
            self.db.commit()
            
            raise
    
    def restore_backup(self, filename: str, user_email: Optional[str] = None) -> Dict:
        """
        Restore database from backup.
        
        Args:
            filename: Backup filename to restore
            user_email: Email of user who triggered restore
            
        Returns:
            Dict with restore details
        """
        try:
            backup_path = self.BACKUP_DIR / filename
            
            logger.warning(f"Restoring backup: {filename} (user={user_email})")
            
            # Verify backup exists
            if not backup_path.exists():
                raise FileNotFoundError(f"Backup file not found: {filename}")
            
            # Verify backup integrity
            if not self._verify_backup(str(backup_path)):
                raise ValueError(f"Backup file is corrupted: {filename}")
            
            # Create a backup of current database before restore
            current_backup = self.create_backup(user_email=user_email)
            logger.info(f"Created safety backup before restore: {current_backup['filename']}")
            
            # Close all database connections (FastAPI handles reconnection)
            # Note: In production, you might want to put app in maintenance mode
            
            # Restore database
            shutil.copy2(backup_path, self.db_path)
            
            # Log success
            log_entry = BackupLog(
                operation="restore",
                status="success",
                filename=filename,
                created_by=user_email
            )
            self.db.add(log_entry)
            self.db.commit()
            
            logger.warning(f"Database restored from backup: {filename}")
            
            return {
                "filename": filename,
                "restored_at": datetime.now().isoformat(),
                "safety_backup": current_backup['filename']
            }
            
        except Exception as e:
            logger.error(f"Restore failed: {e}", exc_info=True)
            
            # Log error
            log_entry = BackupLog(
                operation="restore",
                status="error",
                filename=filename,
                error_message=str(e),
                created_by=user_email
            )
            self.db.add(log_entry)
            self.db.commit()
            
            raise
    
    def list_backups(self) -> List[Dict]:
        """
        List all available backups.
        
        Returns:
            List of backup files with metadata
        """
        backups = []
        
        for backup_file in sorted(self.BACKUP_DIR.glob("backup_*.db"), reverse=True):
            stat = backup_file.stat()
            backups.append({
                "filename": backup_file.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "type": "scheduled" if "scheduled" in backup_file.name else "manual"
            })
        
        return backups
    
    def delete_backup(self, filename: str, user_email: Optional[str] = None) -> bool:
        """
        Delete a backup file.
        
        Args:
            filename: Backup filename to delete
            user_email: Email of user who triggered deletion
            
        Returns:
            True if deleted successfully
        """
        backup_path = self.BACKUP_DIR / filename
        
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {filename}")
        
        backup_path.unlink()
        logger.info(f"Backup deleted: {filename} (user={user_email})")
        
        return True
    
    def get_backup_logs(self, limit: int = 50) -> List[BackupLog]:
        """Get recent backup logs."""
        return (
            self.db.query(BackupLog)
            .order_by(BackupLog.created_at.desc())
            .limit(limit)
            .all()
        )
    
    # Private helper methods
    
    def _backup_database(self, source: str, destination: str):
        """
        Backup SQLite database using SQLite backup API.
        
        This is safer than simple file copy as it handles locked databases.
        """
        # Connect to source and destination
        src_conn = sqlite3.connect(source)
        dst_conn = sqlite3.connect(destination)
        
        try:
            # Perform backup
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
    
    def _verify_backup(self, backup_path: str) -> bool:
        """
        Verify backup file integrity.
        
        Args:
            backup_path: Path to backup file
            
        Returns:
            True if backup is valid
        """
        try:
            conn = sqlite3.connect(backup_path)
            cursor = conn.cursor()
            
            # Run integrity check
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            
            conn.close()
            
            return result[0] == "ok"
            
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False
    
    def _apply_retention_policy(self):
        """Delete old backups based on retention policy."""
        config = self.get_config()
        backups = self.list_backups()
        
        # Keep only retention_count most recent backups
        if len(backups) > config.retention_count:
            to_delete = backups[config.retention_count:]
            
            for backup in to_delete:
                try:
                    self.delete_backup(backup['filename'], user_email="system")
                    logger.info(f"Retention policy: deleted old backup {backup['filename']}")
                except Exception as e:
                    logger.error(f"Failed to delete old backup {backup['filename']}: {e}")
