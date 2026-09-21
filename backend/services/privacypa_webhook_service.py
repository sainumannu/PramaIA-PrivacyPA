"""
PrivacyPA — Webhook (§70): catalogo degli eventi, sottoscrizioni, consegna firmata con ritentativi.

Flusso:
1. un evento di dominio finisce sull'outbox (§130) nella stessa transazione della modifica; `AuditService.log` chiama
   `fan_out_event`, che crea una consegna PENDING per ogni sottoscrizione attiva dell'ente interessata all'evento;
2. `dispatch_due` (worker in background, oppure `POST /webhooks/dispatch`) invia le consegne dovute e registra l'esito.

Sicurezza:
- ogni richiesta è firmata: `X-PrivacyPA-Signature: v1=HMAC-SHA256(segreto, "<timestamp>.<corpo>")` con
  `X-PrivacyPA-Timestamp`; chi riceve rifiuta timestamp troppo vecchi (`verify`);
- il payload è minimale: riferimento all'elemento, stato e date utili; nessun testo descrittivo né dato personale;
- l'indirizzo di destinazione è controllato prima di ogni invio (SSRF): solo HTTPS, senza credenziali nell'URL, e solo
  indirizzi pubblici salvo `WEBHOOK_ALLOWED_HOSTS` / `WEBHOOK_ALLOW_PRIVATE_NETWORKS`; le redirezioni non si seguono;
- consegna «almeno una volta»: chi riceve tratta `X-PrivacyPA-Delivery` come chiave di idempotenza.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func, or_, select, update

from backend.models.privacypa_webhooks import DeliveryStatus, WebhookDelivery, WebhookSubscription
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound, _Base
from backend.services.privacypa_secrets import SecretsUnavailable, decrypt, encrypt
from backend.services.privacypa_webhook_events import EVENTS, PING, envelope, fan_out_event  # noqa: F401

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 20
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = (60, 300, 1800, 7200, 43200)     # attesa dopo il 1°…5° tentativo fallito
LEASE_SECONDS = 120                                # una consegna presa in carico non si riprende prima
MAX_STREAK = 10                                    # consegne morte consecutive dopo le quali l'endpoint si disattiva
TIMEOUT_SECONDS = 10.0
SIGNATURE_TOLERANCE = 300


# ---------------------------------------------------------------- firma

def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "v1=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, body: bytes, signature: str, *, tolerance: int = SIGNATURE_TOLERANCE, now: float | None = None) -> bool:
    """Per chi riceve: firma corretta (confronto a tempo costante) e timestamp entro `tolerance` secondi."""
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except ValueError:
        return False
    return age <= tolerance and hmac.compare_digest(sign(secret, timestamp, body), signature or "")


# ---------------------------------------------------------------- destinazione (SSRF)

class UrlNotAllowed(ValueError):
    pass


def _env_list(name: str) -> set[str]:
    return {h.strip().lower() for h in os.getenv(name, "").split(",") if h.strip()}


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes")


def _blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global


def _trusted(host: str) -> bool:
    return host.lower() in _env_list("WEBHOOK_ALLOWED_HOSTS") or _flag("WEBHOOK_ALLOW_PRIVATE_NETWORKS")


def check_url(url: str) -> tuple[str, int]:
    """Valida forma e schema dell'URL; restituisce (host, porta). `UrlNotAllowed` se non è accettabile."""
    if not isinstance(url, str) or len(url) > 500:
        raise UrlNotAllowed("URL non valido")
    parts = urlsplit(url.strip())
    allow_http = _flag("WEBHOOK_ALLOW_HTTP")
    if parts.scheme not in (("https", "http") if allow_http else ("https",)):
        raise UrlNotAllowed("L'URL deve usare HTTPS")
    if parts.username or parts.password:
        raise UrlNotAllowed("L'URL non può contenere credenziali")
    if not parts.hostname:
        raise UrlNotAllowed("URL senza host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UrlNotAllowed("Porta non valida") from None
    host = parts.hostname.lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _blocked(str(literal)) and not _trusted(host):
        raise UrlNotAllowed("Indirizzo non consentito: solo indirizzi pubblici")
    return host, port


Resolver = Callable[[str, int], Awaitable[list[str]]]
_TRANSPORT: httpx.AsyncBaseTransport | None = None      # sostituibile nei test


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=1)
    return sorted({info[4][0] for info in infos})


