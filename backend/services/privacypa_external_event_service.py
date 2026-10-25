"""
PrivacyPA — Ricezione degli eventi tecnici da sistemi esterni (connettore VideoPA, W3).

PrivacyPA **non decide nulla**: salva l'evento (solo i campi del contratto), lo collega al trattamento se esiste nell'ente e lascia che la
regola `EXTERNAL_CHANGE_REVIEW_PENDING` apra una segnalazione per il DPO e un task. Più eventi dello stesso tipo sullo stesso trattamento
finiscono nella stessa segnalazione (contatore). La segnalazione si chiude quando il DPO archivia gli eventi con una nota, oppure quando il
trattamento viene rivisto con una data di revisione **successiva** al giorno dell'evento (lo stesso giorno non basta: si archivia).

Esiti per evento: `ACCEPTED`, `DUPLICATE` (stesso ente, sorgente ed `event_id`), `UNMATCHED` (trattamento assente nell'ente o fuori dall'ambito
dell'account: non si dice altro) e `REJECTED` con motivo (errore permanente, non si ritenta).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.privacypa_scope import VisibilityScope
from backend.models.privacypa_audit import ActorType
from backend.models.privacypa_external_events import ExternalEvent, ExternalEventStatus
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_rules import Severity
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_deadlines import add_months

MAX_BATCH = 100
SOURCES = ("VIDEOPA",)
# Tipi accettati e gravità della segnalazione (proposta da validare con il DPO; un tipo sconosciuto è REJECTED).
EVENT_SEVERITY: dict[str, Severity] = {
    "AudioEnabled": Severity.HIGH,
    "AnalyticsEnabled": Severity.HIGH,
    "AnprEnabled": Severity.HIGH,
    "RetentionChanged": Severity.MEDIUM,
    "VmsChanged": Severity.MEDIUM,
    "CameraFieldOfViewChanged": Severity.MEDIUM,
    "CameraMoved": Severity.MEDIUM,
    "PrivacyMaskChanged": Severity.MEDIUM,
    "CameraInstalled": Severity.MEDIUM,
    "CameraDecommissioned": Severity.MEDIUM,
    "ExternalAccessGranted": Severity.MEDIUM,
    "ExternalAccessRevoked": Severity.MEDIUM,
    "PrivacyReviewRequested": Severity.MEDIUM,
}
SUMMARY_MAX, SUBJECT_MAX, LINK_MAX, ID_MAX, NOTE_MAX = 200, 100, 500, 100, 1000
DEFAULT_RETENTION_MONTHS = 24
FUTURE_TOLERANCE = timedelta(days=1)


class ExternalEventError(ValueError):
    pass


def _text(value, name: str, limit: int, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ExternalEventError(f"{name} è obbligatorio")
        return None
    if not isinstance(value, str):
        raise ExternalEventError(f"{name} deve essere un testo")
    value = value.strip()
    if required and not value:
        raise ExternalEventError(f"{name} è obbligatorio")
    if len(value) > limit:
        raise ExternalEventError(f"{name} è più lungo di {limit} caratteri")
    return value or None


def validate_event(item, now: datetime) -> dict:
    """Campi ammessi del contratto; tutto il resto si ignora e non si conserva. `ExternalEventError` con il motivo se non valido."""
    if not isinstance(item, dict):
        raise ExternalEventError("L'evento deve essere un oggetto")
    source = _text(item.get("source"), "source", 30, required=True).upper()
    if source not in SOURCES:
        raise ExternalEventError(f"Sorgente sconosciuta: {source}")
    event_type = _text(item.get("type"), "type", 60, required=True)
    if event_type not in EVENT_SEVERITY:
        raise ExternalEventError(f"Tipo di evento sconosciuto: {event_type}")
    raw = item.get("occurred_at")
    try:
        occurred = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise ExternalEventError("occurred_at non è una data ISO 8601") from None
    if occurred.tzinfo:
        occurred = occurred.astimezone(timezone.utc).replace(tzinfo=None)
    if occurred > now + FUTURE_TOLERANCE:
        raise ExternalEventError("occurred_at è nel futuro")
    link = _text(item.get("link"), "link", LINK_MAX)
    if link and not link.lower().startswith(("https://", "http://")):
        raise ExternalEventError("link deve essere un indirizzo http(s)")
    return {
        "source": source, "event_id": _text(item.get("event_id"), "event_id", ID_MAX, required=True), "event_type": event_type,
        "occurred_at": occurred, "claimed_processing_id": _text(item.get("processing_id"), "processing_id", 64),
        "subject_ref": _text(item.get("subject_ref"), "subject_ref", SUBJECT_MAX),
        "summary": _text(item.get("summary"), "summary", SUMMARY_MAX), "link": link,
    }


class ExternalEventService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def ingest(self, tenant_id: str, actor: str, scope: VisibilityScope, items: list, *, retention_months: int = DEFAULT_RETENTION_MONTHS) -> list[dict]:
        now = datetime.utcnow()
        await self.purge_expired(tenant_id, retention_months, now)
        parsed: list[tuple[str | None, dict | None, str | None]] = []       # (event_id, dati, motivo del rifiuto)
        for item in items:
            try:
                clean = validate_event(item, now)
                parsed.append((clean["event_id"], clean, None))
            except ExternalEventError as e:
                parsed.append((item.get("event_id") if isinstance(item, dict) and isinstance(item.get("event_id"), str) else None, None, str(e)))

        claimed = {c["claimed_processing_id"] for _, c, _ in parsed if c and c["claimed_processing_id"]}
        visible: dict[str, ProcessingActivity] = {}
        if claimed:
            for a in (await self.db.execute(select(ProcessingActivity).where(
                ProcessingActivity.tenant_id == tenant_id, ProcessingActivity.id.in_(claimed),
                ProcessingActivity.deleted_at.is_(None), ProcessingActivity.is_current.is_(True),
            ))).scalars():
                if scope.unit_visible(a.organizational_unit_id):
                    visible[a.id] = a
        known = set()
        ids = {c["event_id"] for _, c, _ in parsed if c}
        if ids:
            known = set((await self.db.execute(select(ExternalEvent.event_id).where(
                ExternalEvent.tenant_id == tenant_id, ExternalEvent.source.in_(SOURCES), ExternalEvent.event_id.in_(ids)
            ))).scalars())

        results = []
        for event_id, clean, reason in parsed:
            if clean is None:
                results.append({"event_id": event_id, "outcome": "REJECTED", "reason": reason})
                continue
            if clean["event_id"] in known:
                results.append({"event_id": event_id, "outcome": "DUPLICATE"})
                continue
            activity = visible.get(clean["claimed_processing_id"] or "")
            event = ExternalEvent(
                tenant_id=tenant_id, received_by=actor, received_at=now, **clean,
                processing_activity_id=activity.id if activity else None,
                status=ExternalEventStatus.PENDING if activity else ExternalEventStatus.UNMATCHED,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(event)
                    await self.db.flush()
            except IntegrityError:
                results.append({"event_id": event_id, "outcome": "DUPLICATE"})
                continue
            known.add(clean["event_id"])
            await self.audit.log(
                tenant_id, "external_event", "received", entity_id=event.id, actor_user_id=actor, actor_type=ActorType.SYSTEM,
                new_values={"source": event.source, "type": event.event_type, "status": event.status.value,
                            "processing_activity_id": event.processing_activity_id},
            )
            results.append({"event_id": event_id, "outcome": "ACCEPTED" if activity else "UNMATCHED", "id": event.id})
        return results

    async def purge_expired(self, tenant_id: str, months: int, now: datetime | None = None) -> int:
        """Elimina gli eventi ricevuti da più di `months` mesi (24 di default, parametro dell'ente)."""
        limit = add_months(now or datetime.utcnow(), -months)
        result = await self.db.execute(delete(ExternalEvent).where(ExternalEvent.tenant_id == tenant_id, ExternalEvent.received_at < limit))
        return result.rowcount or 0

    async def get(self, tenant_id: str, event_id: str) -> ExternalEvent | None:
        return (await self.db.execute(select(ExternalEvent).where(
            ExternalEvent.tenant_id == tenant_id, ExternalEvent.id == event_id))).scalar_one_or_none()

    async def list(self, tenant_id: str, scope: VisibilityScope, *, status: ExternalEventStatus | None = None,
                   processing_id: str | None = None, event_type: str | None = None, limit: int = 200, offset: int = 0) -> tuple[list[ExternalEvent], int]:
        stmt = select(ExternalEvent).where(ExternalEvent.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(ExternalEvent.status == status)
        if processing_id:
            stmt = stmt.where(ExternalEvent.processing_activity_id == processing_id)
        if event_type:
            stmt = stmt.where(ExternalEvent.event_type == event_type)
        rows = (await self.db.execute(stmt.order_by(ExternalEvent.occurred_at.desc(), ExternalEvent.id))).scalars().all()
        # Senza trattamento (UNMATCHED) solo chi ha visibilità sull'intero ente.
        visible = [e for e in rows if (scope.processing_visible(e.processing_activity_id) if e.processing_activity_id else scope.unrestricted)]
        return visible[offset:offset + limit], len(visible)

    async def archive(self, tenant_id: str, event: ExternalEvent, actor: str, note: str) -> ExternalEvent:
        note = _text(note, "La nota", NOTE_MAX, required=True)
        if event.status == ExternalEventStatus.ARCHIVED:
            raise ExternalEventError("L'evento è già archiviato")
        old = event.status
        event.status, event.archived_by, event.archived_at, event.archive_note = ExternalEventStatus.ARCHIVED, actor, datetime.utcnow(), note
        await self.db.flush()
        await self.audit.log(
            tenant_id, "external_event", "archived", entity_id=event.id, actor_user_id=actor,
            old_values={"status": old.value}, new_values={"status": "ARCHIVED", "type": event.event_type},
        )
        return event
