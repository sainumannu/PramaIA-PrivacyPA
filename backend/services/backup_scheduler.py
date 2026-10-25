"""
Backup pianificato (§57, §74): ogni giorno all'ora configurata (UTC), se abilitato.

Il ciclo è: backup → **verifica di ripristino** del backup appena creato. Un errore in uno dei due passaggi resta nel registro
(`GET /api/backup/logs`) e nel log dell'applicazione a livello ERROR, da collegare al monitoraggio (§57: «monitoraggio fallimenti»).

Con più istanze dell'applicazione ognuna avrebbe il suo pianificatore e farebbe il proprio backup: usare `BACKUP_SCHEDULER=false`
su tutte tranne una.
"""
import logging
import os
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.db.database import AsyncSessionLocal
from backend.services.backup_service import BackupService

logger = logging.getLogger(__name__)


async def run_scheduled_backup(session_factory=AsyncSessionLocal, **service_options) -> dict | None:
    """Backup e verifica. Restituisce l'esito della verifica; nessuna eccezione esce (il pianificatore deve restare vivo)."""
    logger.info("Backup pianificato in corso...")
    try:
        async with session_factory() as db:
            service = BackupService(db, **service_options)
            info = await service.create_backup(user_email="system@scheduler", scheduled=True)
            verdict = await service.verify_backup(info["filename"], user_email="system@scheduler")
            if verdict["ok"]:
                logger.info(f"Backup pianificato completato e verificato: {info['filename']}")
            else:
                logger.error(f"Backup pianificato creato ma NON verificato: {info['filename']}: {verdict['checks']}")
            if info.get("offsite") and not info["offsite"]["ok"]:
                logger.error(f"Copia esterna del backup {info['filename']} fallita: {info['offsite']['error']}")
            return verdict
    except Exception as e:
        logger.error(f"Backup pianificato FALLITO: {e}")
        return None


class BackupScheduler:
    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.job_id = "scheduled_backup"

    async def start(self):
        if os.getenv("BACKUP_SCHEDULER", "true").strip().lower() in ("0", "false", "no"):
            logger.info("Pianificatore dei backup disattivato (BACKUP_SCHEDULER=false)")
            return
        self.scheduler = AsyncIOScheduler()
        await self._update_schedule()
        self.scheduler.start()
        logger.info("Pianificatore dei backup avviato")

    async def stop(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
            logger.info("Pianificatore dei backup fermato")

    async def _update_schedule(self):
        """Allinea il lavoro pianificato alla configurazione salvata."""
        if self.scheduler is None:
            return
        try:
            async with AsyncSessionLocal() as db:
                config = await BackupService(db).get_config()
                enabled, hour = config.enabled, config.schedule_hour
            if self.scheduler.get_job(self.job_id):
                self.scheduler.remove_job(self.job_id)
            if enabled:
                self.scheduler.add_job(run_scheduled_backup, trigger=CronTrigger(hour=hour, minute=0), id=self.job_id,
                                       name="Backup pianificato del database", replace_existing=True)
                logger.info(f"Backup pianificato alle {hour:02d}:00 UTC")
            else:
                logger.info("Backup pianificato disabilitato nella configurazione")
        except Exception as e:
            logger.error(f"Aggiornamento del pianificatore dei backup fallito: {e}")

    async def reload_configuration(self):
        await self._update_schedule()


_scheduler: Optional[BackupScheduler] = None


async def get_scheduler() -> BackupScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackupScheduler()
    return _scheduler


async def start_backup_scheduler():
    await (await get_scheduler()).start()


async def stop_backup_scheduler():
    global _scheduler
    if _scheduler:
        await _scheduler.stop()
        _scheduler = None


async def reload_backup_schedule():
    await (await get_scheduler()).reload_configuration()
