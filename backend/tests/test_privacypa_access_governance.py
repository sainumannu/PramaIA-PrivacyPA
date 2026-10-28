"""Richieste di accesso dall'HelpDesk: catalogo, valutazione, revisione del privacy manager, registro, webhook e regola (criteri 1-20)."""
import json
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.auth import privacypa_license as lic
from backend.models.privacypa_access import AccessAssessment
from backend.models.privacypa_approvals import Approval
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_catalog import DataCategoryCatalog
from backend.models.privacypa_identity import PrivacyRoleCatalog, PrivacyRoleCode as R
from backend.models.privacypa_people import Authorization, TrainingCompletion
from backend.models.privacypa_webhooks import WebhookDelivery
from backend.services import privacypa_directory as directory
from backend.services import privacypa_webhook_service as hooks
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
AG = f"{API}/access-governance"
AU = f"{API}/authorizations"
SERVICE = "app:pramaia-helpdesk"
OPERATOR = "operatore-hd-1"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


def hd(tenant=TA, on_behalf=OPERATOR, **extra):
    return {**_h(tenant, SERVICE), "X-On-Behalf-Of": on_behalf, **extra}


@pytest.fixture(autouse=True)
def no_license(monkeypatch):
    """Di default nessuna licenza verificabile: come da standard, il modulo non si blocca."""
    monkeypatch.setattr(lic, "licensed_modules", AsyncMock(return_value=None))


REAL_MEMBER_STATUS = directory.member_status
PEOPLE = {"u-ok": directory.ACTIVE, "u-expired": directory.ACTIVE, "u-never": directory.ACTIVE, "pm2": directory.ACTIVE,
          "u-gone": directory.INACTIVE}


