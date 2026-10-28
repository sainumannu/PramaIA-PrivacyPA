"""
Valutazione pianificata delle regole di conformità: ogni giorno all'ora configurata (UTC), per ogni ente.

Senza questa le segnalazioni si aggiornano solo quando qualcuno apre la pagina delle verifiche (o con
`POST /findings/evaluate`), e i webhook che ne dipendono (es. `authorization.expired_not_revoked` per l'HelpDesk)
partono in ritardo o mai.

Configurazione: `FINDINGS_EVALUATION_HOUR` (0-23, predefinita 2). Con più istanze dell'applicazione ognuna avrebbe
il suo pianificatore: usare `FINDINGS_SCHEDULER=false` su tutte tranne una (la valutazione è comunque idempotente).
"""
import logging
import os
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, union

from backend.db.database import AsyncSessionLocal
from backend.models.privacypa_identity import UserRoleAssignment
from backend.models.privacypa_organization import Organization
from backend.services.privacypa_rules_engine import RulesEngine

logger = logging.getLogger(__name__)

JOB_ID = "scheduled_findings_evaluation"


async def known_tenants(db) -> list[str]:
    """Gli enti che usano PrivacyPA: hanno l'anagrafica dell'ente o almeno un ruolo assegnato."""
    query = union(select(Organization.tenant_id), select(UserRoleAssignment.tenant_id))
    return sorted(t for t in (await db.execute(query)).scalars().all() if t)


async def run_scheduled_evaluation(session_factory=AsyncSessionLocal) -> dict[str, dict | None]:
    """Valuta le regole di ogni ente in una sessione propria: l'errore di un ente non ferma gli altri. Nessuna eccezione esce."""
    results: dict[str, dict | None] = {}
    try:
        async with session_factory() as db:
            tenants = await known_tenants(db)
    except Exception as e:
        logger.error(f"Valutazione pianificata delle regole: elenco degli enti non leggibile: {e}")
        return results
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                stats = await RulesEngine(db).evaluate(tenant_id)
                await db.commit()
                results[tenant_id] = stats.to_dict()
        except Exception as e:
            logger.error(f"Valutazione pianificata delle regole FALLITA per l'ente {tenant_id}: {e}")
            results[tenant_id] = None
    logger.info(f"Valutazione pianificata delle regole completata per {len(tenants)} enti")
    return results


def _hour() -> int:
    try:
        hour = int(os.getenv("FINDINGS_EVALUATION_HOUR", "2"))
    except ValueError:
        hour = -1
    if not 0 <= hour <= 23:
        logger.warning("FINDINGS_EVALUATION_HOUR non valida: uso le 02:00 UTC")
        return 2
    return hour


_scheduler: Optional[AsyncIOScheduler] = None


async def start_findings_scheduler() -> None:
    global _scheduler
    if os.getenv("FINDINGS_SCHEDULER", "true").strip().lower() in ("0", "false", "no"):
        logger.info("Valutazione pianificata delle regole disattivata (FINDINGS_SCHEDULER=false)")
        return
    hour = _hour()
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(run_scheduled_evaluation, trigger=CronTrigger(hour=hour, minute=0), id=JOB_ID,
                       name="Valutazione delle regole di conformità", replace_existing=True)
    _scheduler.start()
    logger.info(f"Valutazione pianificata delle regole alle {hour:02d}:00 UTC")


async def stop_findings_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
