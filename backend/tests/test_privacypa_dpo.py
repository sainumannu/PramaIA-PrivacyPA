"""Pareri del DPO e DPO workspace (§43), relazione annuale (§42)."""
import io
import json
import zipfile
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio

from sqlalchemy import select

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_people import DpoOpinion
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
OP, WS, RP, BR = f"{API}/dpo/opinions", f"{API}/dpo/workspace", f"{API}/reports/annual", f"{API}/breaches"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()
YEAR = TODAY.year
SECTIONS = ("registry", "reviews", "dpia", "requests", "breaches", "audits", "suppliers", "training", "actions",
            "criticalities", "dpo_activity", "next_year_plan")


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("dpo-b", R.DPO, ScopeType.UNIT, "unit-b"), ("ict", R.ICT, ScopeType.TENANT, None),
        ("op", R.OPERATOR, ScopeType.TENANT, None), ("aud", R.AUDITOR, ScopeType.TENANT, None),
        ("ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a"), ("ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-dpo", R.DPO)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "dpo-b", "ict", "op", "aud", "ref-a", "ref-b")} | {"b": _h(TB, "b-dpo")}


def _breach(unit="unit-a", **override):
    aware = (datetime.utcnow() - timedelta(hours=5)).replace(microsecond=0).isoformat()
    return {"title": "Invio errato", "organizational_unit_id": unit, "aware_at": aware,
            "description": "Testo riservato della violazione", "confidentiality_affected": True, **override}


async def _request_opinion(env, who, entity_type, entity_id, notes=None):
    return await env["client"].post(OP, json={"entity_type": entity_type, "entity_id": entity_id, "notes": notes}, headers=who)


# ================================================================ pareri

@pytest.mark.asyncio
async def test_opinion_request_validation_and_scope(env, w):
    c, ids = env["client"], env["ids"]
    r = await _request_opinion(env, w["ref-a"], "processing_activity", ids["proc_a"], "Serve un parere sulla base giuridica")
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "REQUESTED" and o["entity_code"] and o["organizational_unit_id"] == "unit-a" and o["requested_by"] == "ref-a"
    assert o["opinion_text"] is None and o["issued_at"] is None

    assert (await _request_opinion(env, w["ref-a"], "processing_activity", ids["proc_a"])).status_code == 400     # già in attesa
    assert (await _request_opinion(env, w["ref-a"], "asset", ids["proc_a"])).status_code == 400
    assert (await _request_opinion(env, w["ref-a"], "processing_activity", "nessuno")).status_code == 404
    assert (await _request_opinion(env, w["ref-a"], "processing_activity", ids["proc_b"])).status_code == 403      # altra struttura
    assert (await _request_opinion(env, w["ref-a"], "processing_activity", ids["proc_x"])).status_code == 404      # altro ente
    assert (await _request_opinion(env, w["aud"], "processing_activity", ids["proc_b"])).status_code == 403        # sola lettura

    # visibilità: struttura del record, oppure chi l'ha richiesto
    assert [x["id"] for x in (await c.get(OP, headers=w["ref-a"])).json()] == [o["id"]]
    assert (await c.get(OP, headers=w["ref-b"])).json() == []
    assert (await c.get(f"{OP}/{o['id']}", headers=w["ref-b"])).status_code == 403
    assert (await c.get(f"{OP}/{o['id']}", headers=w["b"])).status_code == 404
    assert len((await c.get(OP, params={"entity_id": ids["proc_a"], "status": "REQUESTED"}, headers=w["dpo"])).json()) == 1
    assert (await c.get(OP, params={"status": "ISSUED"}, headers=w["dpo"])).json() == []