@pytest.fixture(autouse=True)
def portal_directory(monkeypatch):
    """Anagrafica del Portal finta: le persone note sono attive, le altre non esistono."""
    async def status(tenant_id, user_id):
        return PEOPLE.get(user_id, directory.NOT_FOUND)

    monkeypatch.setattr(directory, "member_status", status)


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv("PRIVACYPA_SECRETS_KEY", "test-only-key-for-webhooks")
    requests = []

    def receive(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async def public(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(hooks, "_TRANSPORT", httpx.MockTransport(receive))
    monkeypatch.setattr(hooks, "_resolve", public)
    return requests


@pytest_asyncio.fixture
async def ag(env):
    """Account dell'HelpDesk, privacy manager, sistema con responsabile, corso, categoria e profilo completo."""
    c, grant = env["client"], env["grant"]
    await grant(TA, SERVICE, R.INTEGRATION_HELPDESK)
    await grant(TB, SERVICE, R.INTEGRATION_HELPDESK)
    await grant(TA, "pm", R.PRIVACY_MANAGER)
    await grant(TA, "pm2", R.PRIVACY_MANAGER)
    await grant(TA, "op", R.OPERATOR)
    asset = (await c.post(f"{API}/assets", json={
        "name": "Anagrafe", "owner_unit_id": "unit-a", "data_owner_name": "Mario Bianchi",
        "data_owner_role": "Dirigente Servizi Demografici", "data_owner_user_id": "u-dirigente"}, headers=ADMIN)).json()
    other_asset = (await c.post(f"{API}/assets", json={"name": "Protocollo", "owner_unit_id": "unit-a"}, headers=ADMIN)).json()
    course = (await c.post(f"{API}/training/courses", json={"title": "Privacy di base", "validity_months": 12}, headers=ADMIN)).json()
    async with env["maker"]() as db:
        db.add(DataCategoryCatalog(id="cat-anag", tenant_id=None, code="ANAG", name="Dati anagrafici"))
        db.add(TrainingCompletion(tenant_id=TA, course_id=course["id"], user_id="u-ok", completed_at=TODAY - timedelta(days=30),
                                  next_due_at=TODAY + timedelta(days=300)))
        db.add(TrainingCompletion(tenant_id=TA, course_id=course["id"], user_id="u-expired", completed_at=TODAY - timedelta(days=500),
                                  next_due_at=TODAY - timedelta(days=100)))
        await db.commit()
    r = await c.post(f"{AU}/profiles", json={
        "name": "Operatore anagrafe", "code": "OPERATORE_ANAGRAFE", "asset_id": asset["id"],
        "data_category_ids": ["cat-anag"], "allowed_operations": ["READ", "UPDATE"], "allowed_scope_types": ["ORGANIZATIONAL_UNIT"],
        "processing_activity_ids": [env["ids"]["proc_a"]], "required_training_course_ids": [course["id"]], "max_duration_days": 30,
    }, headers=ADMIN)
    assert r.status_code == 201, r.text
    return {"asset": asset, "other_asset": other_asset, "course": course, "profile": r.json(), "c": c}


_REF = iter(range(1, 10_000))


def request(ag, *, user="u-ok", ref=None, version=1, **over):
    body = {
        "request_type": "GRANT", "subject_user_id": user, "asset_id": ag["asset"]["id"], "profile_id": ag["profile"]["id"],
        "operations": ["READ"], "scope": {"type": "ORGANIZATIONAL_UNIT", "value": "Servizi demografici"},
        "duration": {"type": "TEMPORARY", "valid_from": TODAY.isoformat(), "valid_to": (TODAY + timedelta(days=19)).isoformat()},
        "current_authorization_id": None,
        "source": {"module": "HELPDESK", "ref": ref or f"001-2026-{next(_REF):07d}", "request_version": version},
    }
    body.update(over)
    return body


async def assess(ag, body, headers=None, status=(200, 201)):
    r = await ag["c"].post(f"{AG}/assessments", json=body, headers=headers or hd())
    assert r.status_code in (status if isinstance(status, tuple) else (status,)), r.text
    return r.json()


def results(assessment) -> dict:
    return {c["code"]: c["result"] for c in assessment["checks"]}


async def grant_auth(ag, *, user="u-ok", ops=("READ", "UPDATE"), valid_to=None, ref="001-2026-9000001", headers=None,
                     valid_from=None):
    body = {
        "user_id": user, "asset_id": ag["asset"]["id"], "authorization_profile_id": ag["profile"]["id"],
        "valid_from": valid_from or (TODAY - timedelta(days=10)).isoformat(), "valid_to": valid_to,
        "operations": list(ops), "scope": {"type": "ORGANIZATIONAL_UNIT", "value": "Servizi demografici"},
        "authorized_by_name": "Mario Bianchi, Dirigente", "authorization_reference": "Prot. 12345",
        "authorization_date": TODAY.isoformat(), "profile_version": ag["profile"]["row_version"],
        "source_module": "HELPDESK", "source_ref": ref,
    }
    r = await ag["c"].post(AU, json=body, headers=headers or hd(**{"Idempotency-Key": f"HELPDESK:{ref}:1"}))
    assert r.status_code in (200, 201), r.text
    return r.json()


# ================================================================ valutazione (criteri 1-11)

@pytest.mark.asyncio
async def test_01_standard_grant_passes_every_check_except_instructions(ag):
    a = await assess(ag, request(ag), status=201)
    assert a["outcome"] == "STANDARD"
    assert results(a) == {
        "SUBJECT_RESOLVED": "PASS", "PROFILE_APPROVED": "PASS", "OPERATION_ALLOWED": "PASS", "SCOPE_ALLOWED": "PASS", "TEMPORARY_ACCESS_POLICY": "PASS",
        "TRAINING_VALID": "PASS", "ALREADY_AUTHORIZED": "PASS", "PRIVACY_INSTRUCTIONS_VALID": "NOT_EXECUTABLE",
    }
    assert a["profile"] == {"id": ag["profile"]["id"], "code": "OPERATORE_ANAGRAFE", "name": "Operatore anagrafe", "version": 1}
    assert a["data_categories"] == ["Dati anagrafici"] and a["processing_activities"][0]["id"]
    assert "u-ok" not in json.dumps(a)                       # mai dati della persona nella risposta


@pytest.mark.asyncio
async def test_02_scope_not_allowed_requires_review(ag):
    a = await assess(ag, request(ag, scope={"type": "ALL_RECORDS"}))
    assert a["outcome"] == "REVIEW_REQUIRED" and results(a)["SCOPE_ALLOWED"] == "FAIL"


@pytest.mark.asyncio
async def test_03_operation_not_allowed_requires_review(ag):
    a = await assess(ag, request(ag, operations=["READ", "EXPORT"]))
    assert a["outcome"] == "REVIEW_REQUIRED" and results(a)["OPERATION_ALLOWED"] == "FAIL"
    assert "EXPORT" in next(c["message"] for c in a["checks"] if c["code"] == "OPERATION_ALLOWED")


@pytest.mark.asyncio
async def test_04_expired_training_is_a_warning_by_default(ag):
    a = await assess(ag, request(ag, user="u-expired"))
    assert a["outcome"] == "STANDARD" and results(a)["TRAINING_VALID"] == "WARNING"


@pytest.mark.asyncio
async def test_05_expired_training_blocks_when_the_tenant_says_so(ag):
    c = ag["c"]
    version = (await c.get(f"{API}/policy/integrations", headers=ADMIN)).json()["row_version"]
    r = await c.put(f"{API}/policy/integrations", json={"access_training_blocking": True}, headers=ifm(version, ADMIN))
    assert r.status_code == 200 and r.json()["values"]["access_training_blocking"] is True
    a = await assess(ag, request(ag, user="u-expired"))
    assert a["outcome"] == "REVIEW_REQUIRED" and results(a)["TRAINING_VALID"] == "FAIL"
    missing = await assess(ag, request(ag, user="u-never"))
    assert "non completato" in next(c["message"] for c in missing["checks"] if c["code"] == "TRAINING_VALID")


@pytest.mark.asyncio
async def test_06_unknown_system_or_person(ag):
    a = await assess(ag, request(ag, asset_id="asset-inesistente"))
    assert a["outcome"] == "UNKNOWN" and results(a) == {"SYSTEM_REGISTERED": "FAIL"}
    b = await assess(ag, request(ag, subject_user_id=None))
    assert b["outcome"] == "UNKNOWN" and results(b) == {"SUBJECT_RESOLVED": "FAIL"}


@pytest.mark.asyncio
async def test_07_temporary_access_longer_than_the_profile_allows(ag):
    long = {"type": "TEMPORARY", "valid_from": TODAY.isoformat(), "valid_to": (TODAY + timedelta(days=59)).isoformat()}
    a = await assess(ag, request(ag, duration=long))
    assert a["outcome"] == "REVIEW_REQUIRED" and results(a)["TEMPORARY_ACCESS_POLICY"] == "FAIL"
    permanent = await assess(ag, request(ag, duration={"type": "PERMANENT"}))
    assert results(permanent)["TEMPORARY_ACCESS_POLICY"] == "FAIL"


@pytest.mark.asyncio
async def test_08_change_that_reduces_operations_is_standard(ag):
    current = await grant_auth(ag, valid_to=(TODAY + timedelta(days=15)).isoformat())
    a = await assess(ag, request(ag, request_type="CHANGE", operations=["READ"], current_authorization_id=current["id"],
                                 duration={"type": "TEMPORARY", "valid_from": TODAY.isoformat(),
                                           "valid_to": (TODAY + timedelta(days=10)).isoformat()}))
    assert a["outcome"] == "STANDARD" and results(a) == {"SUBJECT_RESOLVED": "PASS", "CHANGE_REDUCES_PRIVILEGES": "PASS"}
    wider = await assess(ag, request(ag, request_type="CHANGE", operations=["READ", "DELETE"], current_authorization_id=current["id"]))
    assert wider["outcome"] == "REVIEW_REQUIRED" and results(wider)["ALREADY_AUTHORIZED"] == "NOT_APPLICABLE"


@pytest.mark.asyncio
async def test_09_revoke_of_an_active_authorization_is_standard(ag):
    current = await grant_auth(ag)
    a = await assess(ag, request(ag, request_type="REVOKE", operations=[], scope=None, current_authorization_id=current["id"]))
    assert a["outcome"] == "STANDARD"
    other = await assess(ag, request(ag, request_type="REVOKE", operations=[], scope=None, current_authorization_id="nessuna"))
    assert other["outcome"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_10_same_request_version_is_assessed_once(env, ag):
    body = request(ag, ref="001-2026-0000412")
    first = await assess(ag, body, status=201)
    again = await assess(ag, body, status=200)
    assert first == again
    async with env["maker"]() as db:
        rows = (await db.execute(select(AccessAssessment).where(AccessAssessment.source_ref == "001-2026-0000412"))).scalars().all()
    assert len(rows) == 1 and rows[0].requested_on_behalf_of == OPERATOR and rows[0].subject_user_id == "u-ok"


@pytest.mark.asyncio
async def test_11_new_request_version_cancels_the_open_review(env, ag):
    c = ag["c"]
    v1 = await assess(ag, request(ag, ref="001-2026-0000500", scope={"type": "ALL_RECORDS"}))
    r = await c.post(f"{AG}/assessments/{v1['id']}/review", json={"notes": "Serve per il censimento"}, headers=hd())
    assert r.status_code == 201 and r.json()["approval"]["state"] == "PENDING"
    assert (await c.post(f"{AG}/assessments/{v1['id']}/review", json={}, headers=hd())).json()["approval"]["id"] == r.json()["approval"]["id"]
    await assess(ag, request(ag, ref="001-2026-0000500", version=2, scope={"type": "ALL_RECORDS"}))
    reread = (await c.get(f"{AG}/assessments/{v1['id']}", headers=hd())).json()
    assert reread["approval"]["state"] == "CANCELLED"
    late = await c.post(f"{AG}/assessments/{v1['id']}/review", json={}, headers=hd())
    assert late.status_code == 409


# ================================================================ revisione e webhook (12, 17)

async def _subscribe(c, events):
    r = await c.post(f"{API}/webhooks", json={"name": "HelpDesk", "url": "https://helpdesk.example.org/hook", "events": events}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_12_privacy_manager_approval_sends_a_signed_webhook(env, ag, endpoint):
    c = ag["c"]
    sub = await _subscribe(c, ["access_approval.decided"])
    a = await assess(ag, request(ag, ref="001-2026-0000600", scope={"type": "ALL_RECORDS"}))
    await c.post(f"{AG}/assessments/{a['id']}/review", json={"notes": "Censimento"}, headers=hd())

    queue = (await c.get(f"{API}/approvals", params={"decidable": "true"}, headers=_h(TA, "pm"))).json()
    (item,) = [q for q in queue if q["entity_id"] == a["id"]]
    assert item["entity_type"] == "access_assessment" and item["requested_on_behalf_of"] == OPERATOR
    assert item["access"]["source_ref"] == "001-2026-0000600" and item["access"]["subject_user_id"] == "u-ok"
    assert [f["code"] for f in item["access"]["failed_checks"]] == ["SCOPE_ALLOWED"]

    r = await c.post(f"{API}/approvals/{item['id']}/decision", json={"decision": "APPROVED"}, headers=_h(TA, "pm"))
    assert r.status_code == 200, r.text
    assert (await c.post(f"{API}/webhooks/dispatch", headers=ADMIN)).json()["delivered"] == 1
    (sent,) = endpoint
    body = json.loads(sent.content)
    assert body["event"] == "access_approval.decided" and body["data"]["entity_id"] == a["id"]
    assert body["data"]["decision"] == "APPROVED" and body["data"]["source_ref"] == "001-2026-0000600" and body["data"]["request_version"] == 1
    assert "notes" not in body["data"] and "u-ok" not in sent.content.decode()
    assert hooks.verify(sub["secret"], sent.headers["x-privacypa-timestamp"], sent.content, sent.headers["x-privacypa-signature"])

    reread = (await c.get(f"{AG}/assessments/{a['id']}", headers=hd())).json()
    assert reread["approval"]["decision"] == "APPROVED" and reread["approval"]["entity_version"] == 1 and reread["approval"]["approver_id"] == "pm"


@pytest.mark.asyncio
async def test_other_approvals_do_not_trigger_the_access_webhook(env, ag, endpoint):
    c = ag["c"]
    await _subscribe(c, ["access_approval.decided"])
    proc = env["ids"]["proc_a"]
    detail = await c.get(f"{API}/processing-activities/{proc}", headers=ADMIN)
    r = await c.post(f"{API}/processing-activities/{proc}/status", json={"status": "IN_REVIEW"}, headers=ifm(detail.json()["row_version"], _h(TA, "op")))
    assert r.status_code == 200, r.text
    approval = (await c.get(f"{API}/approvals", params={"entity_id": proc}, headers=ADMIN)).json()[0]
    assert (await c.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "APPROVED"}, headers=ADMIN)).status_code == 200
    async with env["maker"]() as db:
        assert (await db.execute(select(WebhookDelivery))).scalars().all() == []


@pytest.mark.asyncio
async def test_decisions_need_notes_and_respect_segregation(ag):
    c = ag["c"]
    a = await assess(ag, request(ag, user="pm2", scope={"type": "ALL_RECORDS"}))
    await c.post(f"{AG}/assessments/{a['id']}/review", json={}, headers=hd(on_behalf="pm"))
    approval = (await c.get(f"{API}/approvals", params={"entity_id": a["id"]}, headers=_h(TA, "pm"))).json()[0]
    for who in ("pm", "pm2"):                     # chi l'ha inviata (tramite l'HelpDesk) e chi riceve l'accesso
        r = await c.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "APPROVED"}, headers=_h(TA, who))
        assert r.status_code == 403, r.text
    assert (await c.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "REJECTED"}, headers=ADMIN)).status_code == 400
    r = await c.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "REJECTED", "notes": "Non motivata"}, headers=ADMIN)
    assert r.status_code == 200
    reread = (await c.get(f"{AG}/assessments/{a['id']}", headers=hd())).json()
    assert reread["approval"]["decision"] == "REJECTED" and reread["approval"]["notes"] == "Non motivata"


