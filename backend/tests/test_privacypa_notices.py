"""Informative (§25): versioni, approvazione, collegamenti, confronto e regola NOTICE_REVIEW_REQUIRED (§25.1)."""
import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio

from backend.models.privacypa_documents import Document
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
NOTICES = f"{API}/notices"
APPR = f"{API}/approvals"
FINDINGS = f"{API}/findings"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("op", R.OPERATOR, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("op-a", R.OPERATOR, ScopeType.UNIT, "unit-a"), ("op-b", R.OPERATOR, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "op", "reader", "op-a", "op-b")} | {"b": _h(TB, "b-mgr")}


async def _create(env, who, **override):
    body = {"title": "Informativa videosorveglianza", "audience": "Cittadini", "owner_unit_id": "unit-a",
            "content": "Il Comune tratta i dati per finalità di sicurezza urbana.", "effective_from": iso(-10), **override}
    return await env["client"].post(NOTICES, json=body, headers=who)


async def _get(env, notice_id, who=ADMIN):
    r = await env["client"].get(f"{NOTICES}/{notice_id}", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def _status(env, notice_id, target, who, notes=None):
    v = (await _get(env, notice_id))["row_version"]
    return await env["client"].post(f"{NOTICES}/{notice_id}/status", json={"status": target, "notes": notes}, headers=ifm(v, who))


async def _approve(env, w, notice_id):
    assert (await _status(env, notice_id, "IN_REVIEW", w["op"])).status_code == 200
    r = await _status(env, notice_id, "APPROVED", w["dpo"])
    assert r.status_code == 200, r.text
    return r.json()


async def _new_version(env, notice_id, who, **body):
    v = (await _get(env, notice_id))["row_version"]
    return await env["client"].post(f"{NOTICES}/{notice_id}/versions", json={"version_label": "2.0", **body}, headers=ifm(v, who))


async def _link(env, notice_id, ids, who):
    v = (await _get(env, notice_id))["row_version"]
    return await env["client"].put(f"{NOTICES}/{notice_id}/processing-links", json={"processing_activity_ids": ids}, headers=ifm(v, who))


# ---------------------------------------------------------------- creazione e validazione

@pytest.mark.asyncio
async def test_create_notice_with_first_version_and_scope(env, w):
    created = await _create(env, w["op-a"], version_label="1.0")
    assert created.status_code == 201, created.text
    n = created.json()
    assert n["code"] == "INF-000001" and n["status"] == "DRAFT" and n["row_version"] == 1 and created.headers["etag"] == '"1"'
    assert len(n["versions"]) == 1 and n["versions"][0]["version_label"] == "1.0" and n["working_version_id"] == n["versions"][0]["id"]
    assert n["current_version"] is None
    assert (await _create(env, w["mgr"])).json()["code"] == "INF-000002"

    assert (await _create(env, w["op-b"])).status_code == 403                 # struttura diversa
    assert (await _create(env, w["reader"])).status_code == 403
    assert (await _create(env, w["mgr"], owner_unit_id="unit-x")).status_code == 400   # struttura di un altro ente
    # visibilità per struttura proprietaria e per ente
    assert [x["id"] for x in (await env["client"].get(NOTICES, headers=w["op-a"])).json()] == [n["id"], (await env["client"].get(NOTICES, headers=w["mgr"])).json()[1]["id"]]
    assert [x["code"] for x in (await env["client"].get(NOTICES, headers=w["op-b"])).json()] == []
    assert (await env["client"].get(f"{NOTICES}/{n['id']}", headers=w["op-b"])).status_code == 403
    assert (await env["client"].get(f"{NOTICES}/{n['id']}", headers=w["b"])).status_code == 404
    assert (await env["client"].get(NOTICES, headers=w["b"])).json() == []


@pytest.mark.asyncio
async def test_version_field_validation(env, w):
    assert (await _create(env, w["mgr"], published_url="ftp://esempio.it/informativa")).status_code == 400
    assert (await _create(env, w["mgr"], published_url="javascript:alert(1)")).status_code == 400
    assert (await _create(env, w["mgr"], document_id="non-esiste")).status_code == 400
    ok = await _create(env, w["mgr"], published_url="https://comune.example/privacy/videosorveglianza")
    assert ok.status_code == 201 and ok.json()["versions"][0]["published_url"].startswith("https://")
    async with env["maker"]() as db:
        doc = Document(tenant_id=TA, code="DOC-000001", title="Pdf informativa")
        db.add(doc)
        await db.commit()
        doc_id = doc.id
    linked = await _create(env, w["mgr"], document_id=doc_id)
    assert linked.status_code == 201 and linked.json()["versions"][0]["document_id"] == doc_id


# ---------------------------------------------------------------- approvazione e versioni

@pytest.mark.asyncio
async def test_submission_requires_a_complete_version_and_creates_an_approval(env, w):
    n = (await _create(env, w["mgr"], content=None, effective_from=None)).json()
    incomplete = await _status(env, n["id"], "IN_REVIEW", w["op"])
    assert incomplete.status_code == 422 and "testo" in incomplete.json()["detail"] and "decorrenza" in incomplete.json()["detail"]

    vid = n["working_version_id"]
    v = (await _get(env, n["id"]))["row_version"]
    fill = await env["client"].patch(f"{NOTICES}/{n['id']}/versions/{vid}", json={"content": "Testo", "effective_from": iso(0)}, headers=ifm(v, w["op"]))
    assert fill.status_code == 200 and fill.json()["row_version"] == v + 1
    assert (await _status(env, n["id"], "IN_REVIEW", w["op"], "pronta")).status_code == 200

    pending = (await env["client"].get(APPR, params={"state": "PENDING"}, headers=w["dpo"])).json()
    assert [(a["entity_type"], a["entity_id"], a["organizational_unit_id"], a["can_decide"]) for a in pending] == [("privacy_notice", n["id"], "unit-a", True)]

    # in approvazione non si modifica; un cambio dopo la richiesta la rende stantia dopo il ritorno in bozza
    v = (await _get(env, n["id"]))["row_version"]
    blocked = await env["client"].patch(f"{NOTICES}/{n['id']}/versions/{vid}", json={"content": "Altro"}, headers=ifm(v, w["op"]))
    assert blocked.status_code == 400 and "in approvazione" in blocked.json()["detail"]
    assert (await _status(env, n["id"], "DRAFT", w["op"])).status_code == 200      # ritiro della richiesta
    assert (await env["client"].get(APPR, params={"state": "PENDING"}, headers=w["dpo"])).json() == []


@pytest.mark.asyncio
async def test_approval_makes_the_version_current_and_immutable(env, w):
    n = (await _create(env, w["mgr"])).json()
    assert (await _status(env, n["id"], "APPROVED", w["dpo"])).status_code == 400            # da DRAFT non si approva
    assert (await _status(env, n["id"], "IN_REVIEW", w["op"])).status_code == 200
    assert (await _status(env, n["id"], "APPROVED", w["op"])).status_code == 403             # AC-REG-003: serve il permesso
    approved = await _status(env, n["id"], "APPROVED", w["dpo"], "ok")
    body = approved.json()
    version = body["versions"][0]
    assert body["status"] == "APPROVED" and body["current_version_id"] == version["id"] and body["working_version_id"] is None
    assert version["approved_by"] == "dpo" and version["approved_at"] and version["is_approved"] is True

    v = body["row_version"]
    frozen = await env["client"].patch(f"{NOTICES}/{n['id']}/versions/{version['id']}", json={"content": "Cambiato"}, headers=ifm(v, w["mgr"]))
    assert frozen.status_code == 400 and "immutabile" in frozen.json()["detail"]
    assert (await _get(env, n["id"]))["versions"][0]["content"].startswith("Il Comune")
    decided = (await env["client"].get(APPR, params={"entity_id": n["id"]}, headers=w["dpo"])).json()[0]
    assert decided["decision"] == "APPROVED" and decided["approver_id"] == "dpo" and decided["entity_title"] == n["title"]


@pytest.mark.asyncio
async def test_new_version_keeps_the_previous_in_force_until_approved(env, w):
    n = (await _create(env, w["mgr"], effective_from=iso(-100))).json()
    await _approve(env, w, n["id"])
    first_id = (await _get(env, n["id"]))["current_version_id"]

    assert (await _new_version(env, n["id"], w["reader"])).status_code == 403
    created = await _new_version(env, n["id"], w["mgr"], content="Il Comune tratta i dati per finalità di sicurezza urbana e tutela del patrimonio.", effective_from=iso(-1))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "DRAFT" and body["current_version_id"] == first_id and len(body["versions"]) == 2
    assert [v["version_no"] for v in body["versions"]] == [1, 2] and body["working_version_id"] == body["versions"][1]["id"]
    assert (await _new_version(env, n["id"], w["mgr"], version_label="3.0")).status_code == 400   # già una versione in lavorazione

    # copia del testo in vigore quando non se ne indica uno nuovo
    n2 = (await _create(env, w["mgr"], title="Altra")).json()
    await _approve(env, w, n2["id"])
    copied = (await _new_version(env, n2["id"], w["mgr"])).json()
    assert copied["versions"][1]["content"] == copied["versions"][0]["content"]

    # approvando la nuova, la precedente termina il giorno prima e la nuova diventa quella in vigore
    await _approve(env, w, n["id"])
    final = await _get(env, n["id"])
    first, second = final["versions"]
    assert final["current_version_id"] == second["id"] and first["effective_to"] == iso(-2) and second["effective_to"] is None


@pytest.mark.asyncio
async def test_word_diff_between_versions(env, w):
    n = (await _create(env, w["mgr"], content="uno due tre quattro")).json()
    await _approve(env, w, n["id"])
    await _new_version(env, n["id"], w["mgr"], content="uno due CINQUE quattro sei", effective_from=iso(0))
    versions = (await _get(env, n["id"]))["versions"]
    diff = (await env["client"].get(f"{NOTICES}/{n['id']}/versions/{versions[0]['id']}/diff/{versions[1]['id']}", headers=w["reader"])).json()
    ops = [(c["op"], c["text"].strip()) for c in diff["changes"] if c["text"].strip()]
    assert ("delete", "tre") in ops and ("insert", "CINQUE") in ops and ("insert", "sei") in ops and ("equal", "uno due") in ops
    assert diff["old"]["version_no"] == 1 and diff["new"]["version_no"] == 2 and "content" not in diff["old"]
    assert (await env["client"].get(f"{NOTICES}/{n['id']}/versions/{versions[0]['id']}/diff/nope", headers=w["reader"])).status_code == 400


@pytest.mark.asyncio
async def test_retire_delete_and_locking(env, w):
    c = env["client"]
    draft = (await _create(env, w["mgr"])).json()
    body = {"title": "Nuovo titolo", "audience": "Utenti", "owner_unit_id": "unit-a"}
    assert (await c.patch(f"{NOTICES}/{draft['id']}", json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(f"{NOTICES}/{draft['id']}", json=body, headers=ifm(9, w["mgr"]))).status_code == 409
    assert (await c.patch(f"{NOTICES}/{draft['id']}", json=body, headers=ifm(1, w["mgr"]))).json()["title"] == "Nuovo titolo"
    assert (await c.delete(f"{NOTICES}/{draft['id']}", headers=w["reader"])).status_code == 403
    assert (await c.delete(f"{NOTICES}/{draft['id']}", headers=w["mgr"])).status_code == 204
    assert (await c.get(f"{NOTICES}/{draft['id']}", headers=w["mgr"])).status_code == 404

    kept = (await _create(env, w["mgr"])).json()
    await _approve(env, w, kept["id"])
    assert (await c.delete(f"{NOTICES}/{kept['id']}", headers=w["mgr"])).status_code == 400   # già approvata: solo ritiro
    assert (await _status(env, kept["id"], "RETIRED", w["op"])).status_code == 403
    retired = (await _status(env, kept["id"], "RETIRED", w["dpo"])).json()
    assert retired["status"] == "RETIRED" and retired["current_version"]["effective_to"] == iso(0)
    assert (await _new_version(env, kept["id"], w["mgr"])).status_code == 400
    v = retired["row_version"]
    assert (await c.patch(f"{NOTICES}/{kept['id']}", json=body, headers=ifm(v, w["mgr"]))).status_code == 400
    assert (await _status(env, kept["id"], "APPROVED", w["dpo"])).status_code == 400


# ---------------------------------------------------------------- collegamenti ai trattamenti

@pytest.mark.asyncio
async def test_processing_links_scope_and_lookup(env, w):
    c, ids = env["client"], env["ids"]
    n = (await _create(env, w["op-a"])).json()
    v = n["row_version"]
    assert (await c.put(f"{NOTICES}/{n['id']}/processing-links", json={"processing_activity_ids": []}, headers=w["op-a"])).status_code == 428
    assert (await c.put(f"{NOTICES}/{n['id']}/processing-links", json={"processing_activity_ids": []}, headers=ifm(9, w["op-a"]))).status_code == 409
    assert (await _link(env, n["id"], [ids["proc_b"]], w["op-a"])).status_code == 403       # trattamento fuori dallo scope di scrittura
    assert (await _link(env, n["id"], ["non-esiste"], w["mgr"])).status_code == 400
    assert (await _link(env, n["id"], [ids["proc_x"]], w["mgr"])).status_code == 400          # trattamento di un altro ente
    ok = await _link(env, n["id"], [ids["proc_a"], ids["proc_a"]], w["op-a"])
    assert ok.status_code == 200 and ok.json()["processing_activity_ids"] == [ids["proc_a"]]

    assert [x["id"] for x in (await c.get(NOTICES, params={"processing_activity_id": ids["proc_a"]}, headers=w["mgr"])).json()] == [n["id"]]
    assert (await c.get(NOTICES, params={"processing_activity_id": ids["proc_b"]}, headers=w["mgr"])).json() == []
    processing = (await c.get(f"{API}/processing-activities/{ids['proc_a']}", headers=w["mgr"])).json()
    assert [x["code"] for x in processing["notices"]] == [n["code"]]
    assert (await _link(env, n["id"], [], w["reader"])).status_code == 403
    found = (await c.get(f"{API}/search", params={"q": "videosorveglianza"}, headers=w["op-a"])).json()
    assert [x["code"] for x in found["notices"]] == [n["code"]]
    assert (await c.get(f"{API}/search", params={"q": "videosorveglianza"}, headers=w["op-b"])).json()["notices"] == []


# ---------------------------------------------------------------- regola di coerenza (§25.1)

def _processing_body(**override):
    return {"title": "Trattamento A", "organizational_unit_id": "unit-a", "description": "Descrizione", "purposes": ["Sicurezza urbana"],
            "legal_basis_codes": ["GDPR_6_1_E"], "data_subject_ids": ["subject-1"], "data_category_ids": ["category-1"], **override}


async def _patch_processing(env, who, **override):
    url = f"{API}/processing-activities/{env['ids']['proc_a']}"
    r = await env["client"].patch(url, json=_processing_body(**override), headers=ifm(await version_of(env["client"], url), who))
    assert r.status_code == 200, r.text


async def _review_findings(env, who):
    r = await env["client"].get(FINDINGS, params={"code": "NOTICE_REVIEW_REQUIRED"}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


@pytest_asyncio.fixture
async def approved_notice(env, w):
    await _patch_processing(env, w["mgr"])                      # stato di partenza del trattamento
    n = (await _create(env, w["mgr"])).json()
    assert (await _link(env, n["id"], [env["ids"]["proc_a"]], w["mgr"])).status_code == 200
    await _approve(env, w, n["id"])
    await asyncio.sleep(0.02)
    return n["id"]


@pytest.mark.asyncio
async def test_only_relevant_changes_after_approval_require_a_review(env, w, approved_notice):
    assert await _review_findings(env, w["mgr"]) == []
    await _patch_processing(env, w["mgr"], title="Titolo diverso", description="Nuova descrizione")   # non rilevante
    assert await _review_findings(env, w["mgr"]) == []

    await _patch_processing(env, w["mgr"], purposes=["Sicurezza urbana", "Controllo accessi"])
    (finding,) = await _review_findings(env, w["mgr"])
    assert finding["entity_type"] == "privacy_notice" and finding["entity_id"] == approved_notice and finding["severity"] == "MEDIUM"
    assert finding["params"]["fields"] == "finalità" and finding["params"]["notice"] == "INF-000001"
    assert "informativa INF-000001" in finding["message"] and finding["processing_activity_id"] == env["ids"]["proc_a"]

    await _patch_processing(env, w["mgr"], purposes=["Sicurezza urbana", "Controllo accessi"], legal_basis_codes=["GDPR_6_1_C"])
    (finding,) = await _review_findings(env, w["mgr"])       # stessa segnalazione, aggiornata
    assert finding["params"]["fields"] == "base giuridica, finalità"
    # l'informativa non viene toccata
    assert (await _get(env, approved_notice))["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_relations_retention_and_controller_changes_are_detected(env, w, approved_notice):
    c, pid = env["client"], env["ids"]["proc_a"]
    party = (await c.post(f"{API}/parties", json={"legal_name": "Destinatario", "party_type": "RECIPIENT"}, headers=w["mgr"])).json()
    url = f"{API}/processing-activities/{pid}"
    r = await c.put(f"{url}/relations", json={"recipients": [{"party_id": party["id"]}]}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert r.status_code == 200
    (f,) = await _review_findings(env, w["mgr"])
    assert f["params"]["fields"] == "destinatari"

    # una modifica dei soli sistemi non riguarda l'informativa
    await c.put(f"{url}/relations", json={"assets": []}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert (await _review_findings(env, w["mgr"]))[0]["params"]["fields"] == "destinatari"

    rule = await c.post(f"{url}/retention-rules", json={"retention_value": 5, "retention_unit": "YEARS", "retention_criterion": "Prescrizione", "disposal_method": "DELETION"}, headers=w["mgr"])
    assert rule.status_code == 201
    assert (await _review_findings(env, w["mgr"]))[0]["params"]["fields"] == "conservazione, destinatari"

    await c.put(f"{API}/organization", json={"name": "Comune di Prova", "dpo_name": "Nuovo DPO", "dpo_contact": "nuovo@comune.example"}, headers=ADMIN)
    findings = await _review_findings(env, w["mgr"])
    assert len(findings) == 2 and any("DPO" in f["message"] for f in findings)


@pytest.mark.asyncio
async def test_confirming_the_review_or_approving_a_new_version_closes_the_finding(env, w, approved_notice):
    c = env["client"]
    await _patch_processing(env, w["mgr"], purposes=["Sicurezza urbana", "Controllo accessi"])
    (f,) = await _review_findings(env, w["mgr"])
    body = await _get(env, approved_notice)

    assert (await c.post(f"{NOTICES}/{approved_notice}/confirm-review", json={}, headers=w["mgr"])).status_code == 428
    assert (await c.post(f"{NOTICES}/{approved_notice}/confirm-review", json={}, headers=ifm(body["row_version"], w["reader"]))).status_code == 403
    done = await c.post(f"{NOTICES}/{approved_notice}/confirm-review", json={"notes": "Coerente: la nuova finalità è già descritta"}, headers=ifm(body["row_version"], w["mgr"]))
    assert done.status_code == 200 and done.json()["last_reviewed_by"] == "mgr" and done.json()["review_notes"].startswith("Coerente")
    assert await _review_findings(env, w["mgr"]) == []
    closed = (await c.get(FINDINGS, params={"code": "NOTICE_REVIEW_REQUIRED", "status": "RESOLVED"}, headers=w["mgr"])).json()
    assert [x["id"] for x in closed] == [f["id"]]

    # una nuova modifica dopo la verifica riapre la segnalazione; una nuova versione approvata la chiude
    await asyncio.sleep(0.02)
    await _patch_processing(env, w["mgr"], purposes=["Solo controllo accessi"])
    assert len(await _review_findings(env, w["mgr"])) == 1
    await _new_version(env, approved_notice, w["mgr"], content="Testo aggiornato", effective_from=iso(0))
    assert len(await _review_findings(env, w["mgr"])) == 1       # la vecchia versione è ancora quella in vigore
    await _approve(env, w, approved_notice)
    assert await _review_findings(env, w["mgr"]) == []

    again = await c.post(f"{NOTICES}/{approved_notice}/confirm-review", json={}, headers=ifm((await _get(env, approved_notice))["row_version"], w["mgr"]))
    assert again.status_code == 200
    n2 = (await _create(env, w["mgr"], title="Bozza")).json()
    assert (await c.post(f"{NOTICES}/{n2['id']}/confirm-review", json={}, headers=ifm(n2["row_version"], w["mgr"]))).status_code == 400


@pytest.mark.asyncio
async def test_review_findings_respect_scope_dashboard_notifications_and_outbox(env, w, approved_notice):
    c = env["client"]
    await _patch_processing(env, w["mgr"], legal_basis_codes=["GDPR_6_1_C"])
    assert len(await _review_findings(env, w["mgr"])) == 1
    assert len(await _review_findings(env, w["op-a"])) == 1
    assert await _review_findings(env, w["op-b"]) == []                    # trattamento fuori dallo scope
    assert await _review_findings(env, w["b"]) == []

    assert (await c.get(f"{API}/dashboard", headers=w["mgr"])).json()["notices_to_review"] == 1
    items = (await c.get(f"{API}/notifications", headers=w["mgr"])).json()["items"]
    assert [i["title"] for i in items if i["kind"] == "notice_review_required"] == ["Informativa da verificare INF-000001"]

    events = (await c.get(f"{API}/outbox", params={"event_type": "privacy_notice."}, headers=ADMIN)).json()
    types = {e["event_type"] for e in events}
    assert {"privacy_notice.status.in_review", "privacy_notice.status.approved", "privacy_notice.links.updated"} <= types
    approved_event = next(e for e in events if e["event_type"] == "privacy_notice.status.approved")
    assert approved_event["payload"]["data"] == {"status": "APPROVED"}
    assert "Il Comune" not in str(events)                                   # nessun testo dell'informativa nell'outbox
