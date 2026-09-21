"""
PrivacyPA — Scadenze relative configurabili per ente (§156).

Esempi della specifica: prossima revisione = approvazione + 12 mesi; promemoria di un atto = scadenza − 90 giorni (parametro
delle regole di conformità). I mesi sono mesi di calendario, mai «30 giorni» né «anno = 365 giorni»: il 31 gennaio + 1 mese è
il 28 (o 29) febbraio. Un valore 0 disattiva il calcolo automatico.
"""
import calendar
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.privacypa_permissions import get_policy

DEFAULT_DEADLINES = {"dsr_base_months": 1, "dsr_extension_months": 2, "processing_review_months": 12, "dpia_review_months": 12}
DEADLINE_LIMITS = {"dsr_base_months": (1, 3), "dsr_extension_months": (0, 6), "processing_review_months": (0, 60), "dpia_review_months": (0, 60)}


def add_months(moment: datetime, months: int) -> datetime:
    """Aggiunge mesi di calendario mantenendo l'ora; il giorno si riporta all'ultimo del mese se non esiste."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def add_months_to_date(day: date, months: int) -> date:
    return add_months(datetime.combine(day, datetime.min.time()), months).date()


async def get_deadlines(db: AsyncSession, tenant_id: str) -> dict:
    policy = await get_policy(db, tenant_id)
    stored = (getattr(policy, "deadlines", None) if policy else None) or {}
    return {k: int(stored.get(k, default)) for k, default in DEFAULT_DEADLINES.items()}


def renewed_review_date(current: date | None, approved_on: date, months: int) -> date | None:
    """
    Prossima revisione dopo un'approvazione: approvazione + `months` mesi, ma solo se non c'è già una data futura (una data
    inserita a mano, o un ciclo ancora in corso, non si sposta). Con `months` = 0 il calcolo è disattivato: si lascia la data com'è.
    """
    if months <= 0 or (current is not None and current > approved_on):
        return current
    return add_months_to_date(approved_on, months)