@pytest.mark.asyncio
async def test_standard_assessment_cannot_go_to_review(ag):
    a = await assess(ag, request(ag))
    assert (await ag["c"].post(f"{AG}/assessments/{a['id']}/review", json={}, headers=hd())).status_code == 409


@pytest.mark.asyncio
async def test_17_service_token_cannot_decide(ag):
    c = ag["c"]
    a = await assess(ag, request(ag, scope={"type": "ALL_RECORDS"}))
    await c.post(f"{AG}/assessments/{a['id']}/review", json={}, headers=hd())
    approval = (await c.get(f"{API}/approvals", params={"entity_id": a["id"]}, headers=_h(TA, "pm"))).json()[0]
    r = await c.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "APPROVED"}, headers=hd())
    assert r.status_code == 403
    assert (await c.get(f"{API}/approvals", headers=hd())).status_code == 403


# ================================================================ registro (13, 14) e regola (15, 16)

@pytest.mark.asyncio
async def test_13_repeated_registration_returns_the_same_record(env, ag):
    first = await grant_auth(ag, ref="001-2026-0000700")
    again = await grant_auth(ag, ref="001-2026-0000700")            # anche senza Idempotency-Key valida, dopo 24 ore
    assert again["id"] == first["id"]
    async with env["maker"]() as db:
        assert len((await db.execute(select(Authorization).where(Authorization.source_ref == "001-2026-0000700"))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_14_registry_shows_details_ticket_and_profile_version(env, ag):
    created = await grant_auth(ag, ref="001-2026-0000800")
    row = (await ag["c"].get(f"{AU}/{created['id']}", headers=ADMIN)).json()
    assert row["authorized_by_name"] == "Mario Bianchi, Dirigente" and row["authorization_reference"] == "Prot. 12345"
    assert row["source_module"] == "HELPDESK" and row["source_ref"] == "001-2026-0000800" and row["profile_version"] == 1
    assert row["operations"] == ["READ", "UPDATE"] and row["scope"]["type"] == "ORGANIZATIONAL_UNIT" and row["forced"] is False
    async with env["maker"]() as db:
        entry = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "authorization", AuditLog.entity_id == created["id"]))).scalar_one()
    assert entry.actor_user_id == SERVICE and entry.event_metadata == {"on_behalf_of": OPERATOR}
    assert "Prot. 12345" not in json.dumps(entry.new_values)


