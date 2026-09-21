"""
Backup models for database backup configuration.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from backend.db.database import Base


class BackupConfig(Base):
    """
    Configuration for scheduled database backups.
    
    Stores:
    - Backup schedule (hour of day)
    - Retention policy (number of backups to keep)
    - Enabled/disabled status
    """
    __tablename__ = "backup_config"
    
    id = Column(Integer, primary_key=True, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    schedule_hour = Column(Integer, default=2, nullable=False)  # 0-23, default 2 AM
    retention_count = Column(Integer, default=7, nullable=False)  # Max 30
    last_backup_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class BackupLog(Base):
    """
    Log of backup operations (creation and restoration).
    
    Tracks:
    - Backup/restore operations
    - Success/failure status
    - File paths
    - Error messages
    """
    __tablename__ = "backup_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    operation = Column(String, nullable=False)  # 'backup' or 'restore'
    status = Column(String, nullable=False)  # 'success' or 'error'
    filename = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)  # Bytes
    error_message = Column(String, nullable=True)
    created_by = Column(String, nullable=True)  # User email
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