async def assert_destination(host: str, port: int, resolver: Resolver | None = None) -> None:
    """Prima di ogni invio: l'host deve risolvere solo a indirizzi pubblici (salvo host o reti fidati da configurazione)."""
    if _trusted(host):
        return
    try:
        addresses = await (resolver or _resolve)(host, port)
    except OSError:
        raise UrlNotAllowed("Host non risolvibile") from None
    if not addresses or any(_blocked(a) for a in addresses):
        raise UrlNotAllowed("L'host risolve a un indirizzo non consentito")


# ---------------------------------------------------------------- invio

async def send(
    url: str, secret: str, delivery: WebhookDelivery, *, transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> tuple[int | None, str | None]:
    """Una richiesta firmata. Restituisce (codice HTTP, errore); niente testo di eccezioni o risposte (possono contenere l'URL)."""
    try:
        host, port = check_url(url)
        await assert_destination(host, port, resolver)
    except UrlNotAllowed as e:
        return None, str(e)
    body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json", "User-Agent": "PrivacyPA-Webhook/1.0", "X-PrivacyPA-Event": delivery.event_name,
        "X-PrivacyPA-Delivery": delivery.id, "X-PrivacyPA-Timestamp": timestamp, "X-PrivacyPA-Signature": sign(secret, timestamp, body),
    }
    try:
        async with httpx.AsyncClient(transport=transport or _TRANSPORT, timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                return response.status_code, None
    except httpx.HTTPError as e:
        return None, e.__class__.__name__


# ---------------------------------------------------------------- consegna con ritentativi

def _outcome(delivery: WebhookDelivery, sub: WebhookSubscription, status_code: int | None, error: str | None, now: datetime, retry: bool) -> str:
    """Aggiorna consegna e sottoscrizione dopo un tentativo; restituisce `delivered`, `retry` o `dead`."""
    delivery.attempts += 1
    delivery.last_attempt_at, delivery.claimed_until = now, None
    delivery.last_status_code, delivery.last_error = status_code, (error or (f"HTTP {status_code}" if status_code and not 200 <= status_code < 300 else None))
    if status_code is not None and 200 <= status_code < 300:
        delivery.status, delivery.delivered_at = DeliveryStatus.DELIVERED, now
        if delivery.event_name != PING:
            sub.failure_streak, sub.last_success_at = 0, now
        return "delivered"
    if status_code == 410 and delivery.event_name != PING:              # l'endpoint dichiara di non esistere più
        delivery.status = DeliveryStatus.DEAD
        sub.active, sub.disabled_reason, sub.last_failure_at = False, "gone", now
        return "dead"
    if not retry or delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = DeliveryStatus.DEAD
        if delivery.event_name != PING:
            sub.failure_streak, sub.last_failure_at = sub.failure_streak + 1, now
            if sub.failure_streak >= MAX_STREAK:
                sub.active, sub.disabled_reason = False, "too_many_failures"
        return "dead"
    delivery.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[min(delivery.attempts, len(BACKOFF_SECONDS)) - 1])
    return "retry"


async def attempt(
    db, delivery: WebhookDelivery, sub: WebhookSubscription, *, retry: bool = True,
    transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None,
) -> str:
    try:
        secret = decrypt(sub.secret_enc)
        code, error = await send(sub.url, secret, delivery, transport=transport, resolver=resolver)
    except SecretsUnavailable as e:
        code, error = None, str(e)
    result = _outcome(delivery, sub, code, error, datetime.utcnow(), retry)
    await db.flush()
    return result


async def dispatch_due(
    db, *, tenant_id: str | None = None, limit: int = 50, commit_each: bool = False,
    transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None,
) -> dict:
    """Invia le consegne dovute. Ogni consegna si «prende in carico» con un aggiornamento condizionato: due worker non la inviano insieme."""
    now = datetime.utcnow()
    stmt = (select(WebhookDelivery).join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
            .where(WebhookDelivery.status == DeliveryStatus.PENDING, WebhookDelivery.next_attempt_at <= now,
                   or_(WebhookDelivery.claimed_until.is_(None), WebhookDelivery.claimed_until < now), WebhookSubscription.active.is_(True)))
    if tenant_id:
        stmt = stmt.where(WebhookDelivery.tenant_id == tenant_id)
    due = list((await db.execute(stmt.order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id).limit(limit))).scalars().all())
    stats = {"due": len(due), "delivered": 0, "retry": 0, "dead": 0, "skipped": 0}
    for delivery in due:
        claim = await db.execute(update(WebhookDelivery).where(
            WebhookDelivery.id == delivery.id, WebhookDelivery.status == DeliveryStatus.PENDING,
            or_(WebhookDelivery.claimed_until.is_(None), WebhookDelivery.claimed_until < now),
        ).values(claimed_until=now + timedelta(seconds=LEASE_SECONDS)))
        if claim.rowcount != 1:
            stats["skipped"] += 1
            continue
        if commit_each:
            await db.commit()
        await db.refresh(delivery)
        sub = await db.get(WebhookSubscription, delivery.subscription_id)
        stats[await attempt(db, delivery, sub, transport=transport, resolver=resolver)] += 1
        if commit_each:
            await db.commit()
    return stats


