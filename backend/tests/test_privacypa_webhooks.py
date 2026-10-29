"""Webhook (§70): sottoscrizioni, firma, consegna con ritentativi, sicurezza della destinazione e nuovi eventi di scadenza."""
import asyncio
import json
import zipfile
from datetime import datetime, timedelta
import io

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_assessments import Dpia, DpiaStatus
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_webhooks import DeliveryStatus, WebhookDelivery, WebhookSubscription
from backend.services import privacypa_webhook_service as hooks
from backend.services.privacypa_secrets import decrypt
from backend.tests.conftest import TA, TB, _h, ifm
from backend.tests.test_privacypa_confidentiality import _set_policy, strong

API = "/api/privacypa"
WH = f"{API}/webhooks"
ADMIN = _h(TA, "root", "admin")
ADMIN_B = _h(TB, "root", "admin")
URL = "https://hooks.example.org/privacypa"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def secrets_key(monkeypatch):
    monkeypatch.setenv("PRIVACYPA_SECRETS_KEY", "test-only-key-for-webhooks")
    for name in ("WEBHOOK_ALLOW_HTTP", "WEBHOOK_ALLOWED_HOSTS", "WEBHOOK_ALLOW_PRIVATE_NETWORKS"):
        monkeypatch.delenv(name, raising=False)


class Endpoint:
    """Ricevente finto: registra le richieste e risponde con i codici indicati (l'ultimo si ripete)."""

    def __init__(self, *codes):
        self.codes, self.requests = list(codes or (200,)), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.codes.pop(0) if len(self.codes) > 1 else self.codes[0])


@pytest.fixture
def endpoint(monkeypatch):
    ep = Endpoint()
    monkeypatch.setattr(hooks, "_TRANSPORT", httpx.MockTransport(ep))

    async def public(host, port):
        return [PUBLIC_IP]

    monkeypatch.setattr(hooks, "_resolve", public)
    return ep


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role in (("mgr", R.PRIVACY_MANAGER), ("op", R.OPERATOR), ("dpo", R.DPO)):
        await grant(TA, user, role, ScopeType.TENANT, None)
    return {k: _h(TA, k) for k in ("mgr", "op", "dpo")}