@pytest.mark.asyncio
async def test_registration_requires_details_and_patch_keeps_what_is_not_sent(ag):
    c = ag["c"]
    bad = await c.post(AU, json={"user_id": "u-ok", "asset_id": ag["asset"]["id"], "authorization_profile_id": ag["profile"]["id"],
                                 "valid_from": TODAY.isoformat(), "source_module": "HELPDESK", "source_ref": "001-2026-0000900"}, headers=hd())
    assert bad.status_code == 400 and "estremi" in bad.text
    created = await grant_auth(ag, ref="001-2026-0000901", valid_to=(TODAY + timedelta(days=20)).isoformat())
    # PATCH dell'HelpDesk: senza user_id, con la nuova scadenza
    r = await c.patch(f"{AU}/{created['id']}", json={"valid_to": (TODAY + timedelta(days=25)).isoformat(), "source_ref": "001-2026-0000902"},
                      headers=ifm(created["row_version"], hd()))
    assert r.status_code == 200, r.text
    assert r.json()["authorization_reference"] == "Prot. 12345" and r.json()["source_ref"] == "001-2026-0000902"
    revoked = await c.post(f"{AU}/{created['id']}/revoke", json={"revoked_reason": "Fine incarico", "revocation_reference": "Prot. 999"},
                           headers=ifm(r.json()["row_version"], hd()))
    assert revoked.status_code == 200 and revoked.json()["revocation_reference"] == "Prot. 999"
    assert revoked.json()["revoked_reason"] == "Fine incarico"