@pytest.mark.asyncio
async def test_only_the_assigned_dpo_issues_and_an_issued_opinion_is_immutable(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    o = (await _request_opinion(env, w["ref-a"], "processing_activity", pid)).json()
    url = f"{OP}/{o['id']}/issue"
    text = {"text": "Parere favorevole con riserva sulla conservazione"}

    assert (await c.post(url, json=text, headers=w["dpo"])).status_code == 428
    assert (await c.post(url, json=text, headers=ifm(o["row_version"] + 2, w["dpo"]))).status_code == 409
    for who in ("mgr", "ref-a", "op", "aud"):                                       # senza il permesso del DPO
        assert (await c.post(url, json=text, headers=ifm(o["row_version"], w[who]))).status_code == 403
    # neppure un amministratore lo emette al posto del DPO; né un DPO di un'altra struttura
    assert (await c.post(url, json=text, headers=ifm(o["row_version"], ADMIN))).status_code == 403
    assert (await c.post(url, json=text, headers=ifm(o["row_version"], w["dpo-b"]))).status_code == 403
    assert (await c.post(url, json=text, headers=ifm(o["row_version"], w["b"]))).status_code == 404
    assert (await c.post(url, json={"text": ""}, headers=ifm(o["row_version"], w["dpo"]))).status_code == 422
    assert (await c.post(url, json={"text": "   "}, headers=ifm(o["row_version"], w["dpo"]))).status_code == 400

    issued = await c.post(url, json=text, headers=ifm(o["row_version"], w["dpo"]))
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["status"] == "ISSUED" and body["dpo_user_id"] == "dpo" and body["issued_at"] and body["opinion_text"] == text["text"]

    # immutabile: né riemesso né ritirato; una nuova richiesta è un nuovo record
    assert (await c.post(url, json={"text": "Altro"}, headers=ifm(body["row_version"], w["dpo"]))).status_code == 400
    assert (await c.post(f"{OP}/{o['id']}/withdraw", headers=ifm(body["row_version"], w["ref-a"]))).status_code == 400
    assert (await _request_opinion(env, w["ref-a"], "processing_activity", pid)).status_code == 201
    assert len((await c.get(OP, params={"entity_id": pid}, headers=w["dpo"])).json()) == 2

    # il parere non modifica la decisione: il trattamento resta com'era
    assert (await c.get(f"{API}/processing-activities/{pid}", headers=w["dpo"])).json()["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_a_dpo_bound_to_a_unit_issues_only_there_and_requests_can_be_withdrawn(env, w):
    c, ids = env["client"], env["ids"]
    o = (await _request_opinion(env, w["ref-b"], "processing_activity", ids["proc_b"], "Verificare")).json()
    ok = await c.post(f"{OP}/{o['id']}/issue", json={"text": "Nessuna osservazione"}, headers=ifm(o["row_version"], w["dpo-b"]))
    assert ok.status_code == 200 and ok.json()["status"] == "ISSUED"

    second = (await _request_opinion(env, w["mgr"], "processing_activity", ids["proc_a"])).json()
    withdraw = f"{OP}/{second['id']}/withdraw"
    assert (await c.post(withdraw, headers=w["mgr"])).status_code == 428
    assert (await c.post(withdraw, headers=ifm(second["row_version"], w["ref-b"]))).status_code == 403
    done = await c.post(withdraw, headers=ifm(second["row_version"], w["mgr"]))
    assert done.status_code == 200 and done.json()["status"] == "WITHDRAWN"
    assert (await c.post(f"{OP}/{second['id']}/issue", json={"text": "x"}, headers=ifm(done.json()["row_version"], w["dpo"]))).status_code == 400
    assert (await _request_opinion(env, w["mgr"], "processing_activity", ids["proc_a"])).status_code == 201      # dopo il ritiro si può richiedere di nuovo


@pytest.mark.asyncio
async def test_opinion_on_a_breach_and_no_text_in_audit_or_outbox(env, w):
    c = env["client"]
    breach = (await c.post(BR, json=_breach(), headers=w["ict"])).json()
    o = await _request_opinion(env, w["ict"], "data_breach", breach["id"], "Nota della richiesta")
    assert o.status_code == 201 and o.json()["entity_code"] == breach["code"] and o.json()["organizational_unit_id"] == "unit-a"
    secret = "Valutazione riservata del DPO"
    assert (await c.post(f"{OP}/{o.json()['id']}/issue", json={"text": secret}, headers=ifm(o.json()["row_version"], w["dpo"]))).status_code == 200

    events = (await c.get(f"{API}/outbox", headers=ADMIN)).json()
    types = {e["event_type"] for e in events}
    assert {"dpo_opinion.requested", "dpo_opinion.issued"} <= types
    raw = json.dumps(events)
    assert secret not in raw and "Nota della richiesta" not in raw and "Testo riservato" not in raw
    async with env["maker"]() as db:
        logs = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "dpo_opinion"))).scalars().all()
    assert {log.action for log in logs} == {"request", "issue"}
    assert secret not in json.dumps([log.new_values for log in logs]) and "Nota della richiesta" not in json.dumps([log.new_values for log in logs])


