"""
PrivacyPA — Catalogo degli eventi dei webhook (§70) e creazione delle consegne.

Modulo leggero (solo modelli): lo chiama `AuditService.log` nella stessa transazione della modifica, per questo non importa altri servizi.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from backend.models.privacypa_webhooks import DeliveryStatus, WebhookDelivery, WebhookSubscription

PING = "webhook.ping"


@dataclass(frozen=True)
class WebhookEvent:
    name: str
    description: str
    prefixes: tuple[str, ...]                 # prefissi del tipo evento sull'outbox
    entity_type: str | None = None            # tipo dell'elemento (eventi da audit)
    rules: dict[str, str] | None = None       # eventi da verifiche di conformità: codice regola → tipo dell'elemento


EVENTS: dict[str, WebhookEvent] = {e.name: e for e in (
    WebhookEvent("processing.updated", "Un trattamento è cambiato (creazione, dati, stato, relazioni)",
                 ("processing_activity.status.", "processing_activity.relations.", "processing_activity.update", "processing_activity.create"),
                 entity_type="processing_activity"),
    WebhookEvent("processor.expiring", "Un atto art. 28 o un contratto scade a breve", ("finding.opened",),
                 rules={"AGREEMENT_EXPIRING": "processor_agreement", "CONTRACT_EXPIRING": "contract"}),
    WebhookEvent("task.overdue", "Un task ha superato la scadenza", ("finding.opened",), rules={"TASK_OVERDUE": "task"}),
    WebhookEvent("breach.created", "È stata registrata una violazione dei dati", ("data_breach.create",), entity_type="data_breach"),
    WebhookEvent("dpia.review_due", "Una DPIA va rivista", ("finding.opened",), rules={"DPIA_REVIEW_DUE": "dpia"}),
    WebhookEvent("document.expiring", "La versione corrente di un documento scade a breve", ("finding.opened",), rules={"DOCUMENT_EXPIRING": "document"}),
    WebhookEvent("finding.opened", "Si è aperta una verifica di conformità (qualsiasi regola)", ("finding.opened",), entity_type="compliance_finding"),
)}


def matching_events(event_type: str, data: dict) -> list[tuple[str, str, str | None]]:
    """(nome logico, tipo dell'elemento, id dell'elemento) degli eventi del catalogo a cui corrisponde un evento dell'outbox."""
    out = []
    for ev in EVENTS.values():
        if not event_type.startswith(ev.prefixes):
            continue
        if ev.rules is not None:
            entity_type = ev.rules.get(data.get("rule_code"))
            if entity_type is None:
                continue
            out.append((ev.name, entity_type, data.get("entity_id")))
        else:
            out.append((ev.name, ev.entity_type or "", None))
    return out


def envelope(delivery_id: str, name: str, tenant_id: str, occurred_at: str, entity_type: str, entity_id: str | None, data: dict) -> dict:
    return {
        "id": delivery_id, "event": name, "tenant_id": tenant_id, "occurred_at": occurred_at,
        "data": {"entity_type": entity_type, "entity_id": entity_id, **{k: v for k, v in data.items() if k != "entity_id"}},
    }


async def fan_out_event(db, event) -> int:
    """Crea le consegne per un evento appena accodato sull'outbox (stessa transazione). Restituisce quante ne ha create."""
    payload = event.payload or {}
    data = payload.get("data") or {}
    matches = matching_events(event.event_type, data)
    if not matches:
        return 0
    subs = (await db.execute(select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == event.tenant_id, WebhookSubscription.active.is_(True)))).scalars().all()
    created = 0
    now = datetime.utcnow()
    for sub in subs:
        for name, entity_type, entity_id in matches:
            if name not in (sub.events or []):
                continue
            delivery_id = str(uuid.uuid4())
            db.add(WebhookDelivery(
                id=delivery_id, tenant_id=event.tenant_id, subscription_id=sub.id, event_id=event.id, event_name=name,
                payload=envelope(delivery_id, name, event.tenant_id, payload.get("occurred_at") or now.isoformat(), entity_type,
                                  entity_id or payload.get("entity_id"), data),
                status=DeliveryStatus.PENDING, next_attempt_at=now))
            created += 1
    if created:
        await db.flush()
    return created