@pytest.mark.asyncio
async def test_15_16_expired_helpdesk_access_opens_a_finding_and_webhook_then_closes(env, ag, endpoint):
    c = ag["c"]
    await _subscribe(c, ["authorization.expired_not_revoked"])
    created = await grant_auth(ag, ref="001-2026-0001000", valid_to=(TODAY - timedelta(days=4)).isoformat())
    recent = await grant_auth(ag, user="u-expired", ref="001-2026-0001001", valid_to=(TODAY - timedelta(days=2)).isoformat())
    await c.post(f"{API}/findings/evaluate", headers=ADMIN)
    findings = [f for f in (await c.get(f"{API}/findings", params={"refresh": "false"}, headers=ADMIN)).json()
                if f["rule_code"] == "AUTHORIZATION_EXPIRED_NOT_REVOKED"]
    assert [f["entity_id"] for f in findings] == [created["id"]]          # 2 giorni: ancora nella tolleranza di 3
    assert findings[0]["severity"] == "MEDIUM"
    await c.post(f"{API}/webhooks/dispatch", headers=ADMIN)
    (sent,) = endpoint
    data = json.loads(sent.content)["data"]
    assert data == {"entity_type": "authorization", "entity_id": created["id"], "source_ref": "001-2026-0001000",
                    "valid_to": (TODAY - timedelta(days=4)).isoformat(), "rule_code": "AUTHORIZATION_EXPIRED_NOT_REVOKED",
                    "rule_version": data["rule_version"]}

    await c.post(f"{AU}/{created['id']}/revoke", json={"reason": "Scaduto"}, headers=ifm(created["row_version"], hd()))
    await c.post(f"{API}/findings/evaluate", headers=ADMIN)
    open_now = [f for f in (await c.get(f"{API}/findings", params={"refresh": "false"}, headers=ADMIN)).json()
                if f["rule_code"] == "AUTHORIZATION_EXPIRED_NOT_REVOKED" and f["status"] == "OPEN"]
    assert open_now == []
    assert recent["id"]


# ================================================================ licenza, tenant, catalogo, permessi (18-20)

@pytest.mark.asyncio
async def test_18_module_not_licensed_on_every_access_governance_api(ag, monkeypatch):
    c = ag["c"]
    monkeypatch.setattr(lic, "licensed_modules", AsyncMock(return_value=["privacypa"]))
    for method, path, body in (("GET", f"{AG}/assets", None), ("GET", f"{AG}/profiles", None),
                               ("POST", f"{AG}/assessments", request(ag)), ("GET", f"{AG}/assessments/x", None),
                               ("POST", f"{AG}/assessments/x/review", {})):
        r = await c.request(method, path, json=body, headers=hd())
        assert r.status_code == 403 and r.json()["detail"]["code"] == "MODULE_NOT_LICENSED", (path, r.text)
    monkeypatch.setattr(lic, "licensed_modules", AsyncMock(return_value=["privacypa", "access_governance"]))
    assert (await c.get(f"{AG}/assets", headers=hd())).status_code == 200
    monkeypatch.setattr(lic, "licensed_modules", AsyncMock(return_value=["*"]))
    assert (await c.get(f"{AG}/assets", headers=hd())).status_code == 200
    monkeypatch.setattr(lic, "licensed_modules", AsyncMock(side_effect=RuntimeError("Portal giù")))
    assert (await c.get(f"{AG}/assets", headers=hd())).status_code == 200      # un errore del controllo non blocca


@pytest.mark.asyncio
async def test_19_other_tenant_header_or_data_is_refused(ag):
    c = ag["c"]
    r = await c.post(f"{AG}/assessments", json=request(ag), headers=hd(**{"X-Tenant-ID": TB}))
    assert r.status_code == 403
    a = await assess(ag, request(ag), headers=hd(**{"X-Tenant-ID": TA}))
    other = await c.get(f"{AG}/assessments/{a['id']}", headers=hd(tenant=TB))
    assert other.status_code == 404 and a["id"] not in other.text
    foreign = await assess(ag, request(ag, asset_id=ag["asset"]["id"]), headers=hd(tenant=TB))
    assert foreign["outcome"] == "UNKNOWN"                                   # il sistema di un altro ente non esiste


@pytest.mark.asyncio
async def test_20_catalog_returns_data_owner_and_active_profiles_only(ag):
    c = ag["c"]
    assets = (await c.get(f"{AG}/assets", headers=hd())).json()
    assert [a["id"] for a in assets] == [ag["asset"]["id"]]                  # «Protocollo» non ha profili
    assert assets[0]["data_owner"] == {"name": "Mario Bianchi", "role": "Dirigente Servizi Demografici", "user_id": "u-dirigente"}
    (profile,) = (await c.get(f"{AG}/profiles", params={"asset_id": ag["asset"]["id"]}, headers=hd())).json()
    assert profile["code"] == "OPERATORE_ANAGRAFE" and profile["allowed_operations"] == ["READ", "UPDATE"]
    assert profile["max_duration_days"] == 30 and profile["version"] == 1 and profile["required_courses"][0]["title"] == "Privacy di base"
    r = await c.patch(f"{AU}/profiles/{ag['profile']['id']}", json={"name": "Operatore anagrafe", "active": False},
                      headers=ifm(ag["profile"]["row_version"], ADMIN))
    assert r.status_code == 200 and r.json()["allowed_operations"] == ["READ", "UPDATE"]   # i campi non inviati restano
    assert (await c.get(f"{AG}/assets", headers=hd())).json() == []
    a = await assess(ag, request(ag))
    assert results(a)["PROFILE_APPROVED"] == "FAIL"


@pytest.mark.asyncio
async def test_profile_changes_publish_the_profile_webhook(env, ag, endpoint):
    c = ag["c"]
    await _subscribe(c, ["authorization_profile.updated"])
    r = await c.patch(f"{AU}/profiles/{ag['profile']['id']}", json={"name": "Operatore anagrafe", "max_duration_days": 15},
                      headers=ifm(ag["profile"]["row_version"], ADMIN))
    assert r.status_code == 200
    await c.post(f"{API}/webhooks/dispatch", headers=ADMIN)
    (sent,) = endpoint
    assert json.loads(sent.content)["data"] == {"entity_type": "authorization_profile", "entity_id": ag["profile"]["id"],
                                                "version": 2, "active": True}


