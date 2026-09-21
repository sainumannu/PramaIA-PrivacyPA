"""Riferimenti normativi (§13) e azioni correttive (§29)."""
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update

from backend.models.privacypa_compliance import CorrectiveAction
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
LR = f"{API}/legal-references"
CA = f"{API}/corrective-actions"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("reader", R.READ_ONLY, ScopeType.TENANT, None), ("op", R.OPERATOR, ScopeType.TENANT, None),
        ("ict-a", R.ICT, ScopeType.UNIT, "unit-a"), ("ict-b", R.ICT, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "reader", "op", "ict-a", "ict-b")} | {"b": _h(TB, "b-mgr")}


# ---------------------------------------------------------------- riferimenti normativi

REF = {"source_type": "LEGISLATIVE_DECREE", "title": "D.Lgs.", "number": "196", "year": 2003, "article": "art. 2-ter"}


@pytest.mark.asyncio
async def test_legal_reference_crud_citation_and_validation(env, w):
    c = env["client"]
    created = await c.post(LR, json=REF, headers=w["mgr"])
    assert created.status_code == 201, created.text
    ref = created.json()
    assert ref["citation"] == "D.Lgs. 196/2003, art. 2-ter" and ref["row_version"] == 1 and created.headers["etag"] == '"1"'

    assert (await c.post(LR, json=REF, headers=w["mgr"])).status_code == 400            # duplicato
    assert (await c.post(LR, json={**REF, "year": 1500}, headers=w["mgr"])).status_code == 400
    assert (await c.post(LR, json={**REF, "number": "1", "url": "javascript:x"}, headers=w["mgr"])).status_code == 400
    assert (await c.post(LR, json={**REF, "number": "2"}, headers=w["reader"])).status_code == 403
    assert (await c.get(LR, params={"search": "196"}, headers=w["reader"])).json()[0]["id"] == ref["id"]
    assert (await c.get(f"{LR}/{ref['id']}", headers=w["b"])).status_code == 404 and (await c.get(LR, headers=w["b"])).json() == []

    body = {**REF, "article": "art. 2-sexies", "url": "https://www.normattiva.it/"}
    assert (await c.patch(f"{LR}/{ref['id']}", json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(f"{LR}/{ref['id']}", json=body, headers=ifm(9, w["mgr"]))).status_code == 409
    updated = await c.patch(f"{LR}/{ref['id']}", json=body, headers=ifm(1, w["mgr"]))
    assert updated.status_code == 200 and updated.json()["citation"].endswith("art. 2-sexies")
    assert (await c.delete(f"{LR}/{ref['id']}", headers=w["op"])).status_code == 403    # solo ruoli di governo
    assert (await c.delete(f"{LR}/{ref['id']}", headers=w["dpo"])).status_code == 204


@pytest.mark.asyncio
async def test_legal_references_link_to_processing_retention_and_report(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    ref = (await c.post(LR, json=REF, headers=w["mgr"])).json()
    url = f"{API}/processing-activities/{pid}"

    assert (await c.put(f"{url}/relations", json={"legal_references": ["nessuno"]}, headers=ifm(await version_of(c, url), w["mgr"]))).status_code == 400
    ok = await c.put(f"{url}/relations", json={"legal_references": [ref["id"]]}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert ok.status_code == 200 and [r["citation"] for r in ok.json()["legal_references"]] == ["D.Lgs. 196/2003, art. 2-ter"]
    events = (await c.get(f"{API}/audit", params={"entity_id": pid}, headers=ADMIN)).json()
    assert any(e["event_type"] == "processing_activity.relations.updated" and "legal_references" in e["new_values"] for e in events)

    # un riferimento in uso non si elimina
    blocked = await c.delete(f"{LR}/{ref['id']}", headers=w["dpo"])
    assert blocked.status_code == 400 and "usato" in blocked.json()["detail"]

    # regola di conservazione con riferimento strutturato
    rule = {"retention_value": 5, "retention_unit": "YEARS", "retention_criterion": "Prescrizione", "disposal_method": "DELETION"}
    assert (await c.post(f"{url}/retention-rules", json={**rule, "legal_reference_id": "nessuno"}, headers=w["mgr"])).status_code == 400
    created = await c.post(f"{url}/retention-rules", json={**rule, "legal_reference_id": ref["id"]}, headers=w["mgr"])
    assert created.status_code == 201 and created.json()["legal_reference_id"] == ref["id"]
    report = (await c.get(f"{API}/reports/art30", headers=ADMIN)).json()
    record = next(r for r in report["processing_activities"] if r["id"] == pid)
    assert record["legal_references_resolved"] == ["D.Lgs. 196/2003, art. 2-ter"]
    assert record["retention_resolved"][0]["legal_reference"] == "D.Lgs. 196/2003, art. 2-ter"

    await c.delete(f"{API}/retention-rules/{created.json()['id']}", headers=w["mgr"])
    await c.put(f"{url}/relations", json={"legal_references": []}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert (await c.delete(f"{LR}/{ref['id']}", headers=w["dpo"])).status_code == 204


# ---------------------------------------------------------------- azioni correttive

def _action(**override):
    return {"source_type": "MANUAL", "title": "Aggiornare la procedura", "description": "Rivedere la procedura di accesso",
            "priority": "HIGH", "owner_user_id": "resp-1", "due_date": iso(30), **override}


@pytest.mark.asyncio
async def test_action_lifecycle_and_validation(env, w):
    c = env["client"]
    created = await c.post(CA, json=_action(organizational_unit_id="unit-a"), headers=w["mgr"])
    assert created.status_code == 201, created.text
    a = created.json()
    assert a["code"] == "CA-000001" and a["status"] == "OPEN" and a["row_version"] == 1

    assert (await c.post(CA, json=_action(due_date=iso(-1)), headers=w["mgr"])).status_code == 400
    assert (await c.post(CA, json=_action(source_type="AUDIT"), headers=w["mgr"])).status_code == 400          # serve l'origine
    assert (await c.post(CA, json=_action(source_type="AUDIT", source_id="nope"), headers=w["mgr"])).status_code == 400
    assert (await c.post(CA, json=_action(source_type="MANUAL", source_id="x"), headers=w["mgr"])).status_code == 400
    assert (await c.post(CA, json=_action(evidence_document_id="nope"), headers=w["mgr"])).status_code == 400
    assert (await c.post(CA, json=_action(), headers=w["reader"])).status_code == 403

    url = f"{CA}/{a['id']}/status"
    assert (await c.post(url, json={"status": "COMPLETED"}, headers=ifm(1, w["mgr"]))).status_code == 400          # serve la nota
    assert (await c.post(url, json={"status": "IN_PROGRESS"}, headers=w["mgr"])).status_code == 428
    started = await c.post(url, json={"status": "IN_PROGRESS"}, headers=ifm(1, w["mgr"]))
    assert started.status_code == 200 and started.json()["row_version"] == 2
    done = await c.post(url, json={"status": "COMPLETED", "notes": "Procedura aggiornata e diffusa"}, headers=ifm(2, w["mgr"]))
    assert done.status_code == 200 and done.json()["closed_at"] and done.json()["closure_notes"].startswith("Procedura")
    assert (await c.patch(f"{CA}/{a['id']}", json=_action(), headers=ifm(3, w["mgr"]))).status_code == 400          # chiusa
    reopened = await c.post(url, json={"status": "IN_PROGRESS"}, headers=ifm(3, w["mgr"]))
    assert reopened.json()["closed_at"] is None
    assert (await c.post(url, json={"status": "CANCELLED", "notes": "Non più necessaria"}, headers=ifm(4, w["mgr"]))).status_code == 200
    assert (await c.post(url, json={"status": "COMPLETED", "notes": "x"}, headers=ifm(5, w["mgr"]))).status_code == 400  # non ammessa
    assert (await c.delete(f"{CA}/{a['id']}", headers=w["op"])).status_code == 403
    assert (await c.delete(f"{CA}/{a['id']}", headers=w["dpo"])).status_code == 204


@pytest.mark.asyncio
async def test_action_visibility_owner_unit_and_tenant(env, w):
    c = env["client"]
    in_a = (await c.post(CA, json=_action(organizational_unit_id="unit-a", owner_user_id="ict-b"), headers=w["mgr"])).json()
    org_wide = (await c.post(CA, json=_action(title="Di ente"), headers=w["mgr"])).json()

    assert {x["id"] for x in (await c.get(CA, headers=w["ict-a"])).json()} == {in_a["id"]}               # solo la propria struttura
    # chi è responsabile dell'azione la vede anche fuori scope
    assert {x["id"] for x in (await c.get(CA, headers=w["ict-b"])).json()} == {in_a["id"]}
    assert (await c.get(f"{CA}/{org_wide['id']}", headers=w["ict-a"])).status_code == 403
    assert (await c.get(f"{CA}/{in_a['id']}", headers=w["b"])).status_code == 404
    assert [x["id"] for x in (await c.get(CA, params={"mine": "true"}, headers=w["ict-b"])).json()] == [in_a["id"]]
    assert (await c.post(CA, json=_action(), headers=w["ict-a"])).status_code == 403                    # senza struttura serve l'intero ente
    assert (await c.post(CA, json=_action(organizational_unit_id="unit-b"), headers=w["ict-a"])).status_code == 403


@pytest.mark.asyncio
async def test_overdue_action_generates_a_finding_scoped_by_unit(env, w):
    c = env["client"]
    a = (await c.post(CA, json=_action(organizational_unit_id="unit-a", due_date=iso(0)), headers=w["mgr"])).json()
    async with env["maker"]() as db:
        await db.execute(update(CorrectiveAction).where(CorrectiveAction.id == a["id"]).values(due_date=TODAY - timedelta(days=4)))
        await db.commit()
    found = (await c.get(f"{API}/findings", params={"code": "CORRECTIVE_ACTION_OVERDUE"}, headers=w["mgr"])).json()
    assert [(f["entity_id"], f["severity"], f["organizational_unit_id"]) for f in found] == [(a["id"], "HIGH", "unit-a")]
    assert len((await c.get(f"{API}/findings", params={"code": "CORRECTIVE_ACTION_OVERDUE"}, headers=w["ict-a"])).json()) == 1
    assert (await c.get(f"{API}/findings", params={"code": "CORRECTIVE_ACTION_OVERDUE"}, headers=w["ict-b"])).json() == []

    await c.post(f"{CA}/{a['id']}/status", json={"status": "COMPLETED", "notes": "Fatto"}, headers=ifm(a["row_version"], w["mgr"]))
    assert (await c.get(f"{API}/findings", params={"code": "CORRECTIVE_ACTION_OVERDUE"}, headers=w["mgr"])).json() == []