async def run_worker(session_factory, stop: asyncio.Event, interval: float = 5.0) -> None:
    """Ciclo in background: consegna ciò che è dovuto ogni `interval` secondi finché `stop` non viene impostato."""
    while not stop.is_set():
        try:
            async with session_factory() as db:
                await dispatch_due(db, commit_each=True)
                await db.commit()
        except Exception:                                                   # un errore non ferma il ciclo
            logger.exception("Webhook worker: ciclo di consegna fallito")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------- sottoscrizioni

def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


class WebhookService(_Base):
    def _q(self, tenant_id: str):
        return select(WebhookSubscription).where(WebhookSubscription.tenant_id == tenant_id)

    async def search(self, tenant_id: str) -> list[WebhookSubscription]:
        return list((await self.db.execute(self._q(tenant_id).order_by(WebhookSubscription.name, WebhookSubscription.id))).scalars().all())

    async def get(self, tenant_id: str, sub_id: str) -> WebhookSubscription | None:
        return (await self.db.execute(self._q(tenant_id).where(WebhookSubscription.id == sub_id))).scalar_one_or_none()

    async def _require(self, tenant_id: str, sub_id: str) -> WebhookSubscription:
        sub = await self.get(tenant_id, sub_id)
        if sub is None:
            raise ComplianceNotFound("Endpoint non trovato")
        return sub

    @staticmethod
    def _clean(name: str, url: str, events: list[str]) -> tuple[str, str, list[str]]:
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise ComplianceError("Il nome è obbligatorio (massimo 120 caratteri)")
        try:
            check_url(url)
        except UrlNotAllowed as e:
            raise ComplianceError(str(e)) from None
        unknown = [e for e in events if e not in EVENTS]
        if unknown:
            raise ComplianceError(f"Eventi sconosciuti: {', '.join(unknown)}")
        events = list(dict.fromkeys(events))
        if not events:
            raise ComplianceError("Indicare almeno un evento")
        return name, url.strip(), events

    @staticmethod
    def _audit_values(sub: WebhookSubscription) -> dict:
        # Né segreto né URL completo (può contenere token): solo nome, host, eventi e stato.
        return {"name": sub.name, "host": urlsplit(sub.url).hostname, "events": list(sub.events), "active": sub.active}

    async def create(self, tenant_id: str, actor: str, name: str, url: str, events: list[str], active: bool = True) -> tuple[WebhookSubscription, str]:
        name, url, events = self._clean(name, url, events)
        if len(await self.search(tenant_id)) >= MAX_SUBSCRIPTIONS:
            raise ComplianceError(f"Al massimo {MAX_SUBSCRIPTIONS} endpoint per ente")
        try:
            secret = new_secret()
            sub = WebhookSubscription(tenant_id=tenant_id, name=name, url=url, secret_enc=encrypt(secret), events=events, active=active,
                                      created_by=actor, secret_rotated_at=datetime.utcnow())
        except SecretsUnavailable as e:
            raise ComplianceError(str(e)) from None
        self.db.add(sub)
        await self.db.flush()
        await self.audit.log(tenant_id, "webhook", "create", event_type="webhook.created", entity_id=sub.id, actor_user_id=actor,
                             new_values=self._audit_values(sub))
        return sub, secret

    async def update(self, tenant_id: str, sub_id: str, actor: str, name: str, url: str, events: list[str], active: bool) -> WebhookSubscription:
        sub = await self._require(tenant_id, sub_id)
        name, url, events = self._clean(name, url, events)
        before = self._audit_values(sub)
        sub.name, sub.url, sub.events, sub.updated_at = name, url, events, datetime.utcnow()
        if active and not sub.active:                               # riattivazione: si riparte da zero
            sub.failure_streak = 0
        sub.active = active
        sub.disabled_reason = None if active else (sub.disabled_reason or "manual")
        await self.db.flush()
        await self.audit.log(tenant_id, "webhook", "update", event_type="webhook.updated", entity_id=sub.id, actor_user_id=actor,
                             old_values=before, new_values=self._audit_values(sub))
        return sub

    async def rotate_secret(self, tenant_id: str, sub_id: str, actor: str) -> tuple[WebhookSubscription, str]:
        sub = await self._require(tenant_id, sub_id)
        try:
            secret = new_secret()
            sub.secret_enc = encrypt(secret)
        except SecretsUnavailable as e:
            raise ComplianceError(str(e)) from None
        sub.secret_rotated_at = sub.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "webhook", "rotate_secret", event_type="webhook.secret_rotated", entity_id=sub.id, actor_user_id=actor)
        return sub, secret

    async def delete(self, tenant_id: str, sub_id: str, actor: str) -> None:
        sub = await self._require(tenant_id, sub_id)
        values = self._audit_values(sub)
        await self.db.execute(delete(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id))
        await self.db.delete(sub)
        await self.db.flush()
        await self.audit.log(tenant_id, "webhook", "delete", event_type="webhook.deleted", entity_id=sub_id, actor_user_id=actor, old_values=values)

    async def ping(self, tenant_id: str, sub_id: str, *, transport=None, resolver=None) -> WebhookDelivery:
        """Invia subito un evento di prova (`webhook.ping`), senza ritentativi e senza toccare lo stato dell'endpoint."""
        sub = await self._require(tenant_id, sub_id)
        delivery_id = str(uuid.uuid4())
        delivery = WebhookDelivery(
            id=delivery_id, tenant_id=tenant_id, subscription_id=sub.id, event_id=f"ping-{delivery_id}", event_name=PING,
            payload=envelope(delivery_id, PING, tenant_id, datetime.utcnow().isoformat(), "webhook", sub.id, {}),
            status=DeliveryStatus.PENDING, next_attempt_at=datetime.utcnow())
        self.db.add(delivery)
        await self.db.flush()
        await attempt(self.db, delivery, sub, retry=False, transport=transport, resolver=resolver)
        return delivery

    # ------------------------------------------------------------ consegne
    async def deliveries(self, tenant_id: str, sub_id: str, status: DeliveryStatus | None = None, limit: int = 50) -> list[WebhookDelivery]:
        await self._require(tenant_id, sub_id)
        stmt = select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant_id, WebhookDelivery.subscription_id == sub_id)
        if status:
            stmt = stmt.where(WebhookDelivery.status == status)
        return list((await self.db.execute(stmt.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id).limit(limit))).scalars().all())

    async def retry_delivery(self, tenant_id: str, delivery_id: str, actor: str) -> WebhookDelivery:
        delivery = (await self.db.execute(select(WebhookDelivery).where(
            WebhookDelivery.tenant_id == tenant_id, WebhookDelivery.id == delivery_id))).scalar_one_or_none()
        if delivery is None:
            raise ComplianceNotFound("Consegna non trovata")
        if delivery.status != DeliveryStatus.DEAD or delivery.event_name == PING:
            raise ComplianceError("Si possono riprovare solo le consegne fallite definitivamente")
        sub = await self.db.get(WebhookSubscription, delivery.subscription_id)
        if not sub.active:
            raise ComplianceError("L'endpoint è disattivato: riattivarlo prima di riprovare")
        delivery.status, delivery.attempts, delivery.next_attempt_at, delivery.claimed_until = DeliveryStatus.PENDING, 0, datetime.utcnow(), None
        delivery.delivered_at = None
        await self.db.flush()
        await self.audit.log(tenant_id, "webhook", "retry_delivery", event_type="webhook.delivery_retried", entity_id=sub.id, actor_user_id=actor,
                             new_values={"delivery_id": delivery.id, "event": delivery.event_name})
        return delivery

    async def purge(self, tenant_id: str, before: datetime) -> int:
        result = await self.db.execute(delete(WebhookDelivery).where(
            WebhookDelivery.tenant_id == tenant_id, WebhookDelivery.status != DeliveryStatus.PENDING, WebhookDelivery.created_at < before))
        return result.rowcount or 0

    async def counts(self, tenant_id: str) -> dict[str, dict[str, int]]:
        rows = await self.db.execute(select(WebhookDelivery.subscription_id, WebhookDelivery.status, func.count()).where(
            WebhookDelivery.tenant_id == tenant_id).group_by(WebhookDelivery.subscription_id, WebhookDelivery.status))
        out: dict[str, dict[str, int]] = {}
        for sub_id, status, n in rows.all():
            out.setdefault(sub_id, {})[status.value] = n
        return out