@pytest.mark.asyncio
async def test_helpdesk_role_sees_only_its_modules_and_cannot_manage_profiles(env, ag):
    c = ag["c"]
    for path in (f"{API}/processing-activities", f"{API}/assets", f"{API}/breaches", f"{API}/approvals", f"{API}/training/courses"):
        assert (await c.get(path, headers=hd())).status_code == 403, path
    assert (await c.get(AU, headers=hd())).status_code == 200
    r = await c.post(f"{AU}/profiles", json={"name": "Allargato", "asset_id": ag["asset"]["id"]}, headers=hd())
    assert r.status_code == 403
    r = await c.patch(f"{AU}/profiles/{ag['profile']['id']}", json={"name": "x", "allowed_scope_types": ["ALL_RECORDS"]},
                      headers=ifm(ag["profile"]["row_version"], hd()))
    assert r.status_code == 403
    assert (await c.post(f"{AG}/assessments", json=request(ag), headers=_h(TA, "op"))).status_code == 403


@pytest.mark.asyncio
async def test_helpdesk_role_only_for_service_accounts_on_the_whole_tenant(env, ag):
    c = ag["c"]
    async with env["maker"]() as db:
        db.add(PrivacyRoleCatalog(code=R.INTEGRATION_HELPDESK, name="Integrazione HelpDesk"))
        await db.commit()
    person = await c.post(f"{API}/roles/assign", json={"user_id": "mario", "role_code": "INTEGRATION_HELPDESK", "scope_type": "TENANT"}, headers=ADMIN)
    assert person.status_code == 400
    unit = await c.post(f"{API}/roles/assign", json={"user_id": "app:altro", "role_code": "INTEGRATION_HELPDESK", "scope_type": "UNIT",
                                                     "organizational_unit_id": "unit-a"}, headers=ADMIN)
    assert unit.status_code == 400
    ok = await c.post(f"{API}/roles/assign", json={"user_id": "app:altro", "role_code": "INTEGRATION_HELPDESK", "scope_type": "TENANT"}, headers=ADMIN)
    assert ok.status_code in (200, 201), ok.text


def test_service_roles_cannot_be_added_to_other_permissions():
    from backend.auth.privacypa_permissions import validate_overrides

    with pytest.raises(ValueError):
        validate_overrides({"access.approve": ["PRIVACY_MANAGER", "INTEGRATION_HELPDESK"]})
    with pytest.raises(ValueError):
        validate_overrides({"processing.view": ["OPERATOR", "INTEGRATION_HELPDESK"]})
    with pytest.raises(ValueError):
        validate_overrides({"access.create": ["OPERATOR"]})                 # bloccato: solo l'account di servizio
    assert validate_overrides({"access.approve": ["PRIVACY_MANAGER", "DPO"]}) == {"access.approve": ["SUPER_ADMIN", "TENANT_ADMIN", "PRIVACY_MANAGER", "DPO"]}


@pytest.mark.asyncio
async def test_on_behalf_of_is_recorded_only_for_service_accounts(env, ag):
    c = ag["c"]
    created = await grant_auth(ag, ref="001-2026-0001100", headers={**ADMIN, "X-On-Behalf-Of": "qualcuno"})
    async with env["maker"]() as db:
        entry = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "authorization", AuditLog.entity_id == created["id"]))).scalar_one()
        approvals = (await db.execute(select(Approval))).scalars().all()
    assert entry.actor_user_id == "root" and not (entry.event_metadata or {}).get("on_behalf_of")
    assert approvals == []


# ================================================================ anagrafica del Portal, token, tenant

@pytest.mark.asyncio
async def test_person_must_exist_and_be_active_in_the_portal(ag, monkeypatch):
    gone = await assess(ag, request(ag, user="u-gone"))
    assert gone["outcome"] == "UNKNOWN" and results(gone) == {"SUBJECT_RESOLVED": "FAIL"}
    stranger = await assess(ag, request(ag, user="u-sconosciuto"))
    assert stranger["outcome"] == "UNKNOWN"
    current = await grant_auth(ag, user="u-gone", ref="001-2026-0001200")
    revoke = await assess(ag, request(ag, user="u-gone", request_type="REVOKE", operations=[], scope=None, current_authorization_id=current["id"]))
    assert revoke["outcome"] == "STANDARD" and results(revoke)["SUBJECT_RESOLVED"] == "NOT_APPLICABLE"   # si revoca chi è uscito

    async def down(tenant_id, user_id):
        raise directory.DirectoryUnavailable("Portal giù")

    monkeypatch.setattr(directory, "member_status", down)
    a = await assess(ag, request(ag))
    assert a["outcome"] == "STANDARD" and results(a)["SUBJECT_RESOLVED"] == "NOT_EXECUTABLE"     # il fermo del Portal non blocca


@pytest.mark.asyncio
async def test_directory_reads_the_tenant_users_with_a_short_cache(monkeypatch):
    calls = []

    async def fetch(tenant_id):
        calls.append(tenant_id)
        return {"u1": {"id": "u1", "is_active": True, "membership_active": True},
                "u2": {"id": "u2", "is_active": True, "membership_active": False},
                "u3": {"id": "u3", "is_active": False}}

    monkeypatch.setattr(directory, "member_status", REAL_MEMBER_STATUS)
    monkeypatch.setattr(directory, "_fetch", fetch)
    monkeypatch.setattr(directory, "_users", {})
    assert await directory.member_status("t1", "u1") == directory.ACTIVE
    assert await directory.member_status("t1", "u2") == directory.INACTIVE         # tolto dal tenant (es. sincronizzazione AD)
    assert await directory.member_status("t1", "u3") == directory.INACTIVE         # disattivato nel Portal
    assert calls == ["t1"]                                                          # una sola lettura
    assert await directory.member_status("t1", "nuovo") == directory.NOT_FOUND
    assert calls == ["t1"]                                                          # id sconosciuto ma cache appena letta
    directory._users["t1"] = (directory._users["t1"][0] - directory.UNKNOWN_REFRESH_SECONDS - 1, directory._users["t1"][1])
    assert await directory.member_status("t1", "nuovo") == directory.NOT_FOUND and calls == ["t1", "t1"]