async def _sub(env, events=("breach.created",), *, name="Reminder", url=URL, who=ADMIN, **extra):
    r = await env["client"].post(WH, json={"name": name, "url": url, "events": list(events), **extra}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def _deliveries(env):
    async with env["maker"]() as db:
        return list((await db.execute(select(WebhookDelivery).order_by(WebhookDelivery.created_at, WebhookDelivery.id))).scalars().all())


async def _edit(env, model, row_id, **values):
    async with env["maker"]() as db:
        row = await db.get(model, row_id)
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


async def _breach(env, who, title="Perdita chiavetta"):
    aware = (datetime.utcnow() - timedelta(hours=3)).replace(microsecond=0).isoformat()
    r = await env["client"].post(f"{API}/breaches", json={"title": title, "organizational_unit_id": "unit-a", "aware_at": aware, "description": "Testo riservato"}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def _dispatch(env, who=ADMIN):
    r = await env["client"].post(f"{WH}/dispatch", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def _due_now(env, delivery):
    return _edit(env, WebhookDelivery, delivery.id, next_attempt_at=datetime.utcnow() - timedelta(seconds=1))


# ================================================================ firma (unit)

def test_signature_binds_timestamp_and_body_and_rejects_old_or_tampered_requests():
    body = b'{"event":"breach.created"}'
    sig = hooks.sign("whsec_a", "1000", body)
    assert sig.startswith("v1=") and len(sig) == 3 + 64
    assert hooks.verify("whsec_a", "1000", body, sig, now=1100)
    assert not hooks.verify("whsec_a", "1000", body, sig, now=1000 + 301)          # troppo vecchia (replay)
    assert not hooks.verify("whsec_b", "1000", body, sig, now=1000)                # altro segreto
    assert not hooks.verify("whsec_a", "1000", body + b" ", sig, now=1000)          # corpo alterato
    assert not hooks.verify("whsec_a", "1001", body, sig, now=1001)                # timestamp alterato
    assert not hooks.verify("whsec_a", "abc", body, sig, now=1000) and not hooks.verify("whsec_a", "1000", body, "", now=1000)


def test_url_policy_accepts_only_https_public_addresses(monkeypatch):
    assert hooks.check_url("https://hooks.example.org:8443/x") == ("hooks.example.org", 8443)
    for bad in ("http://hooks.example.org/x", "ftp://hooks.example.org", "https://user:pw@hooks.example.org/x", "https:///x", "hooks.example.org",
                "https://127.0.0.1/x", "https://10.1.2.3/x", "https://192.168.0.5/x", "https://169.254.169.254/latest", "https://[::1]/x",
                "https://[::ffff:127.0.0.1]/x", "https://0.0.0.0/x", "https://100.64.0.1/x", "https://hooks.example.org:99999/x", "https://" + "a" * 600):
        with pytest.raises(hooks.UrlNotAllowed):
            hooks.check_url(bad)
    monkeypatch.setenv("WEBHOOK_ALLOW_HTTP", "true")
    assert hooks.check_url("http://hooks.example.org/x")[1] == 80
    monkeypatch.setenv("WEBHOOK_ALLOWED_HOSTS", "10.1.2.3")
    assert hooks.check_url("https://10.1.2.3/x")[0] == "10.1.2.3"
    monkeypatch.delenv("WEBHOOK_ALLOWED_HOSTS")
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_NETWORKS", "true")
    assert hooks.check_url("https://192.168.0.5/x")[0] == "192.168.0.5"


# ================================================================ sottoscrizioni

@pytest.mark.asyncio
async def test_only_administrators_manage_webhooks(env, w):
    c = env["client"]
    assert [e["name"] for e in (await c.get(f"{WH}/events", headers=ADMIN)).json()] == [
        "processing.updated", "processor.expiring", "task.overdue", "breach.created", "dpia.review_due", "document.expiring", "finding.opened",
        "access_approval.decided", "authorization_profile.updated", "authorization.expired_not_revoked"]
    for who in ("mgr", "op", "dpo"):
        assert (await c.get(WH, headers=w[who])).status_code == 403
        assert (await c.get(f"{WH}/events", headers=w[who])).status_code == 403
        assert (await c.post(WH, json={"name": "X", "url": URL, "events": ["breach.created"]}, headers=w[who])).status_code == 403
    assert (await c.post(f"{WH}/dispatch", headers=w["mgr"])).status_code == 403


@pytest.mark.asyncio
async def test_creation_validates_and_shows_the_secret_only_once(env):
    c = env["client"]
    created = await _sub(env, ["breach.created", "breach.created", "task.overdue"], name=" Reminder ")
    assert created["secret"].startswith("whsec_") and len(created["secret"]) > 40
    assert created["events"] == ["breach.created", "task.overdue"] and created["name"] == "Reminder" and created["active"] is True
    assert "secret_enc" not in created
    for shown in (await c.get(WH, headers=ADMIN)).json() + [(await c.get(f"{WH}/{created['id']}", headers=ADMIN)).json()]:
        assert "secret" not in shown and "secret_enc" not in shown

    async with env["maker"]() as db:
        stored = await db.get(WebhookSubscription, created["id"])
        assert stored.secret_enc != created["secret"] and created["secret"] not in stored.secret_enc      # cifrato a riposo
        assert decrypt(stored.secret_enc) == created["secret"]

    body = {"name": "X", "url": URL, "events": ["breach.created"]}
    for bad, reason in (
        ({**body, "url": "http://hooks.example.org/x"}, "HTTPS"), ({**body, "url": "https://127.0.0.1/x"}, "pubblici"),
        ({**body, "url": "https://u:p@hooks.example.org/x"}, "credenziali"), ({**body, "events": ["nope"]}, "sconosciuti"),
        ({**body, "events": ["webhook.ping"]}, "sconosciuti"),
    ):
        r = await c.post(WH, json=bad, headers=ADMIN)
        assert r.status_code == 400 and reason in r.text, (bad, r.text)
    for bad in ({**body, "events": []}, {**body, "name": ""}, {**body, "url": ""}, {**body, "name": "x" * 121}, {"name": "X"}):
        assert (await c.post(WH, json=bad, headers=ADMIN)).status_code == 422


@pytest.mark.asyncio
async def test_creation_needs_a_secrets_key_and_a_limit(env, monkeypatch):
    c = env["client"]
    monkeypatch.delenv("PRIVACYPA_SECRETS_KEY")
    monkeypatch.delenv("PRAMAIA_JWT_SECRET", raising=False)
    r = await c.post(WH, json={"name": "X", "url": URL, "events": ["breach.created"]}, headers=ADMIN)
    assert r.status_code == 400 and "Chiave dei segreti" in r.text
    monkeypatch.setenv("PRIVACYPA_SECRETS_KEY", "another-key")
    for i in range(hooks.MAX_SUBSCRIPTIONS):
        await _sub(env, name=f"Endpoint {i}")
    assert (await c.post(WH, json={"name": "Uno di troppo", "url": URL, "events": ["breach.created"]}, headers=ADMIN)).status_code == 400


@pytest.mark.asyncio
async def test_update_rotation_deletion_versioning_and_tenant_isolation(env):
    c = env["client"]
    sub = await _sub(env, ["breach.created"])
    url = f"{WH}/{sub['id']}"
    body = {"name": "Nuovo nome", "url": "https://other.example.org/hook", "events": ["task.overdue", "dpia.review_due"], "active": True}
    assert (await c.put(url, json=body, headers=ADMIN)).status_code == 428
    assert (await c.put(url, json=body, headers=ifm(9, ADMIN))).status_code == 409
    assert (await c.put(url, json={**body, "url": "http://insecure.example.org"}, headers=ifm(1, ADMIN))).status_code == 400
    updated = await c.put(url, json=body, headers=ifm(1, ADMIN))
    assert updated.status_code == 200 and updated.json()["events"] == ["task.overdue", "dpia.review_due"] and updated.headers["etag"] == '"2"'
    assert "secret" not in updated.json()

    old = (await c.get(f"{WH}", headers=ADMIN)).json()[0]
    rotated = (await c.post(f"{url}/rotate-secret", headers=ADMIN)).json()
    assert rotated["secret"].startswith("whsec_") and rotated["secret"] != sub["secret"]
    async with env["maker"]() as db:
        assert decrypt((await db.get(WebhookSubscription, sub["id"])).secret_enc) == rotated["secret"]
    assert old["secret_rotated_at"] is not None

    for call in (c.get(url, headers=ADMIN_B), c.put(url, json=body, headers=ifm(3, ADMIN_B)), c.delete(url, headers=ADMIN_B),
                 c.post(f"{url}/rotate-secret", headers=ADMIN_B), c.post(f"{url}/test", headers=ADMIN_B), c.get(f"{url}/deliveries", headers=ADMIN_B)):
        assert (await call).status_code == 404
    assert (await c.get(WH, headers=ADMIN_B)).json() == []

    assert (await c.delete(url, headers=ADMIN)).status_code == 204
    assert (await c.get(url, headers=ADMIN)).status_code == 404 and (await c.delete(url, headers=ADMIN)).status_code == 404


@pytest.mark.asyncio
async def test_audit_never_carries_the_secret_or_the_full_url(env):
    c = env["client"]
    sub = await _sub(env, url="https://hooks.example.org/private/path?token=abc123")
    await c.post(f"{WH}/{sub['id']}/rotate-secret", headers=ADMIN)
    await c.delete(f"{WH}/{sub['id']}", headers=ADMIN)
    async with env["maker"]() as db:
        events = list((await db.execute(select(AuditLog).where(AuditLog.entity_type == "webhook"))).scalars().all())
    assert [e.event_type for e in events] == ["webhook.created", "webhook.secret_rotated", "webhook.deleted"]
    raw = json.dumps([[e.old_values, e.new_values, e.event_metadata] for e in events])
    assert "abc123" not in raw and "whsec_" not in raw and "/private/path" not in raw and "hooks.example.org" in raw
    assert not [e for e in (await c.get(f"{API}/outbox", headers=ADMIN)).json() if e["event_type"].startswith("webhook.")]     # la gestione degli endpoint non genera eventi


@pytest.mark.asyncio
async def test_admin_step_up_applies_to_webhook_changes(env):
    c = env["client"]
    await _set_policy(env, {"step_up": {"admin_actions": True}})
    body = {"name": "X", "url": URL, "events": ["breach.created"]}
    r = await c.post(WH, json=body, headers=ADMIN)
    assert r.status_code == 403 and r.headers.get("x-step-up-required")
    created = await c.post(WH, json=body, headers=strong("root", tenant_role="admin"))
    assert created.status_code == 201
    assert (await c.get(WH, headers=ADMIN)).status_code == 200                           # la lettura non lo richiede
    assert (await c.post(f"{WH}/{created.json()['id']}/rotate-secret", headers=ADMIN)).status_code == 403
    assert (await c.delete(f"{WH}/{created.json()['id']}", headers=ADMIN)).status_code == 403


# ================================================================ eventi

@pytest.mark.asyncio
async def test_events_create_minimal_deliveries_only_for_matching_active_subscriptions(env, w):
    c = env["client"]
    only_breach = await _sub(env, ["breach.created"], name="A")
    both = await _sub(env, ["breach.created", "processing.updated"], name="B")
    inactive = await _sub(env, ["breach.created"], name="C", active=False)
    other_tenant = (await c.post(WH, json={"name": "T", "url": URL, "events": ["breach.created"]}, headers=ADMIN_B)).json()
    breach = await _breach(env, w["mgr"], title="Titolo segreto")
    rows = await _deliveries(env)
    assert sorted(d.subscription_id for d in rows) == sorted([only_breach["id"], both["id"]])
    assert inactive["id"] not in {d.subscription_id for d in rows} and other_tenant["id"] not in {d.subscription_id for d in rows}
    d = rows[0]
    assert d.status == DeliveryStatus.PENDING and d.attempts == 0 and d.event_name == "breach.created"
    assert d.payload["id"] == d.id and d.payload["event"] == "breach.created" and d.payload["tenant_id"] == TA
    assert d.payload["data"]["entity_type"] == "data_breach" and d.payload["data"]["entity_id"] == breach["id"]
    assert set(d.payload["data"]) <= {"entity_type", "entity_id", "status", "aware_at"}
    raw = json.dumps(d.payload)
    for secret in ("Titolo segreto", "Testo riservato", "mgr"):
        assert secret not in raw                                                          # nessun testo descrittivo né persona

    proc = await c.post(f"{API}/processing-activities", json={"title": "Nuovo trattamento", "organizational_unit_id": "unit-a"}, headers=w["mgr"])
    assert proc.status_code == 201
    assert [x.event_name for x in (await _deliveries(env))[2:]] == ["processing.updated"]
    assert (await _deliveries(env))[-1].subscription_id == both["id"]


@pytest.mark.asyncio
async def test_rejected_requests_and_rolled_back_changes_create_no_delivery(env, w):
    c = env["client"]
    await _sub(env, ["breach.created"])
    r = await c.post(f"{API}/breaches", json={"title": "Senza descrizione", "organizational_unit_id": "unit-a", "aware_at": datetime.utcnow().isoformat()}, headers=w["mgr"])
    assert r.status_code == 422 and await _deliveries(env) == []
    assert (await c.post(f"{API}/breaches", json={"title": "X", "organizational_unit_id": "unit-a", "aware_at": datetime.utcnow().isoformat(), "description": "d"}, headers=w["op"])).status_code == 403
    assert await _deliveries(env) == []


@pytest.mark.asyncio
async def test_new_deadline_rules_open_findings_that_feed_the_webhooks(env, w):
    c, maker = env["client"], env["maker"]
    subscribed = ["task.overdue", "dpia.review_due", "document.expiring", "finding.opened", "processor.expiring"]
    sub = await _sub(env, subscribed)
    long_ago = "2020-01-01"
    task = (await c.post(f"{API}/tasks", json={"title": "Vecchio", "due_at": f"{long_ago}T10:00:00"}, headers=w["mgr"])).json()
    dpia = (await c.post(f"{API}/dpias", json={"title": "DPIA vecchia", "processing_activity_id": env["ids"]["proc_a"]}, headers=w["mgr"])).json()
    await _edit(env, Dpia, dpia["id"], status=DpiaStatus.APPROVED, next_review_date=datetime(2020, 1, 1).date())
    doc = (await c.post(f"{API}/documents", json={"title": "Nomina", "document_type": "OTHER"}, headers=w["mgr"])).json()
    up = await c.post(f"{API}/documents/{doc['id']}/versions", files={"file": ("n.txt", b"x", "text/plain")}, data={"expiry_date": long_ago}, headers=w["mgr"])
    assert up.status_code == 200, up.text

    stats = (await c.post(f"{API}/findings/evaluate", headers=w["mgr"])).json()
    assert stats["evaluated_rules"] == 34
    by_rule = {f["rule_code"]: f for f in (await c.get(f"{API}/findings", params={"status": "ALL", "refresh": "false"}, headers=w["mgr"])).json()}
    assert by_rule["TASK_OVERDUE"]["entity_id"] == task["id"] and by_rule["DPIA_REVIEW_DUE"]["entity_id"] == dpia["id"]
    assert by_rule["DOCUMENT_EXPIRING"]["entity_id"] == doc["id"]
    assert "Vecchio" not in by_rule["TASK_OVERDUE"]["message"] and by_rule["TASK_OVERDUE"]["message"].startswith(f"Il task {task['code']} è scaduto")

    got = {}
    for d in await _deliveries(env):
        got.setdefault(d.event_name, []).append(d.payload["data"])
    assert {(x["entity_type"], x["entity_id"]) for x in got["task.overdue"]} == {("task", task["id"])}
    assert {(x["entity_type"], x["entity_id"]) for x in got["dpia.review_due"]} == {("dpia", dpia["id"])}
    assert {(x["entity_type"], x["entity_id"]) for x in got["document.expiring"]} == {("document", doc["id"])}
    assert {x["rule_code"] for x in got["finding.opened"]} >= {"TASK_OVERDUE", "DPIA_REVIEW_DUE", "DOCUMENT_EXPIRING"}
    assert "processor.expiring" not in got and all(d.subscription_id == sub["id"] for d in await _deliveries(env))

    before = len(await _deliveries(env))
    await c.post(f"{API}/findings/evaluate", headers=w["mgr"])                              # rivalutare non ripete gli eventi
    assert len(await _deliveries(env)) == before
    # una volta sistemata la causa il finding si chiude da solo
    await _edit(env, Dpia, dpia["id"], next_review_date=datetime(2099, 1, 1).date())
    await c.post(f"{API}/findings/evaluate", headers=w["mgr"])
    assert "DPIA_REVIEW_DUE" not in {f["rule_code"] for f in (await c.get(f"{API}/findings", params={"refresh": "false"}, headers=w["mgr"])).json()}


# ================================================================ consegna

@pytest.mark.asyncio
async def test_dispatch_sends_a_signed_minimal_request_and_records_success(env, w, endpoint):
    sub = await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    (delivery,) = await _deliveries(env)
    assert (await _dispatch(env)) == {"due": 1, "delivered": 1, "retry": 0, "dead": 0, "skipped": 0}

    (request,) = endpoint.requests
    assert str(request.url) == URL and request.method == "POST" and request.headers["content-type"] == "application/json"
    assert request.headers["x-privacypa-event"] == "breach.created" and request.headers["x-privacypa-delivery"] == delivery.id
    body = request.content
    assert json.loads(body) == delivery.payload
    assert hooks.verify(sub["secret"], request.headers["x-privacypa-timestamp"], body, request.headers["x-privacypa-signature"])
    assert not hooks.verify("whsec_altro", request.headers["x-privacypa-timestamp"], body, request.headers["x-privacypa-signature"])

    (done,) = await _deliveries(env)
    assert done.status == DeliveryStatus.DELIVERED and done.attempts == 1 and done.last_status_code == 200 and done.delivered_at and done.last_error is None
    listed = (await env["client"].get(f"{WH}", headers=ADMIN)).json()[0]
    assert listed["last_success_at"] and listed["failure_streak"] == 0 and listed["deliveries"] == {"DELIVERED": 1}
    assert (await _dispatch(env))["due"] == 0 and len(endpoint.requests) == 1               # nulla da ripetere


@pytest.mark.asyncio
async def test_failed_deliveries_back_off_then_die_and_can_be_retried(env, w, endpoint):
    c = env["client"]
    endpoint.codes = [500]
    sub = await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    (delivery,) = await _deliveries(env)

    for attempt in range(1, hooks.MAX_ATTEMPTS):
        await _due_now(env, delivery)
        assert (await _dispatch(env))["retry"] == 1
        row = (await _deliveries(env))[0]
        wait = (row.next_attempt_at - datetime.utcnow()).total_seconds()
        expected = hooks.BACKOFF_SECONDS[attempt - 1]
        assert row.status == DeliveryStatus.PENDING and row.attempts == attempt and expected - 5 < wait <= expected and row.last_error == "HTTP 500"
        assert (await _dispatch(env))["due"] == 0                                           # non è ancora il momento
    await _due_now(env, delivery)
    assert (await _dispatch(env))["dead"] == 1
    dead = (await _deliveries(env))[0]
    assert dead.status == DeliveryStatus.DEAD and dead.attempts == hooks.MAX_ATTEMPTS
    assert (await c.get(f"{WH}/{sub['id']}", headers=ADMIN)).json()["failure_streak"] == 1

    assert (await c.post(f"{WH}/deliveries/{dead.id}/retry", headers=w["mgr"])).status_code == 403
    endpoint.codes = [204]
    assert (await c.post(f"{WH}/deliveries/{dead.id}/retry", headers=ADMIN)).json()["status"] == "PENDING"
    assert (await c.post(f"{WH}/deliveries/{dead.id}/retry", headers=ADMIN)).status_code == 400      # non più fallita
    assert (await _dispatch(env))["delivered"] == 1
    assert (await c.get(f"{WH}/{sub['id']}", headers=ADMIN)).json()["failure_streak"] == 0
    assert (await c.post(f"{WH}/deliveries/nope/retry", headers=ADMIN)).status_code == 404
    assert (await c.post(f"{WH}/deliveries/{dead.id}/retry", headers=ADMIN_B)).status_code == 404


@pytest.mark.asyncio
async def test_a_gone_endpoint_is_disabled_and_stops_receiving_events(env, w, endpoint):
    c = env["client"]
    endpoint.codes = [410]
    sub = await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    assert (await _dispatch(env))["dead"] == 1
    got = (await c.get(f"{WH}/{sub['id']}", headers=ADMIN)).json()
    assert got["active"] is False and got["disabled_reason"] == "gone"
    await _breach(env, w["mgr"], title="Seconda")
    assert len(await _deliveries(env)) == 1                                                  # niente più consegne
    dead = (await _deliveries(env))[0]
    assert (await c.post(f"{WH}/deliveries/{dead.id}/retry", headers=ADMIN)).status_code == 400          # prima va riattivato

    version = got["row_version"]
    back = await c.put(f"{WH}/{sub['id']}", json={"name": got["name"], "url": got["url"], "events": got["events"], "active": True}, headers=ifm(version, ADMIN))
    assert back.json()["active"] is True and back.json()["disabled_reason"] is None and back.json()["failure_streak"] == 0
    await _breach(env, w["mgr"], title="Terza")
    assert len(await _deliveries(env)) == 2


@pytest.mark.asyncio
async def test_too_many_dead_deliveries_disable_the_endpoint(env, w, endpoint):
    endpoint.codes = [503]
    sub = await _sub(env, ["breach.created"])
    await _edit(env, WebhookSubscription, sub["id"], failure_streak=hooks.MAX_STREAK - 1)
    await _breach(env, w["mgr"])
    (delivery,) = await _deliveries(env)
    await _edit(env, WebhookDelivery, delivery.id, attempts=hooks.MAX_ATTEMPTS - 1)
    assert (await _dispatch(env))["dead"] == 1
    got = (await env["client"].get(f"{WH}/{sub['id']}", headers=ADMIN)).json()
    assert got["active"] is False and got["disabled_reason"] == "too_many_failures" and got["last_failure_at"]


@pytest.mark.asyncio
async def test_destination_is_checked_before_every_send(env, w, endpoint, monkeypatch):
    async def private(host, port):
        return ["10.0.0.5"]

    sub = await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    monkeypatch.setattr(hooks, "_resolve", private)                                          # il DNS ora punta alla rete interna
    assert (await _dispatch(env))["retry"] == 1
    row = (await _deliveries(env))[0]
    assert endpoint.requests == [] and "non consentito" in row.last_error and row.last_status_code is None

    async def mixed(host, port):
        return [PUBLIC_IP, "127.0.0.1"]

    monkeypatch.setattr(hooks, "_resolve", mixed)
    await _due_now(env, row)
    assert (await _dispatch(env))["retry"] == 1 and endpoint.requests == []                  # basta un indirizzo interno

    monkeypatch.setenv("WEBHOOK_ALLOWED_HOSTS", "hooks.example.org")                         # host fidato da configurazione
    await _due_now(env, row)
    assert (await _dispatch(env))["delivered"] == 1 and len(endpoint.requests) == 1
    assert sub["id"]


@pytest.mark.asyncio
async def test_redirects_are_not_followed_and_errors_do_not_leak_the_url(env, w, endpoint, monkeypatch):
    endpoint.codes = [302]
    await _sub(env, ["breach.created"], url="https://hooks.example.org/secret-token-123")
    await _breach(env, w["mgr"])
    assert (await _dispatch(env))["retry"] == 1 and len(endpoint.requests) == 1
    row = (await _deliveries(env))[0]
    assert row.last_status_code == 302 and row.last_error == "HTTP 302"

    def boom(request):
        raise httpx.ConnectError("no route to https://hooks.example.org/secret-token-123")

    monkeypatch.setattr(hooks, "_TRANSPORT", httpx.MockTransport(boom))
    await _due_now(env, row)
    await _dispatch(env)
    row = (await _deliveries(env))[0]
    assert row.last_error == "ConnectError" and "secret-token" not in row.last_error


@pytest.mark.asyncio
async def test_ping_is_immediate_signed_and_does_not_change_the_endpoint_state(env, endpoint):
    c = env["client"]
    sub = await _sub(env, ["breach.created"])
    ok = (await c.post(f"{WH}/{sub['id']}/test", headers=ADMIN)).json()
    assert ok["status"] == "DELIVERED" and ok["payload"]["event"] == "webhook.ping" and ok["payload"]["data"]["entity_id"] == sub["id"]
    request = endpoint.requests[0]
    assert request.headers["x-privacypa-event"] == "webhook.ping"
    assert hooks.verify(sub["secret"], request.headers["x-privacypa-timestamp"], request.content, request.headers["x-privacypa-signature"])

    endpoint.codes = [500]
    failed = (await c.post(f"{WH}/{sub['id']}/test", headers=ADMIN)).json()
    assert failed["status"] == "DEAD" and failed["attempts"] == 1 and failed["last_error"] == "HTTP 500"      # nessun ritentativo
    got = (await c.get(f"{WH}/{sub['id']}", headers=ADMIN)).json()
    assert got["failure_streak"] == 0 and got["active"] is True and got["last_success_at"] is None
    endpoint.codes = [410]
    await c.post(f"{WH}/{sub['id']}/test", headers=ADMIN)
    assert (await c.get(f"{WH}/{sub['id']}", headers=ADMIN)).json()["active"] is True                   # 410 su una prova non disattiva
    dead_ping = (await _deliveries(env))[-1]
    assert (await c.post(f"{WH}/deliveries/{dead_ping.id}/retry", headers=ADMIN)).status_code == 400
    assert (await c.post(f"{WH}/{sub['id']}/test", headers=ADMIN_B)).status_code == 404


@pytest.mark.asyncio
async def test_rotated_secret_signs_the_next_delivery_and_old_one_stops_working(env, w, endpoint):
    sub = await _sub(env, ["breach.created"])
    new = (await env["client"].post(f"{WH}/{sub['id']}/rotate-secret", headers=ADMIN)).json()["secret"]
    await _breach(env, w["mgr"])
    await _dispatch(env)
    request = endpoint.requests[0]
    args = (request.headers["x-privacypa-timestamp"], request.content, request.headers["x-privacypa-signature"])
    assert hooks.verify(new, *args) and not hooks.verify(sub["secret"], *args)


@pytest.mark.asyncio
async def test_unreadable_secret_is_a_failure_not_a_crash(env, w, endpoint, monkeypatch):
    await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    monkeypatch.setenv("PRIVACYPA_SECRETS_KEY", "changed-key")                                # chiave cambiata dopo la creazione
    assert (await _dispatch(env))["retry"] == 1 and endpoint.requests == []
    assert "rigenerarlo" in (await _deliveries(env))[0].last_error


@pytest.mark.asyncio
async def test_claimed_deliveries_are_not_sent_twice_and_dispatch_is_per_tenant(env, w, endpoint):
    await _sub(env, ["breach.created"])
    await env["client"].post(WH, json={"name": "B", "url": URL, "events": ["breach.created"]}, headers=ADMIN_B)
    await _breach(env, w["mgr"])
    (delivery,) = await _deliveries(env)
    await _edit(env, WebhookDelivery, delivery.id, claimed_until=datetime.utcnow() + timedelta(seconds=60))     # presa in carico da un altro worker
    assert (await _dispatch(env))["due"] == 0 and endpoint.requests == []
    await _edit(env, WebhookDelivery, delivery.id, claimed_until=datetime.utcnow() - timedelta(seconds=1))      # lease scaduto
    assert (await _dispatch(env, ADMIN_B))["due"] == 0                                        # un altro ente non tocca le consegne
    assert (await _dispatch(env))["delivered"] == 1 and (await _deliveries(env))[0].claimed_until is None


@pytest.mark.asyncio
async def test_background_worker_delivers_and_stops_cleanly(env, w, endpoint):
    await _sub(env, ["breach.created"])
    await _breach(env, w["mgr"])
    stop = asyncio.Event()
    task = asyncio.create_task(hooks.run_worker(env["maker"], stop, interval=0.05))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if (await _deliveries(env))[0].status == DeliveryStatus.DELIVERED:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert (await _deliveries(env))[0].status == DeliveryStatus.DELIVERED and len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_delivery_history_listing_and_purge(env, w, endpoint):
    c = env["client"]
    sub = await _sub(env, ["breach.created"])
    for title in ("Uno", "Due"):
        await _breach(env, w["mgr"], title=title)
    await _dispatch(env)
    await _breach(env, w["mgr"], title="Tre")
    url = f"{WH}/{sub['id']}/deliveries"
    assert len((await c.get(url, headers=ADMIN)).json()) == 3
    assert [d["status"] for d in (await c.get(url, params={"status": "PENDING"}, headers=ADMIN)).json()] == ["PENDING"]
    assert len((await c.get(url, params={"limit": 2}, headers=ADMIN)).json()) == 2
    assert (await c.get(url, params={"status": "WHATEVER"}, headers=ADMIN)).status_code == 422
    assert (await c.get(url, headers=w["mgr"])).status_code == 403

    old = datetime.utcnow() - timedelta(days=40)
    for d in (await _deliveries(env)):
        await _edit(env, WebhookDelivery, d.id, created_at=old)
    assert (await c.delete(f"{WH}/deliveries", params={"older_than_days": 30}, headers=w["mgr"])).status_code == 403
    assert (await c.delete(f"{WH}/deliveries", params={"older_than_days": 30}, headers=ADMIN)).json() == {"removed": 2}     # la in coda resta
    assert [d.status for d in await _deliveries(env)] == [DeliveryStatus.PENDING]
    assert (await c.delete(f"{WH}/{sub['id']}", headers=ADMIN)).status_code == 204 and await _deliveries(env) == []


@pytest.mark.asyncio
async def test_tenant_export_never_contains_webhook_secrets(env):
    c = env["client"]
    sub = await _sub(env, ["breach.created"])
    await c.post(f"{WH}/{sub['id']}/test", headers=ADMIN)
    r = await c.post(f"{API}/export/tenant", headers=ADMIN)
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert not [n for n in names if "webhook" in n]
        raw = b"".join(zf.read(n) for n in names if n.endswith(".json"))
    assert sub["secret"].encode() not in raw and b"secret_enc" not in raw
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.json"))
    assert {"privacypa_webhook_subscriptions", "privacypa_webhook_deliveries"} <= {e["table"] for e in manifest["excluded_tables"]}
