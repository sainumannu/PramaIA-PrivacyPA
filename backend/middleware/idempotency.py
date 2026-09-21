"""
Idempotency-Key (§131) per le richieste POST: un retry con la stessa chiave restituisce la risposta della prima
richiesta senza rieseguirla.

- La chiave vale per l'utente e l'ente (dal JWT, letto senza verificarlo: la risposta si memorizza solo se la
  richiesta è andata a buon fine, e quindi il token era valido).
- Stessa chiave con metodo, percorso o corpo diversi: 422. Richiesta originale ancora in corso: 409.
- Si memorizzano solo le risposte 2xx in JSON; gli errori non si "congelano", così il client può correggere e riprovare.
- I caricamenti multipart e i corpi molto grandi non vengono bufferizzati: passano senza idempotenza.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from backend.models.privacypa_idempotency import IdempotencyRecord

_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
MAX_BODY = 1_000_000
MAX_STORED = 1_000_000


def _identity(authorization: str) -> tuple[str, str] | None:
    """(tenant, subject) dal payload del JWT, senza verifica della firma."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    try:
        segment = parts[1].split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError):
        return None
    tenant, subject = payload.get("tenant_id"), payload.get("sub") or payload.get("email")
    return (str(tenant), str(subject)) if tenant and subject else None


async def _json_response(send, status: int, detail: str, extra: list | None = None):
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *(extra or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class IdempotencyMiddleware:
    def __init__(self, app, session_factory, ttl_hours: int = 24):
        self.app, self.session_factory, self.ttl = app, session_factory, timedelta(hours=ttl_hours)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        key = headers.get("idempotency-key")
        if key is None or headers.get("content-type", "").lower().startswith("multipart/"):
            return await self.app(scope, receive, send)
        if not _KEY_RE.match(key):
            return await _json_response(send, 400, "Idempotency-Key non valida: 8-128 caratteri tra lettere, numeri e _ . : -")
        who = _identity(headers.get("authorization", ""))
        if who is None or int(headers.get("content-length", "0") or 0) > MAX_BODY:
            return await self.app(scope, receive, send)

        body, more = b"", True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                return await self.app(scope, _replay(body, receive, more), send)
        request_hash = hashlib.sha256(
            b"\x00".join([scope["method"].encode(), scope["path"].encode(), scope.get("query_string", b""), body])
        ).hexdigest()

        tenant, subject = who
        async with self.session_factory() as db:
            existing = (await db.execute(select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == tenant, IdempotencyRecord.subject == subject, IdempotencyRecord.key == key,
            ))).scalar_one_or_none()
            if existing is not None and existing.expires_at < datetime.utcnow():
                await db.delete(existing)
                await db.commit()
                existing = None
            if existing is not None:
                if existing.request_hash != request_hash:
                    return await _json_response(send, 422, "Idempotency-Key già usata con una richiesta diversa")
                if existing.response_status is None:
                    return await _json_response(send, 409, "Una richiesta con questa Idempotency-Key è ancora in corso")
                return await self._replay_stored(send, existing)
            record = IdempotencyRecord(
                tenant_id=tenant, subject=subject, key=key, request_hash=request_hash,
                expires_at=datetime.utcnow() + self.ttl,
            )
            db.add(record)
            try:
                await db.commit()
            except IntegrityError:  # un'altra richiesta con la stessa chiave ha vinto la corsa
                await db.rollback()
                return await _json_response(send, 409, "Una richiesta con questa Idempotency-Key è ancora in corso")
            record_id = record.id
            await db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < datetime.utcnow()))
            await db.commit()

        captured: dict = {"start": None, "body": b""}

        async def capture(message):
            if message["type"] == "http.response.start":
                captured["start"] = message
            elif message["type"] == "http.response.body":
                captured["body"] += message.get("body", b"")

        try:
            await self.app(scope, _replay(body, receive, False), capture)
        except Exception:
            await self._forget(record_id)
            raise
        start = captured["start"]
        response_headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in start["headers"]]
        is_json = any(k.lower() == "content-type" and "json" in v.lower() for k, v in response_headers)
        if 200 <= start["status"] < 300 and is_json and len(captured["body"]) <= MAX_STORED:
            async with self.session_factory() as db:
                row = await db.get(IdempotencyRecord, record_id)
                row.response_status, row.response_body = start["status"], captured["body"]
                row.response_headers = {k: v for k, v in response_headers if k.lower() in ("content-type", "etag", "location")}
                await db.commit()
        else:
            await self._forget(record_id)
        await send(start)
        await send({"type": "http.response.body", "body": captured["body"]})

    async def _forget(self, record_id: str) -> None:
        async with self.session_factory() as db:
            await db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == record_id))
            await db.commit()

    @staticmethod
    async def _replay_stored(send, record: IdempotencyRecord) -> None:
        headers = [(k.encode(), v.encode()) for k, v in (record.response_headers or {}).items()]
        headers += [(b"content-length", str(len(record.response_body or b"")).encode()), (b"idempotent-replay", b"true")]
        await send({"type": "http.response.start", "status": record.response_status, "headers": headers})
        await send({"type": "http.response.body", "body": record.response_body or b""})


def _replay(body: bytes, receive, more: bool):
    """`receive` che restituisce il corpo già letto (e poi il resto, se era troppo grande per essere bufferizzato)."""
    state = {"sent": False}

    async def _receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": more}
        return await receive()

    return _receive