@pytest.mark.asyncio
async def test_people_come_from_the_tenant_users_and_the_org_chart(monkeypatch):
    async def get(path):
        if path.endswith("/users"):
            return [{"id": "u1", "email": "anna@ente.it", "display_name": "Anna Verdi", "is_active": True, "membership_active": True},
                    {"id": "u2", "email": "ex@ente.it", "display_name": "Ex Dipendente", "is_active": True, "membership_active": False},
                    {"id": "u4", "email": "libero@ente.it", "display_name": "Senza Struttura", "is_active": True}]
        return {"root": {"name": "Comune", "members": [{"id": "sindaco", "email": "s@ente.it", "display_name": "Sindaco", "is_active": True}],
                         "children": [{"name": "Servizi demografici", "members": [], "children": [
                             {"name": "Anagrafe", "members": [{"id": "u1", "display_name": "Anna Verdi"}, {"id": "u2"}], "children": []}]}]}}

    monkeypatch.setattr(directory, "_get", get)
    monkeypatch.setattr(directory, "_users", {})
    monkeypatch.setattr(directory, "_org", {})
    people = {p["id"]: p for p in await directory.people("t1")}
    assert people["u1"] == {"id": "u1", "name": "Anna Verdi", "email": "anna@ente.it", "active": True,
                            "units": ["Servizi demografici › Anagrafe"]}
    assert people["u2"]["active"] is False                                      # ancora nell'organigramma, ma uscito dal tenant
    assert people["sindaco"]["units"] == ["Comune"] and people["sindaco"]["active"] is True   # solo nell'organigramma
    assert people["u4"]["units"] == []                                          # utente del tenant fuori dall'organigramma
    assert [p["id"] for p in await directory.people("t1")][-1] == "u4"          # in fondo chi non ha struttura


