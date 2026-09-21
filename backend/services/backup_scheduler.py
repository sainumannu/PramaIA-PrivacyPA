"""
Backup Scheduler Service

This module handles scheduled backups using APScheduler.
It runs daily at the configured hour (UTC) if enabled.
"""

import logging
from datetime import datetime, time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.backup_service import BackupService
from backend.models.backup_models import BackupConfig

logger = logging.getLogger(__name__)


class BackupScheduler:
    """Manages scheduled backups"""
    
    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.job_id = "scheduled_backup"
    
    async def start(self):
        """Initialize and start the scheduler"""
        logger.info("Starting backup scheduler...")
        
        # Create scheduler
        self.scheduler = AsyncIOScheduler()
        
        # Load configuration and schedule job
        await self._update_schedule()
        
        # Start the scheduler
        self.scheduler.start()
        logger.info("Backup scheduler started")
    
    async def stop(self):
        """Stop the scheduler"""
        if self.scheduler:
            logger.info("Stopping backup scheduler...")
            self.scheduler.shutdown(wait=False)
            logger.info("Backup scheduler stopped")
    
    async def _update_schedule(self):
        """Update the scheduled job based on current configuration"""
        try:
            # Get current configuration from database
            async for db in get_db():
                try:
                    result = await db.execute(
                        "SELECT enabled, schedule_hour FROM backup_configs ORDER BY id DESC LIMIT 1"
                    )
                    row = result.fetchone()
                    
                    if row is None:
                        logger.warning("No backup configuration found, scheduler disabled")
                        return
                    
                    enabled, schedule_hour = row
                    
                    # Remove existing job if present
                    if self.scheduler and self.scheduler.get_job(self.job_id):
                        self.scheduler.remove_job(self.job_id)
                        logger.info(f"Removed existing scheduled backup job")
                    
                    # Add new job if enabled
                    if enabled and self.scheduler:
                        trigger = CronTrigger(hour=schedule_hour, minute=0)
                        self.scheduler.add_job(
                            self._run_scheduled_backup,
                            trigger=trigger,
                            id=self.job_id,
                            name="Scheduled Database Backup",
                            replace_existing=True,
                        )
                        logger.info(f"Scheduled backup at {schedule_hour:02d}:00 UTC (enabled)")
                    else:
                        logger.info("Scheduled backup disabled in configuration")
                
                finally:
                    await db.close()
                    break  # Only iterate once
                    
        except Exception as e:
            logger.error(f"Failed to update backup schedule: {e}")
    
    async def _run_scheduled_backup(self):
        """Execute a scheduled backup"""
        logger.info("Running scheduled backup...")
        
        try:
            async for db in get_db():
                try:
                    service = BackupService(db)
                    result = await service.create_backup(
                        user_email="system@scheduler",
                        scheduled=True
                    )
                    
                    # Update last_run timestamp
                    await db.execute(
                        "UPDATE backup_configs SET last_run = ? WHERE id = (SELECT MAX(id) FROM backup_configs)",
                        (datetime.utcnow(),)
                    )
                    await db.commit()
                    
                    logger.info(f"Scheduled backup completed: {result.get('filename')}")
                
                except Exception as e:
                    logger.error(f"Scheduled backup failed: {e}")
                    await db.rollback()
                
                finally:
                    await db.close()
                    break  # Only iterate once
                    
        except Exception as e:
            logger.error(f"Failed to run scheduled backup: {e}")
    
    async def reload_configuration(self):
        """Reload configuration and update schedule (called after config changes)"""
        logger.info("Reloading backup scheduler configuration...")
        await self._update_schedule()


# Global scheduler instance
_scheduler: Optional[BackupScheduler] = None


async def get_scheduler() -> BackupScheduler:
    """Get or create the global scheduler instance"""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackupScheduler()
    return _scheduler


async def start_backup_scheduler():
    """Start the backup scheduler (call on app startup)"""
    scheduler = await get_scheduler()
    await scheduler.start()


async def stop_backup_scheduler():
    """Stop the backup scheduler (call on app shutdown)"""
    global _scheduler
    if _scheduler:
        await _scheduler.stop()
        _scheduler = None


async def reload_backup_schedule():
    """Reload backup schedule after configuration changes"""
    scheduler = await get_scheduler()
    await scheduler.reload_configuration()
