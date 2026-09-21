"""Data breach (§26, §157), richieste degli interessati (§27, §158) e audit strutturati (§28)."""
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.services.privacypa_compliance_service import add_months
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
BR, RQ, AU, CA = f"{API}/breaches", f"{API}/requests", f"{API}/audits", f"{API}/corrective-actions"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


def ago(**kw) -> str:
    return (datetime.utcnow() - timedelta(**kw)).replace(microsecond=0).isoformat()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("ict", R.ICT, ScopeType.TENANT, None), ("op", R.OPERATOR, ScopeType.TENANT, None),
        ("reader", R.READ_ONLY, ScopeType.TENANT, None), ("auditor", R.AUDITOR, ScopeType.TENANT, None),
        ("ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a"), ("ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b"),
        ("op-a", R.OPERATOR, ScopeType.UNIT, "unit-a"), ("op-b", R.OPERATOR, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "ict", "op", "reader", "auditor", "ref-a", "ref-b", "op-a", "op-b")} | {"b": _h(TB, "b-mgr")}


async def ver(c, url, who):
    return (await c.get(url, headers=who)).json()["row_version"]


async def _finding_codes(env, who, code):
    r = await env["client"].get(f"{API}/findings", params={"code": code}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ data breach

def _breach(**override):
    return {"title": "Invio errato di un elenco", "organizational_unit_id": "unit-a", "aware_at": ago(hours=10),
            "description": "Elenco con dati di contatto inviato al destinatario sbagliato", "confidentiality_affected": True, **override}


@pytest.mark.asyncio
async def test_breach_is_registered_with_a_non_binding_timer(env, w):
    c = env["client"]
    created = await c.post(BR, json=_breach(detected_at=ago(hours=12)), headers=w["ict"])
    assert created.status_code == 201, created.text
    b = created.json()
    assert b["code"] == "BR-000001" and b["status"] == "OPEN" and b["notify_authority"] is None
    assert 9.9 <= b["hours_since_aware"] <= 10.2 and b["reference_72h_at"] > b["aware_at"]
    assert (await c.post(BR, json=_breach(aware_at=(datetime.utcnow() + timedelta(hours=2)).isoformat()), headers=w["ict"])).status_code == 400
    assert (await c.post(BR, json=_breach(organizational_unit_id="unit-x"), headers=w["ict"])).status_code == 400
    assert (await c.post(BR, json=_breach(approx_records=-1), headers=w["ict"])).status_code == 422

    # sicurezza: dati sensibili non visibili a operatori e sola lettura; per struttura per gli altri
    for who in ("op", "reader"):
        assert (await c.get(BR, headers=w[who])).status_code == 403
    assert [x["id"] for x in (await c.get(BR, headers=w["ref-a"])).json()] == [b["id"]]
    assert (await c.get(BR, headers=w["ref-b"])).json() == []
    assert (await c.get(f"{BR}/{b['id']}", headers=w["ref-b"])).status_code == 403
    assert (await c.get(f"{BR}/{b['id']}", headers=w["b"])).status_code == 404
    assert (await c.post(BR, json=_breach(), headers=w["ref-b"])).status_code == 403                      # struttura diversa


@pytest.mark.asyncio
async def test_risk_and_notification_decisions_belong_to_governance_and_must_be_reasoned(env, w):
    c = env["client"]
    b = (await c.post(BR, json=_breach(), headers=w["ref-a"])).json()
    body = _breach(risk_level="MEDIUM", risk_rationale="Dati di contatto verso un solo destinatario")
    body.pop("aware_at")
    assert (await c.patch(f"{BR}/{b['id']}", json=body, headers=ifm(1, w["ref-a"]))).status_code == 403    # decisione: solo governo
    assert (await c.post(BR, json=_breach(notify_authority=False, notify_authority_rationale="x"), headers=w["ref-a"])).status_code == 403

    assert (await c.patch(f"{BR}/{b['id']}", json={**body, "risk_rationale": None}, headers=ifm(1, w["dpo"]))).status_code == 400
    assert (await c.patch(f"{BR}/{b['id']}", json={**body, "notify_authority": False}, headers=ifm(1, w["dpo"]))).status_code == 400
    assert (await c.patch(f"{BR}/{b['id']}", json={**body, "authority_notified_at": ago(hours=1)}, headers=ifm(1, w["dpo"]))).status_code == 400
    assert (await c.patch(f"{BR}/{b['id']}", json={**body, "notify_subjects": False}, headers=ifm(1, w["dpo"]))).status_code == 400
    ok = await c.patch(f"{BR}/{b['id']}", json={**body, "notify_authority": False,
                                                 "notify_authority_rationale": "Rischio improbabile: un destinatario, richiesta di cancellazione"},
                       headers=ifm(1, w["dpo"]))
    assert ok.status_code == 200 and ok.json()["notify_authority"] is False and ok.json()["row_version"] == 2
    # modifiche non decisionali restano possibili a chi ha il permesso di modifica
    plain = {**body, "notify_authority": False, "notify_authority_rationale": ok.json()["notify_authority_rationale"], "title": "Titolo corretto"}
    assert (await c.patch(f"{BR}/{b['id']}", json=plain, headers=ifm(2, w["ref-a"]))).status_code == 200
    assert (await c.patch(f"{BR}/{b['id']}", json=plain, headers=w["ref-a"])).status_code == 428


@pytest.mark.asyncio
async def test_aware_at_correction_keeps_the_original_and_is_audited(env, w):
    c = env["client"]
    b = (await c.post(BR, json=_breach(aware_at=ago(hours=30)), headers=w["ict"])).json()
    original = b["aware_at"]
    new_time = ago(hours=20)
    url = f"{BR}/{b['id']}/aware-at"

    assert (await c.post(url, json={"aware_at": new_time, "reason": "x"}, headers=ifm(1, w["ict"]))).status_code == 403      # decisione di governo
    assert (await c.post(url, json={"aware_at": new_time, "reason": ""}, headers=ifm(1, w["dpo"]))).status_code == 422
    assert (await c.post(url, json={"aware_at": original, "reason": "uguale"}, headers=ifm(1, w["dpo"]))).status_code == 400
    assert (await c.post(url, json={"aware_at": (datetime.utcnow() + timedelta(hours=3)).isoformat(), "reason": "futuro"}, headers=ifm(1, w["dpo"]))).status_code == 400
    first = await c.post(url, json={"aware_at": new_time, "reason": "Il registro accessi indica la conoscenza alle 16:40"}, headers=ifm(1, w["dpo"]))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["aware_at"] == new_time and body["aware_at_original"] == original
    assert body["aware_at_corrected_by"] == "dpo" and body["aware_at_correction_reason"].startswith("Il registro")
    second = await c.post(url, json={"aware_at": ago(hours=25), "reason": "Nuova evidenza"}, headers=ifm(2, w["dpo"]))
    assert second.json()["aware_at_original"] == original                                       # l'originale non si perde

    events = (await c.get(f"{API}/audit", params={"entity_id": b["id"]}, headers=ADMIN)).json()
    corrections = [e for e in events if e["event_type"] == "data_breach.aware_at_corrected"]
    assert len(corrections) == 2
    assert all("reason" in e["new_values"] and e["actor_user_id"] == "dpo" for e in corrections)


@pytest.mark.asyncio
async def test_breach_status_links_and_deletion_rules(env, w):
    c, ids = env["client"], env["ids"]
    b = (await c.post(BR, json=_breach(), headers=w["ict"])).json()
    url = f"{BR}/{b['id']}"
    assert (await c.post(f"{url}/status", json={"status": "CLOSED"}, headers=ifm(1, w["dpo"]))).status_code == 400     # mancano le valutazioni
    assert (await c.post(f"{url}/status", json={"status": "ASSESSING"}, headers=ifm(1, w["ict"]))).status_code == 200
    assert (await c.post(f"{url}/status", json={"status": "CLOSED"}, headers=ifm(2, w["ict"]))).status_code == 403      # chiudere: governo

    # collegamenti (§26.3), con controllo di scope sui trattamenti
    party = (await c.post(f"{API}/parties", json={"legal_name": "Fornitore", "party_type": "PROCESSOR"}, headers=w["mgr"])).json()
    links = {"links": [{"entity_type": "processing_activity", "entity_id": ids["proc_a"]}, {"entity_type": "party", "entity_id": party["id"]}]}
    assert (await c.put(f"{url}/links", json={"links": [{"entity_type": "spaceship", "entity_id": "x"}]}, headers=ifm(2, w["ict"]))).status_code == 400
    assert (await c.put(f"{url}/links", json={"links": [{"entity_type": "party", "entity_id": "nope"}]}, headers=ifm(2, w["ict"]))).status_code == 400
    linked = await c.put(f"{url}/links", json=links, headers=ifm(2, w["ict"]))
    assert linked.status_code == 200 and len(linked.json()["links"]) == 2 and linked.json()["row_version"] == 3
    assert (await c.put(f"{url}/links", json=links, headers=ifm(3, w["ict"]))).json()["row_version"] == 3            # invariato

    ready = _breach()
    ready.pop("aware_at")
    decided = await c.patch(url, json={**ready, "risk_level": "LOW", "risk_rationale": "Contenuta", "notify_authority": False,
                                       "notify_authority_rationale": "Rischio improbabile"}, headers=ifm(3, w["dpo"]))
    assert decided.status_code == 200, decided.text
    closed = await c.post(f"{url}/status", json={"status": "CLOSED"}, headers=ifm(4, w["dpo"]))
    assert closed.status_code == 200 and closed.json()["closed_at"] and closed.json()["status"] == "CLOSED"
    assert (await c.patch(url, json=ready, headers=ifm(5, w["dpo"]))).status_code == 400                                # chiusa
    assert (await c.delete(url, headers=w["dpo"])).status_code == 400                                                    # documentata: si conserva
    assert (await c.post(f"{url}/status", json={"status": "ASSESSING"}, headers=ifm(5, w["ict"]))).status_code == 403    # riaprire: governo
    assert (await c.post(f"{url}/status", json={"status": "ASSESSING"}, headers=ifm(5, w["dpo"]))).json()["closed_at"] is None

    fresh = (await c.post(BR, json=_breach(title="Bozza"), headers=w["ict"])).json()
    assert (await c.delete(f"{BR}/{fresh['id']}", headers=w["ict"])).status_code == 403
    assert (await c.delete(f"{BR}/{fresh['id']}", headers=w["dpo"])).status_code == 204


@pytest.mark.asyncio
async def test_breach_data_stays_out_of_audit_and_outbox_and_generates_a_reminder(env, w):
    c = env["client"]
    b = (await c.post(BR, json=_breach(aware_at=ago(hours=60), description="Dettaglio riservato con nomi"), headers=w["ict"])).json()
    audit = str((await c.get(f"{API}/audit", params={"entity_type": "data_breach"}, headers=ADMIN)).json())
    outbox = str((await c.get(f"{API}/outbox", params={"event_type": "data_breach."}, headers=ADMIN)).json())
    assert "Dettaglio riservato" not in audit + outbox and "Invio errato" not in audit + outbox
    assert "data_breach.create" in outbox

    (finding,) = await _finding_codes(env, w["dpo"], "BREACH_NOTIFICATION_DECISION_MISSING")
    assert finding["entity_id"] == b["id"] and finding["severity"] == "HIGH" and finding["params"]["hours"] >= 59
    assert "non stabilisce" in finding["condition"] or "Violazione non chiusa" in finding["condition"]
    assert (await _finding_codes(env, w["ref-b"], "BREACH_NOTIFICATION_DECISION_MISSING")) == []                       # altra struttura
    assert len(await _finding_codes(env, w["ref-a"], "BREACH_NOTIFICATION_DECISION_MISSING")) == 1

    ready = _breach()
    ready.pop("aware_at")
    await c.patch(f"{BR}/{b['id']}", json={**ready, "risk_level": "LOW", "risk_rationale": "ok", "notify_authority": True,
                                           "notify_authority_rationale": "Notifica effettuata"}, headers=ifm(1, w["dpo"]))
    assert await _finding_codes(env, w["dpo"], "BREACH_NOTIFICATION_DECISION_MISSING") == []


# ================================================================ richieste degli interessati

def _request(**override):
    return {"organizational_unit_id": "unit-a", "request_type": "ACCESS", "received_at": ago(days=3), "channel": "PEC",
            "requester_reference": "mario.rossi@example.org", **override}


def test_month_arithmetic_uses_calendar_months():
    assert add_months(datetime(2026, 1, 31, 9, 30), 1) == datetime(2026, 2, 28, 9, 30)
    assert add_months(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)                  # anno bisestile
    assert add_months(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)
    assert add_months(datetime(2026, 12, 31), 2) == datetime(2027, 2, 28)


@pytest.mark.asyncio
async def test_request_deadline_extension_and_configurable_terms(env, w):
    c = env["client"]
    received = datetime.utcnow().replace(microsecond=0) - timedelta(days=3)
    r = (await c.post(RQ, json=_request(received_at=received.isoformat()), headers=w["op-a"])).json()
    assert r["code"] == "RI-000001" and r["status"] == "RECEIVED" and r["identity_verified"] is False
    assert r["due_at"] == add_months(received, 1).isoformat()

    url = f"{RQ}/{r['id']}/extension"
    assert (await c.post(url, json={"reason": "Complessità"}, headers=ifm(1, w["op-a"]))).status_code == 403                # serve chi decide
    assert (await c.post(url, json={"reason": ""}, headers=ifm(1, w["dpo"]))).status_code == 422
    ext = await c.post(url, json={"reason": "Numero elevato di richieste", "communicated_at": ago(days=1)}, headers=ifm(1, w["dpo"]))
    assert ext.status_code == 200, ext.text
    assert ext.json()["due_at"] == add_months(received, 3).isoformat() and ext.json()["extension_applied"] is True
    assert (await c.post(url, json={"reason": "Ancora"}, headers=ifm(2, w["dpo"]))).status_code == 400                      # una sola volta

    # termini configurabili dall'ente (§158): base di 2 mesi, proroga di 1
    pol = f"{API}/policy/deadlines"
    assert (await c.get(pol, headers=w["mgr"])).status_code == 403
    assert (await c.get(pol, headers=ADMIN)).json() == {"row_version": 0, "values": {"dsr_base_months": 1, "dsr_extension_months": 2, "processing_review_months": 12, "dpia_review_months": 12}}
    assert (await c.put(pol, json={"dsr_base_months": 9}, headers=ifm(0, ADMIN))).status_code == 400
    assert (await c.put(pol, json={"dsr_base_months": 2}, headers=ADMIN)).status_code == 428
    assert (await c.put(pol, json={"dsr_base_months": 2, "dsr_extension_months": 1}, headers=ifm(0, ADMIN))).status_code == 200
    assert (await c.put(pol, json={"dsr_base_months": 3}, headers=ifm(0, ADMIN))).status_code == 409
    r2 = (await c.post(RQ, json=_request(received_at=received.isoformat()), headers=w["op-a"])).json()
    assert r2["due_at"] == add_months(received, 2).isoformat()
    assert (await c.post(f"{RQ}/{r2['id']}/extension", json={"reason": "Complessità"}, headers=ifm(1, w["dpo"]))).json()["due_at"] == add_months(received, 3).isoformat()
    events = (await c.get(f"{API}/audit", params={"entity_type": "tenant_policy"}, headers=ADMIN)).json()
    assert [e["event_type"] for e in events] == ["policy.deadlines.updated"]


@pytest.mark.asyncio
async def test_request_response_flow_tasks_and_privacy(env, w):
    c = env["client"]
    r = (await c.post(RQ, json=_request(), headers=w["op-a"])).json()
    url = f"{RQ}/{r['id']}"
    assert (await c.post(f"{url}/status", json={"status": "RESPONDED", "outcome": "ok"}, headers=ifm(1, w["dpo"]))).status_code == 400   # non ammessa da RECEIVED
    assert (await c.post(f"{url}/status", json={"status": "IN_PROGRESS"}, headers=ifm(1, w["op-a"]))).status_code == 200
    assert (await c.post(f"{url}/status", json={"status": "RESPONDED", "outcome": "Inviati i dati"}, headers=ifm(2, w["op-a"]))).status_code == 403
    assert (await c.post(f"{url}/status", json={"status": "RESPONDED", "outcome": "Inviati i dati"}, headers=ifm(2, w["dpo"]))).status_code == 400  # identità non verificata

    body = _request(identity_verified=True, identity_verification_notes="Documento d'identità controllato")
    assert (await c.patch(url, json=body, headers=ifm(2, w["op-a"]))).status_code == 200
    assert (await c.post(f"{url}/status", json={"status": "RESPONDED"}, headers=ifm(3, w["dpo"]))).status_code == 400                          # serve l'esito

    tasks = await c.post(f"{url}/tasks", json={"unit_ids": ["unit-a", "unit-b"], "notes": "Estrarre i dati dal gestionale"}, headers=w["op-a"])
    assert tasks.status_code == 201 and len(tasks.json()) == 2
    assert {t["assigned_unit_id"] for t in tasks.json()} == {"unit-a", "unit-b"} and {t["source_type"] for t in tasks.json()} == {"REQUEST"}
    assert all("mario.rossi" not in str(t) for t in tasks.json()) and all(t["source_id"] == r["id"] for t in tasks.json())
    assert (await c.post(f"{url}/tasks", json={"unit_ids": ["unit-x"]}, headers=w["op-a"])).status_code == 400
    assert (await c.post(f"{url}/tasks", json={"unit_ids": []}, headers=w["op-a"])).status_code == 422

    done = await c.post(f"{url}/status", json={"status": "RESPONDED", "outcome": "Copia dei dati inviata via PEC"}, headers=ifm(3, w["dpo"]))
    assert done.status_code == 200 and done.json()["response_sent_at"] and done.json()["outcome"].startswith("Copia")
    assert (await c.post(f"{url}/tasks", json={"unit_ids": ["unit-a"]}, headers=w["op-a"])).status_code == 400
    assert (await c.post(f"{url}/status", json={"status": "CLOSED"}, headers=ifm(4, w["dpo"]))).status_code == 200
    assert (await c.patch(url, json=body, headers=ifm(5, w["dpo"]))).status_code == 400

    # il riferimento del richiedente non esce dall'API: né audit né outbox
    audit = str((await c.get(f"{API}/audit", params={"entity_type": "data_subject_request"}, headers=ADMIN)).json())
    outbox = str((await c.get(f"{API}/outbox", params={"event_type": "data_subject_request."}, headers=ADMIN)).json())
    assert "mario.rossi" not in audit + outbox and "Documento d'identità" not in audit + outbox and "Copia dei dati" not in audit + outbox
    assert "data_subject_request.status.closed" in outbox


@pytest.mark.asyncio
async def test_request_scope_permissions_and_deletion(env, w):
    c = env["client"]
    r = (await c.post(RQ, json=_request(), headers=w["op-a"])).json()
    assert (await c.post(RQ, json=_request(), headers=w["op-b"])).status_code == 403
    assert (await c.post(RQ, json=_request(), headers=w["reader"])).status_code == 403
    assert (await c.post(RQ, json=_request(received_at=(datetime.utcnow() + timedelta(days=2)).isoformat()), headers=w["dpo"])).status_code == 400
    assert (await c.get(RQ, headers=w["reader"])).status_code == 403
    assert [x["id"] for x in (await c.get(RQ, headers=w["op-a"])).json()] == [r["id"]]
    assert (await c.get(RQ, headers=w["op-b"])).json() == []
    assert (await c.get(f"{RQ}/{r['id']}", headers=w["op-b"])).status_code == 403
    assert (await c.get(f"{RQ}/{r['id']}", headers=w["b"])).status_code == 404
    assert (await c.delete(f"{RQ}/{r['id']}", headers=w["op-a"])).status_code == 403
    assert (await c.delete(f"{RQ}/{r['id']}", headers=w["dpo"])).status_code == 204


@pytest.mark.asyncio
async def test_request_deadlines_generate_findings_scoped_by_unit(env, w):
    c = env["client"]
    soon = (await c.post(RQ, json=_request(received_at=ago(days=26)), headers=w["dpo"])).json()          # scade tra pochi giorni
    late = (await c.post(RQ, json=_request(received_at=ago(days=50)), headers=w["dpo"])).json()          # già scaduta
    quiet = (await c.post(RQ, json=_request(received_at=ago(days=2)), headers=w["dpo"])).json()

    approaching = await _finding_codes(env, w["dpo"], "DSR_DEADLINE_APPROACHING")
    overdue = await _finding_codes(env, w["dpo"], "DSR_OVERDUE")
    assert [f["entity_id"] for f in approaching] == [soon["id"]] and [f["entity_id"] for f in overdue] == [late["id"]]
    assert overdue[0]["severity"] == "HIGH" and all("mario.rossi" not in str(f) for f in approaching + overdue)
    assert (await _finding_codes(env, w["ref-b"], "DSR_OVERDUE")) == []
    assert len(await _finding_codes(env, w["ref-a"], "DSR_OVERDUE")) == 1

    r = await c.post(f"{RQ}/{late['id']}/extension", json={"reason": "Complessità"}, headers=ifm(1, w["dpo"]))
    assert r.status_code == 200
    assert await _finding_codes(env, w["dpo"], "DSR_OVERDUE") == []          # la proroga sposta il termine
    assert quiet["id"] not in {f["entity_id"] for f in await _finding_codes(env, w["dpo"], "DSR_DEADLINE_APPROACHING")}


# ================================================================ audit strutturati

def _audit(**override):
    return {"title": "Audit trattamenti Servizi Sociali", "audit_type": "INTERNAL", "organizational_unit_id": "unit-a",
            "planned_date": iso(10), "lead_auditor_user_id": "auditor", **override}


@pytest.mark.asyncio
async def test_audit_lifecycle_findings_and_corrective_actions(env, w):
    c = env["client"]
    created = await c.post(AU, json=_audit(), headers=w["auditor"])
    assert created.status_code == 201, created.text
    a = created.json()
    assert a["code"] == "AU-000001" and a["status"] == "PLANNED" and a["findings"] == [] and created.headers["etag"] == '"1"'
    assert (await c.post(AU, json=_audit(), headers=w["op"])).status_code == 403
    assert (await c.post(AU, json=_audit(organizational_unit_id="unit-x"), headers=w["auditor"])).status_code == 400

    url = f"{AU}/{a['id']}"
    assert (await c.post(f"{url}/status", json={"status": "COMPLETED"}, headers=ifm(1, w["auditor"]))).status_code == 400     # non ammessa
    started = await c.post(f"{url}/status", json={"status": "IN_PROGRESS"}, headers=ifm(1, w["auditor"]))
    assert started.status_code == 200
    finding = await c.post(f"{url}/findings", json={"severity": "HIGH", "title": "Registro incompleto", "description": "Mancano le basi giuridiche",
                                                     "evidence": "Verbale del 12/05"}, headers=w["auditor"])
    assert finding.status_code == 201, finding.text
    fid = finding.json()["id"]
    assert (await c.post(f"{url}/findings", json={"severity": "URGENT", "title": "x", "description": "y"}, headers=w["auditor"])).status_code == 422
    assert (await c.post(f"{url}/status", json={"status": "COMPLETED"}, headers=ifm(await ver(c, url, w["auditor"]), w["auditor"]))).status_code == 400    # serve la sintesi

    action = await c.post(f"{url}/findings/{fid}/corrective-action", json={"owner_user_id": "resp-1", "due_date": iso(20), "priority": "HIGH"}, headers=w["mgr"])
    assert action.status_code == 201, action.text
    assert action.json()["source_type"] == "AUDIT" and action.json()["source_id"] == a["id"] and action.json()["organizational_unit_id"] == "unit-a"
    detail = (await c.get(url, headers=w["auditor"])).json()
    assert detail["findings"][0]["status"] == "ACTION_PLANNED" and detail["open_findings"] == 1
    assert [x["id"] for x in (await c.get(CA, params={"source_id": a["id"]}, headers=w["mgr"])).json()] == [action.json()["id"]]

    assert (await c.patch(f"{url}/findings/{fid}", json={"status": "CLOSED"}, headers=w["auditor"])).json()["status"] == "CLOSED"
    assert (await c.post(f"{url}/findings/{fid}/corrective-action", json={"owner_user_id": "r", "due_date": iso(5)}, headers=w["mgr"])).status_code == 400
    done = await c.post(f"{url}/status", json={"status": "COMPLETED", "performed_date": iso(-1), "summary": "Un rilievo grave, chiuso con azione"}, headers=ifm(await ver(c, url, w["auditor"]), w["auditor"]))
    assert done.status_code == 200 and done.json()["performed_date"] == iso(-1) and done.json()["status"] == "COMPLETED"
    assert (await c.post(f"{url}/findings", json={"severity": "LOW", "title": "t", "description": "d"}, headers=w["auditor"])).status_code == 400
    assert (await c.patch(url, json=_audit(), headers=ifm(done.json()["row_version"], w["auditor"]))).status_code == 400
    assert (await c.delete(url, headers=w["dpo"])).status_code == 400


@pytest.mark.asyncio
async def test_audit_scope_lead_auditor_and_deletion(env, w):
    c = env["client"]
    await env["grant"](TA, "aud-b", R.AUDITOR, ScopeType.UNIT, "unit-b")
    w["aud-b"] = _h(TA, "aud-b")
    a = (await c.post(AU, json=_audit(), headers=w["auditor"])).json()
    org_wide = (await c.post(AU, json=_audit(organizational_unit_id=None, title="Trasversale", lead_auditor_user_id="aud-b"), headers=w["auditor"])).json()
    assert (await c.post(AU, json=_audit(organizational_unit_id=None), headers=w["ref-a"])).status_code == 403
    assert {x["id"] for x in (await c.get(AU, headers=w["mgr"])).json()} == {a["id"], org_wide["id"]}
    assert {x["id"] for x in (await c.get(AU, headers=w["aud-b"])).json()} == {org_wide["id"]}              # solo quello di cui è responsabile
    assert (await c.get(f"{AU}/{a['id']}", headers=w["aud-b"])).status_code == 403
    assert (await c.get(f"{AU}/{a['id']}", headers=w["b"])).status_code == 404
    assert (await c.get(AU, headers=w["reader"])).status_code == 403
    assert (await c.patch(f"{AU}/{a['id']}", json=_audit(), headers=w["auditor"])).status_code == 428
    assert (await c.delete(f"{AU}/{a['id']}", headers=w["auditor"])).status_code == 403
    assert (await c.delete(f"{AU}/{a['id']}", headers=w["dpo"])).status_code == 204