@pytest.mark.asyncio
async def test_people_endpoint_falls_back_to_free_text_when_the_portal_is_down(ag, monkeypatch):
    c = ag["c"]
    monkeypatch.setattr(directory, "people", AsyncMock(return_value=[{"id": "u1", "name": "Anna Verdi", "email": None, "active": True, "units": []}]))
    ok = await c.get(f"{AU}/people", headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["available"] is True and ok.json()["people"][0]["name"] == "Anna Verdi"
    assert (await c.get(f"{AU}/people", headers=_h(TA, "nessuno"))).status_code == 403

    monkeypatch.setattr(directory, "people", AsyncMock(side_effect=directory.DirectoryUnavailable("Portal giù")))
    down = await c.get(f"{AU}/people", headers=ADMIN)
    assert down.status_code == 200 and down.json()["available"] is False and down.json()["people"] == []


@pytest.mark.asyncio
async def test_directory_without_portal_configuration_is_unavailable(monkeypatch):
    for name in ("PORTAL_URL", "PORTAL_API_URL", "APP_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(directory, "member_status", REAL_MEMBER_STATUS)
    monkeypatch.setattr(directory, "_users", {})
    monkeypatch.setattr(directory, "_token", {"value": None, "expires_at": 0.0})
    with pytest.raises(directory.DirectoryUnavailable):
        await directory.member_status("t1", "u1")


def _jwt(**claims):
    import time

    import jwt as pyjwt

    payload = {"sub": "app:pramaia-helpdesk", "iss": "pramaia-portal", "exp": int(time.time()) + 600, "tenant_id": TA, **claims}
    return pyjwt.encode(payload, "test-secret-key-with-enough-length-0123456789", algorithm="HS256")


def test_service_tokens_with_audience_must_be_addressed_to_privacypa(monkeypatch):
    from fastapi import HTTPException

    from backend.auth.portal_jwt import decode_portal_token

    monkeypatch.setenv("PRAMAIA_JWT_SECRET", "test-secret-key-with-enough-length-0123456789")
    monkeypatch.setenv("APP_ID", "PramaIA-PrivacyManager")
    assert decode_portal_token(_jwt(aud="PramaIA-PrivacyManager", token_type="service")).tenant_id == TA
    assert decode_portal_token(_jwt()).sub == "app:pramaia-helpdesk"                       # senza destinatario: come prima
    with pytest.raises(HTTPException) as e:
        decode_portal_token(_jwt(aud="pramaia-communicator", token_type="service"))
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_switch_proxies_the_portal_and_returns_the_new_token(monkeypatch):
    from fastapi import FastAPI, Request

    from backend.auth.portal_jwt import TokenPayload, get_current_user
    from backend.routers import tenants_router

    seen = {}

    async def fake_switch(authorization, tenant_id):
        seen.update(authorization=authorization, tenant_id=tenant_id)
        return {"access_token": "nuovo", "tenant_id": tenant_id, "tenant_name": "Comune B"}

    async def fake_list(authorization):
        return [{"id": TA, "name": "Comune A"}, {"id": TB, "name": "Comune B"}]

    monkeypatch.setattr(tenants_router, "switch_tenant", fake_switch)
    monkeypatch.setattr(tenants_router, "fetch_my_tenants", fake_list)
    app = FastAPI()
    app.include_router(tenants_router.router)
    def user(request: Request) -> TokenPayload:
        return TokenPayload(json.loads(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        headers = {**_h(TA, "mario"), "Authorization": "Bearer vecchio"}
        assert (await c.get("/api/tenants/me", headers=headers)).json()[1]["name"] == "Comune B"
        r = await c.post("/api/tenants/switch", json={"tenant_id": TB}, headers=headers)
    assert r.json()["access_token"] == "nuovo" and seen == {"authorization": "Bearer vecchio", "tenant_id": TB}


# ================================================================ richieste dell'HelpDesk del 27 ottobre (A1-A4)

@pytest.mark.asyncio
async def test_registry_can_include_future_accesses_and_names_even_of_inactive_profiles(ag):
    c = ag["c"]
    started = await grant_auth(ag, ref="001-2026-0002000", valid_to=(TODAY + timedelta(days=5)).isoformat())
    future = await grant_auth(ag, ref="001-2026-0002001", valid_from=(TODAY + timedelta(days=30)).isoformat(),
                              valid_to=(TODAY + timedelta(days=40)).isoformat())
    expired = await grant_auth(ag, ref="001-2026-0002002", valid_from=(TODAY - timedelta(days=30)).isoformat(),
                               valid_to=(TODAY - timedelta(days=20)).isoformat())

    def ids(params):
        return c.get(AU, params={"user_id": "u-ok", **params}, headers=hd())

    assert {a["id"] for a in (await ids({"active_only": "true"})).json()} == {started["id"]}
    current = (await ids({"active_only": "true", "include_future": "true"})).json()
    assert {a["id"] for a in current} == {started["id"], future["id"]} and expired["id"] not in {a["id"] for a in current}

    profile = ag["profile"]
    r = await c.patch(f"{AU}/profiles/{profile['id']}", json={"name": profile["name"], "active": False},
                      headers=ifm(profile["row_version"], ADMIN))
    assert r.status_code == 200, r.text
    listed = {a["id"]: a for a in (await ids({})).json()}
    assert listed[future["id"]]["asset_name"] == "Anagrafe" and listed[future["id"]]["profile_code"] == "OPERATORE_ANAGRAFE"
    single = (await c.get(f"{AU}/{future['id']}", headers=hd())).json()
    assert (single["asset_name"], single["profile_code"]) == ("Anagrafe", "OPERATORE_ANAGRAFE")


@pytest.mark.asyncio
async def test_change_is_allowed_before_the_access_starts_but_not_after_it_expired(ag):
    future = await grant_auth(ag, ref="001-2026-0002100", valid_from=(TODAY + timedelta(days=10)).isoformat(),
                              valid_to=(TODAY + timedelta(days=20)).isoformat())
    a = await assess(ag, request(ag, request_type="CHANGE", operations=["READ"], current_authorization_id=future["id"],
                                 duration={"type": "TEMPORARY", "valid_from": (TODAY + timedelta(days=10)).isoformat(),
                                           "valid_to": (TODAY + timedelta(days=15)).isoformat()}))
    assert a["outcome"] == "STANDARD" and results(a)["CHANGE_REDUCES_PRIVILEGES"] == "PASS"

    expired = await grant_auth(ag, ref="001-2026-0002101", valid_to=(TODAY - timedelta(days=1)).isoformat())
    late = await assess(ag, request(ag, request_type="CHANGE", operations=["READ"], current_authorization_id=expired["id"]))
    assert late["outcome"] == "UNKNOWN" and results(late) == {"CURRENT_AUTHORIZATION": "FAIL"}

    # Una concessione già registrata con inizio futuro segnala il doppione come una in vigore
    again = await assess(ag, request(ag))
    assert results(again)["ALREADY_AUTHORIZED"] == "WARNING"


@pytest.mark.asyncio
async def test_ticket_address_is_kept_and_shown_in_the_review_queue(ag):
    c = ag["c"]
    body = request(ag, scope={"type": "ALL_RECORDS"})
    body["source"]["url"] = "https://helpdesk.ente.it/tickets/412"
    a = await assess(ag, body)
    assert a["source"]["url"] == "https://helpdesk.ente.it/tickets/412"
    assert (await c.get(f"{AG}/assessments/{a['id']}", headers=hd())).json()["source"]["url"] == "https://helpdesk.ente.it/tickets/412"
    await c.post(f"{AG}/assessments/{a['id']}/review", json={}, headers=hd())
    (item,) = (await c.get(f"{API}/approvals", params={"entity_id": a["id"]}, headers=_h(TA, "pm"))).json()
    assert item["access"]["source_url"] == "https://helpdesk.ente.it/tickets/412"

    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://helpdesk.ente.it/1", "helpdesk.ente.it/tickets/1"):
        wrong = request(ag)
        wrong["source"]["url"] = bad
        assert (await c.post(f"{AG}/assessments", json=wrong, headers=hd())).status_code == 422, bad
    without = await assess(ag, request(ag))
    assert without["source"]["url"] is None


@pytest.mark.asyncio
async def test_daily_evaluation_opens_the_expired_access_finding_for_every_tenant(env, ag, endpoint):
    from backend.services.privacypa_findings_scheduler import known_tenants, run_scheduled_evaluation

    c = ag["c"]
    await _subscribe(c, ["authorization.expired_not_revoked"])
    created = await grant_auth(ag, ref="001-2026-0002200", valid_to=(TODAY - timedelta(days=5)).isoformat())
    async with env["maker"]() as db:
        assert {TA, TB} <= set(await known_tenants(db))

    results_by_tenant = await run_scheduled_evaluation(session_factory=env["maker"])
    assert results_by_tenant[TA] is not None and results_by_tenant[TB] is not None
    findings = [f for f in (await c.get(f"{API}/findings", params={"refresh": "false"}, headers=ADMIN)).json()
                if f["rule_code"] == "AUTHORIZATION_EXPIRED_NOT_REVOKED"]
    assert [f["entity_id"] for f in findings] == [created["id"]]
    await c.post(f"{API}/webhooks/dispatch", headers=ADMIN)
    assert [json.loads(r.content)["event"] for r in endpoint] == ["authorization.expired_not_revoked"]