@pytest.mark.asyncio
async def test_pending_opinion_finding(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    o = (await _request_opinion(env, w["ref-a"], "processing_activity", pid)).json()
    r = await c.get(f"{API}/findings", params={"code": "DPO_OPINION_PENDING"}, headers=w["mgr"])
    assert r.status_code == 200 and r.json() == []                                # appena richiesto: nessuna segnalazione
    async with env["maker"]() as db:
        row = await db.get(DpoOpinion, o["id"])
        row.requested_at = datetime.utcnow() - timedelta(days=20)
        await db.commit()
    found = (await c.get(f"{API}/findings", params={"code": "DPO_OPINION_PENDING"}, headers=w["mgr"])).json()
    assert len(found) == 1 and found[0]["entity_type"] == "dpo_opinion" and found[0]["severity"] == "LOW"
    assert (await c.get(f"{API}/findings", params={"code": "DPO_OPINION_PENDING"}, headers=w["ref-b"])).json() == []
    await c.post(f"{OP}/{o['id']}/issue", json={"text": "Ok"}, headers=ifm(await version_of(c, f"{OP}/{o['id']}"), w["dpo"]))
    assert (await c.get(f"{API}/findings", params={"code": "DPO_OPINION_PENDING"}, headers=w["mgr"])).json() == []


# ================================================================ workspace

async def _seed_workspace(env, w):
    c, ids = env["client"], env["ids"]
    v = await version_of(c, f"{API}/processing-activities/{ids['proc_a']}")
    review = await c.post(f"{API}/processing-activities/{ids['proc_a']}/status", json={"status": "IN_REVIEW"}, headers=ifm(v, w["mgr"]))
    assert review.status_code == 200, review.text
    a = (await c.post(BR, json=_breach("unit-a"), headers=w["ict"])).json()
    b = (await c.post(BR, json=_breach("unit-b", title="Perdita di un supporto"), headers=w["ict"])).json()
    assert (await _request_opinion(env, w["mgr"], "processing_activity", ids["proc_b"])).status_code == 201
    audit = await c.post(f"{API}/audits", json={"title": "Audit servizi", "audit_type": "INTERNAL", "organizational_unit_id": "unit-b",
                                                 "planned_date": iso(20), "lead_auditor_user_id": "aud"}, headers=w["mgr"])
    assert audit.status_code == 201, audit.text
    for unit, title in (("unit-a", "Raccomandazione A"), ("unit-b", "Raccomandazione B")):
        act = await c.post(f"{API}/corrective-actions", json={
            "source_type": "DPO_RECOMMENDATION", "title": title, "description": "Da fare", "owner_user_id": "resp",
            "organizational_unit_id": unit, "due_date": iso(15)}, headers=w["mgr"])
        assert act.status_code == 201, act.text
    return a, b


@pytest.mark.asyncio
async def test_workspace_collects_what_the_dpo_must_follow_and_respects_scope(env, w):
    c, ids = env["client"], env["ids"]
    a, b = await _seed_workspace(env, w)

    r = await c.get(WS, headers=w["dpo"])
    assert r.status_code == 200, r.text
    ws = r.json()
    assert {"processing_in_review", "dpias_to_review", "opinions_requested", "breaches_open", "audits", "recommended_actions",
            "followups", "calendar", "annual_report"} <= set(ws)
    assert [p["id"] for p in ws["processing_in_review"]] == [ids["proc_a"]]
    assert {x["id"] for x in ws["breaches_open"]} == {a["id"], b["id"]}
    assert all(x["hours_since_aware"] >= 4.9 for x in ws["breaches_open"])
    assert [x["entity_id"] for x in ws["opinions_requested"]] == [ids["proc_b"]]
    assert len(ws["audits"]) == 1 and len(ws["recommended_actions"]) == 2
    assert ws["followups"]["opinions_pending"] == 1 and ws["followups"]["actions_overdue"] == 0
    assert {"audit", "action_due"} <= {i["kind"] for i in ws["calendar"]}
    assert [i["date"] for i in ws["calendar"]] == sorted(i["date"] for i in ws["calendar"])
    assert ws["annual_report"] == {"year": YEAR, "status": None}

    # un DPO limitato a una struttura vede solo quella
    scoped = (await c.get(WS, headers=w["dpo-b"])).json()
    assert scoped["processing_in_review"] == [] and [x["id"] for x in scoped["breaches_open"]] == [b["id"]]
    assert len(scoped["recommended_actions"]) == 1 and scoped["recommended_actions"][0]["title"] == "Raccomandazione B"
    assert len(scoped["audits"]) == 1

    # chi non è del perimetro DPO/governance non vede il workspace; altro ente separato
    for who in ("ref-a", "op", "ict"):
        assert (await c.get(WS, headers=w[who])).status_code == 403
    other = (await c.get(WS, headers=w["b"])).json()
    assert other["breaches_open"] == [] and other["opinions_requested"] == [] and other["processing_in_review"] == []
    assert (await c.get(WS, headers=w["aud"])).status_code == 200


@pytest.mark.asyncio
async def test_workspace_shows_only_opinions_assigned_to_the_dpo_or_to_anyone(env, w):
    c, ids = env["client"], env["ids"]
    await c.post(OP, json={"entity_type": "processing_activity", "entity_id": ids["proc_a"], "dpo_user_id": "dpo"}, headers=w["mgr"])
    await c.post(OP, json={"entity_type": "processing_activity", "entity_id": ids["proc_b"], "dpo_user_id": "altro-dpo"}, headers=w["mgr"])
    mine = (await c.get(WS, headers=w["dpo"])).json()["opinions_requested"]
    assert [x["entity_id"] for x in mine] == [ids["proc_a"]] and mine[0]["assigned_to_me"] is True


# ================================================================ relazione annuale

@pytest.mark.asyncio
async def test_annual_report_is_tenant_wide_and_generates_all_twelve_sections(env, w):
    c = env["client"]
    for who in ("ref-a", "op", "ict", "dpo-b"):                       # senza permesso, o con scope limitato
        assert (await c.get(f"{RP}/{YEAR}", headers=w[who])).status_code == 403
    r = await c.get(f"{RP}/{YEAR}", headers=w["dpo"])
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["status"] == "DRAFT" and report["watermark"] == "bozza" and report["row_version"] == 0 and report["comments"] == {}
    assert r.headers["ETag"] == '"0"'
    assert set(report["data"]) >= set(SECTIONS)
    assert report["data"]["registry"]["total"] == 2 and report["data"]["registry"]["by_status"] == {"DRAFT": 2}
    assert report["data"]["registry"]["live_without_retention"] == 0
    assert (await c.get(f"{RP}/1990", headers=w["dpo"])).status_code == 400
    assert (await c.get(f"{RP}/{YEAR + 5}", headers=w["dpo"])).status_code == 400
    assert (await c.get(f"{RP}/{YEAR}", headers=w["b"])).json()["data"]["registry"]["total"] == 1


@pytest.mark.asyncio
async def test_annual_report_statistics_reflect_the_year(env, w):
    c, ids = env["client"], env["ids"]
    b1 = (await c.post(BR, json=_breach(), headers=w["ict"])).json()
    await c.post(BR, json=_breach("unit-b", title="Seconda"), headers=w["ict"])
    v = b1["row_version"]
    notified = await c.patch(f"{BR}/{b1['id']}", json=_breach(
        risk_level="HIGH", risk_rationale="Dati di contatto", notify_authority=True, notify_authority_rationale="Rischio elevato",
        authority_notified_at=datetime.utcnow().replace(microsecond=0).isoformat()), headers=ifm(v, w["mgr"]))
    assert notified.status_code == 200, notified.text
    await c.post(f"{API}/requests", json={"organizational_unit_id": "unit-a", "request_type": "ACCESS", "channel": "PEC",
                                          "received_at": datetime.utcnow().replace(microsecond=0).isoformat()}, headers=w["mgr"])
    await _request_opinion(env, w["mgr"], "processing_activity", ids["proc_a"])
    course = (await c.post(f"{API}/training/courses", json={"title": "Base", "version": "1", "mandatory": True}, headers=w["mgr"])).json()
    await c.post(f"{API}/training/courses/{course['id']}/assign", json={"user_ids": ["op", "ict"], "due_date": iso(30)}, headers=w["mgr"])

    data = (await c.get(f"{RP}/{YEAR}", headers=w["mgr"])).json()["data"]
    assert data["breaches"]["registered"] == 2 and data["breaches"]["notified_to_authority"] == 1
    assert data["breaches"]["without_decision"] == 1 and data["breaches"]["by_risk"] == {"HIGH": 1}
    assert data["requests"]["received"] == 1 and data["requests"]["by_type"] == {"ACCESS": 1} and data["requests"]["responded"] == 0
    assert data["dpo_activity"]["opinions_requested"] == 1 and data["dpo_activity"]["opinions_pending"] == 1
    assert data["training"]["courses"] == 1 and data["training"]["mandatory_assignments"] == 2 and data["training"]["completed"] == 0
    assert data["training"]["completion_rate"] == 0.0
    # un anno passato non conta ciò che è avvenuto oggi
    past = (await c.get(f"{RP}/{YEAR - 1}", headers=w["mgr"])).json()["data"]
    assert past["breaches"]["registered"] == 0 and past["requests"]["received"] == 0 and past["dpo_activity"]["opinions_requested"] == 0


@pytest.mark.asyncio
async def test_comments_are_versioned_validated_and_saved_per_section(env, w):
    c = env["client"]
    url = f"{RP}/{YEAR}/comments"
    body = {"comments": {"registry": "Registro completo al 60%", "conclusions": "Si conferma il piano", "breaches": "  "}}
    assert (await c.put(url, json=body, headers=w["dpo"])).status_code == 428
    assert (await c.put(url, json=body, headers=ifm(3, w["dpo"]))).status_code == 409
    assert (await c.put(url, json={"comments": {"inesistente": "x"}}, headers=ifm(0, w["dpo"]))).status_code == 400
    assert (await c.put(url, json={"comments": {"registry": "x" * 8001}}, headers=ifm(0, w["dpo"]))).status_code == 400
    for who in ("aud", "ref-a", "op"):
        assert (await c.put(url, json=body, headers=ifm(0, w[who]))).status_code == 403
    assert (await c.put(url, json=body, headers=ifm(0, w["dpo-b"]))).status_code == 403                   # non ha visibilità su tutto l'ente

    saved = await c.put(url, json=body, headers=ifm(0, w["dpo"]))
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view["comments"] == {"registry": "Registro completo al 60%", "conclusions": "Si conferma il piano"}
    assert view["status"] == "DRAFT" and view["watermark"] == "bozza" and view["row_version"] == 1
    assert (await c.put(url, json=body, headers=ifm(0, w["dpo"]))).status_code == 409                      # versione stantia
    again = await c.put(url, json={"comments": {"registry": "Aggiornato"}}, headers=ifm(1, w["mgr"]))
    assert again.status_code == 200 and again.json()["comments"] == {"registry": "Aggiornato"} and again.json()["row_version"] == 2
    assert (await c.get(f"{RP}/{YEAR}", headers=w["aud"])).json()["comments"] == {"registry": "Aggiornato"}      # sola lettura
    assert (await c.get(f"{RP}/{YEAR}", headers=w["b"])).json()["comments"] == {}


@pytest.mark.asyncio
async def test_finalize_freezes_data_and_reopen_restores_the_draft(env, w):
    c, ids = env["client"], env["ids"]
    await c.put(f"{RP}/{YEAR}/comments", json={"comments": {"conclusions": "Conclusioni"}}, headers=ifm(0, w["dpo"]))
    fin = f"{RP}/{YEAR}/finalize"
    assert (await c.post(fin, headers=w["dpo"])).status_code == 428
    assert (await c.post(fin, headers=ifm(0, w["dpo"]))).status_code == 409
    assert (await c.post(fin, headers=ifm(1, w["op"]))).status_code == 403
    assert (await c.post(fin, headers=ifm(1, w["aud"]))).status_code == 403

    done = await c.post(fin, headers=ifm(1, w["dpo"]))
    assert done.status_code == 200, done.text
    final = done.json()
    assert final["status"] == "FINAL" and final["watermark"] is None and final["finalized_by"] == "dpo" and final["finalized_at"]
    assert final["data"]["breaches"]["registered"] == 0
    assert (await c.post(fin, headers=ifm(final["row_version"], w["dpo"]))).status_code == 400            # già finalizzata

    # dopo la finalizzazione i dati non cambiano più e i commenti sono bloccati
    assert (await c.post(BR, json=_breach(), headers=w["ict"])).status_code == 201
    frozen = (await c.get(f"{RP}/{YEAR}", headers=w["dpo"])).json()
    assert frozen["data"]["breaches"]["registered"] == 0 and frozen["data"] == final["data"]
    assert (await c.put(f"{RP}/{YEAR}/comments", json={"comments": {"registry": "x"}}, headers=ifm(final["row_version"], w["dpo"]))).status_code == 400

    reopen = f"{RP}/{YEAR}/reopen"
    assert (await c.post(reopen, headers=ifm(final["row_version"], w["op"]))).status_code == 403
    assert (await c.post(reopen, headers=ifm(final["row_version"] + 4, w["dpo"]))).status_code == 409
    reopened = await c.post(reopen, headers=ifm(final["row_version"], w["dpo"]))
    assert reopened.status_code == 200
    live = reopened.json()
    assert live["status"] == "DRAFT" and live["watermark"] == "bozza" and live["finalized_at"] is None
    assert live["data"]["breaches"]["registered"] == 1 and live["comments"] == {"conclusions": "Conclusioni"}
    assert (await c.post(reopen, headers=ifm(live["row_version"], w["dpo"]))).status_code == 400          # non è finalizzata

    events = {e["event_type"] for e in (await c.get(f"{API}/outbox", headers=ADMIN)).json()}
    assert {"annual_report.comments.updated", "annual_report.finalized", "annual_report.reopened"} <= events


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("word/document.xml").decode("utf-8")


@pytest.mark.asyncio
async def test_export_as_word_document_with_draft_watermark(env, w):
    c = env["client"]
    await c.put(f"{RP}/{YEAR}/comments", json={"comments": {"registry": "Commento sul registro", "conclusions": "Conclusioni finali"}}, headers=ifm(0, w["dpo"]))
    r = await c.get(f"{RP}/{YEAR}/export", headers=w["dpo"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert f"relazione-annuale-{YEAR}.docx" in r.headers["content-disposition"]
    xml = _docx_text(r.content)
    for text in ("BOZZA", "Stato del registro", "Piano dell", "Commento sul registro", "Conclusioni finali"):
        assert text in xml
    assert "Finalizzata il" not in xml

    await c.post(f"{RP}/{YEAR}/finalize", headers=ifm(1, w["dpo"]))
    final = _docx_text((await c.get(f"{RP}/{YEAR}/export", headers=w["dpo"])).content)
    assert "BOZZA" not in final and "Finalizzata il" in final
    assert (await c.get(f"{RP}/{YEAR}/export", headers=w["op"])).status_code == 403
    assert (await c.get(f"{RP}/{YEAR}/export", headers=w["dpo-b"])).status_code == 403
