"""Formazione (§44) e autorizzati al trattamento (§45)."""
from datetime import date, datetime, time, timedelta

import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_people import TrainingAssignment
from backend.services.privacypa_compliance_service import add_months
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
TR, AU, FINDINGS = f"{API}/training", f"{API}/authorizations", f"{API}/findings"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("op", R.OPERATOR, ScopeType.TENANT, None),
        ("op2", R.OPERATOR, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a"), ("ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b"),
        ("aud", R.AUDITOR, ScopeType.TENANT, None),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "op", "op2", "reader", "ref-a", "ref-b", "aud")} | {"b": _h(TB, "b-mgr")}


async def _course(env, who, **override):
    body = {"title": "Privacy di base", "version": "1.0", "mandatory": True, "validity_months": 24, **override}
    r = await env["client"].post(f"{TR}/courses", json=body, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def _assign(env, who, course_id, users, due=30):
    r = await env["client"].post(f"{TR}/courses/{course_id}/assign", json={"user_ids": users, "due_date": iso(due)}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def _codes(env, who, code):
    r = await env["client"].get(FINDINGS, params={"code": code}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ formazione

@pytest.mark.asyncio
async def test_course_lifecycle_and_validation(env, w):
    c = env["client"]
    course = await _course(env, w["mgr"])
    assert course["code"] == "CO-000001" and course["mandatory"] and course["active"] and course["validity_months"] == 24
    assert (await _course(env, w["mgr"], title="Secondo"))["code"] == "CO-000002"

    bad = {"title": "X", "version": "1.0"}
    assert (await c.post(f"{TR}/courses", json={**bad, "validity_months": 0}, headers=w["mgr"])).status_code == 422
    assert (await c.post(f"{TR}/courses", json={**bad, "target_roles": ["CAPO"]}, headers=w["mgr"])).status_code == 400
    assert (await c.post(f"{TR}/courses", json={**bad, "target_unit_ids": ["unit-x"]}, headers=w["mgr"])).status_code == 400
    for who in ("op", "reader", "aud"):
        assert (await c.post(f"{TR}/courses", json=bad, headers=w[who])).status_code == 403

    # tutti vedono il catalogo, un altro ente no
    assert len((await c.get(f"{TR}/courses", headers=w["reader"])).json()) == 2
    assert (await c.get(f"{TR}/courses", headers=w["b"])).json() == []
    assert (await c.get(f"{TR}/courses/{course['id']}", headers=w["b"])).status_code == 404

    url = f"{TR}/courses/{course['id']}"
    body = {"title": "Privacy avanzata", "version": "2.0", "mandatory": True, "validity_months": 12}
    assert (await c.patch(url, json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(course["row_version"] + 5, w["mgr"]))).status_code == 409
    updated = await c.patch(url, json=body, headers=ifm(course["row_version"], w["mgr"]))
    assert updated.status_code == 200 and updated.json()["version"] == "2.0" and updated.json()["validity_months"] == 12

    # ritirato: non si assegna più
    retired = await c.post(f"{url}/active", json={"active": False}, headers=ifm(updated.json()["row_version"], w["mgr"]))
    assert retired.status_code == 200 and retired.json()["active"] is False
    late = await c.post(f"{url}/assign", json={"user_ids": ["op"], "due_date": iso(10)}, headers=w["mgr"])
    assert late.status_code == 400
    active = (await c.get(f"{TR}/courses", params={"active": "true"}, headers=w["mgr"])).json()
    assert [x["title"] for x in active] == ["Secondo"]


@pytest.mark.asyncio
async def test_assignment_completion_waiver_and_visibility(env, w):
    c = env["client"]
    course = await _course(env, w["mgr"], validity_months=12)
    result = await _assign(env, w["mgr"], course["id"], ["op", "op2", "op"])
    assert len(result["created"]) == 2 and result["skipped"] == []
    again = await _assign(env, w["mgr"], course["id"], ["op", "reader"])
    assert [a["user_id"] for a in again["created"]] == ["reader"] and again["skipped"] == ["op"]

    bad_due = await c.post(f"{TR}/courses/{course['id']}/assign", json={"user_ids": ["op"], "due_date": iso(-1)}, headers=w["mgr"])
    assert bad_due.status_code == 400
    assert (await c.post(f"{TR}/courses/{course['id']}/assign", json={"user_ids": [], "due_date": iso(3)}, headers=w["mgr"])).status_code == 422
    assert (await c.post(f"{TR}/courses/{course['id']}/assign", json={"user_ids": ["x"], "due_date": iso(3)}, headers=w["op"])).status_code == 403

    # chi non gestisce la formazione vede solo le proprie assegnazioni, anche chiedendo di altri
    mine = (await c.get(f"{TR}/assignments", params={"user_id": "op2"}, headers=w["op"])).json()
    assert [a["user_id"] for a in mine] == ["op"]
    assert len((await c.get(f"{TR}/assignments", headers=w["mgr"])).json()) == 3
    assert [a["user_id"] for a in (await c.get(f"{TR}/assignments", params={"mine": "true"}, headers=w["mgr"])).json()] == []

    op_row = mine[0]
    url = f"{TR}/assignments/{op_row['id']}"
    done = {"completed_at": TODAY.isoformat(), "score": 88}
    assert (await c.post(f"{url}/complete", json=done, headers=w["mgr"])).status_code == 428
    assert (await c.post(f"{url}/complete", json=done, headers=ifm(op_row["row_version"] + 3, w["mgr"]))).status_code == 409
    assert (await c.post(f"{url}/complete", json={**done, "score": 101}, headers=ifm(op_row["row_version"], w["mgr"]))).status_code == 422
    future = {**done, "completed_at": iso(2)}
    assert (await c.post(f"{url}/complete", json=future, headers=ifm(op_row["row_version"], w["mgr"]))).status_code == 400
    assert (await c.post(f"{url}/complete", json={**done, "certificate_document_id": "nessuno"}, headers=ifm(op_row["row_version"], w["mgr"]))).status_code == 400
    assert (await c.post(f"{url}/complete", json=done, headers=ifm(op_row["row_version"], w["op"]))).status_code == 403
    ok = await c.post(f"{url}/complete", json=done, headers=ifm(op_row["row_version"], w["mgr"]))
    assert ok.status_code == 201, ok.text
    assert ok.json()["next_due_at"] == add_months(datetime.combine(TODAY, time.min), 12).date().isoformat()
    closed = (await c.get(f"{TR}/assignments", params={"status": "COMPLETED"}, headers=w["mgr"])).json()
    assert [a["user_id"] for a in closed] == ["op"]
    assert (await c.post(f"{url}/complete", json=done, headers=ifm(closed[0]["row_version"], w["mgr"]))).status_code == 400

    # esonero: solo con motivazione
    op2 = next(a for a in (await c.get(f"{TR}/assignments", params={"user_id": "op2"}, headers=w["mgr"])).json())
    waive = f"{TR}/assignments/{op2['id']}/waive"
    assert (await c.post(waive, json={"reason": ""}, headers=ifm(op2["row_version"], w["mgr"]))).status_code == 422
    assert (await c.post(waive, json={"reason": "   "}, headers=ifm(op2["row_version"], w["mgr"]))).status_code == 400
    waived = await c.post(waive, json={"reason": "Personale in aspettativa"}, headers=ifm(op2["row_version"], w["mgr"]))
    assert waived.status_code == 200 and waived.json()["status"] == "WAIVED"

    # storico dei completamenti: proprio per chi non gestisce, completo per chi gestisce
    assert len((await c.get(f"{TR}/completions", headers=w["mgr"])).json()) == 1
    assert len((await c.get(f"{TR}/completions", headers=w["op"])).json()) == 1
    assert (await c.get(f"{TR}/completions", headers=w["op2"])).json() == []
    assert (await c.get(f"{TR}/completions", headers=w["b"])).json() == []


@pytest.mark.asyncio
async def test_sessions_and_automatic_assignment_by_role(env, w):
    c = env["client"]
    course = await _course(env, w["mgr"], target_roles=["OPERATOR"])
    session = await c.post(f"{TR}/courses/{course['id']}/sessions", json={"session_date": iso(-3), "trainer": "Dott.ssa Rossi"}, headers=w["mgr"])
    assert session.status_code == 201 and session.json()["trainer"] == "Dott.ssa Rossi"
    assert len((await c.get(f"{TR}/courses/{course['id']}/sessions", headers=w["reader"])).json()) == 1
    assert (await c.post(f"{TR}/courses/{course['id']}/sessions", json={"session_date": iso(1)}, headers=w["op"])).status_code == 403

    preview = (await c.get(f"{TR}/courses/{course['id']}/targets", headers=w["mgr"])).json()["user_ids"]
    assert preview == ["op", "op2"]
    assert (await c.get(f"{TR}/courses/{course['id']}/targets", headers=w["op"])).status_code == 403

    created = await c.post(f"{TR}/courses/{course['id']}/assign-targets", json={"due_date": iso(20)}, headers=w["mgr"])
    assert created.status_code == 201 and sorted(a["user_id"] for a in created.json()["created"]) == ["op", "op2"]
    # ripetere non duplica; chi ha un completamento valido non viene riassegnato
    assert (await c.post(f"{TR}/courses/{course['id']}/assign-targets", json={"due_date": iso(20)}, headers=w["mgr"])).json()["created"] == []
    row = next(a for a in created.json()["created"] if a["user_id"] == "op")
    complete = {"completed_at": TODAY.isoformat(), "session_id": session.json()["id"]}
    assert (await c.post(f"{TR}/assignments/{row['id']}/complete", json=complete, headers=ifm(row["row_version"], w["mgr"]))).status_code == 201
    assert (await c.get(f"{TR}/courses/{course['id']}/targets", headers=w["mgr"])).json()["user_ids"] == ["op2"]

    other = await _course(env, w["mgr"], title="Altro")
    other_row = (await _assign(env, w["mgr"], other["id"], ["op"]))["created"][0]
    wrong = {"completed_at": TODAY.isoformat(), "session_id": session.json()["id"]}
    assert (await c.post(f"{TR}/assignments/{other_row['id']}/complete", json=wrong, headers=ifm(other_row["row_version"], w["mgr"]))).status_code == 400


@pytest.mark.asyncio
async def test_training_rules_raise_and_close_findings(env, w):
    c = env["client"]
    course = await _course(env, w["mgr"], validity_months=1)
    row = (await _assign(env, w["mgr"], course["id"], ["op"]))["created"][0]
    assert await _codes(env, w["mgr"], "TRAINING_OVERDUE") == []

    async with env["maker"]() as db:                       # la scadenza non si può assegnare nel passato: la si fa passare
        item = await db.get(TrainingAssignment, row["id"])
        item.due_date = TODAY - timedelta(days=3)
        await db.commit()
    overdue = await _codes(env, w["mgr"], "TRAINING_OVERDUE")
    assert len(overdue) == 1 and overdue[0]["entity_type"] == "training_assignment" and overdue[0]["severity"] == "MEDIUM"
    assert overdue[0]["entity_code"] == "CO-000001"

    row = (await c.get(f"{TR}/assignments", headers=w["mgr"])).json()[0]
    done = await c.post(f"{TR}/assignments/{row['id']}/complete", json={"completed_at": TODAY.isoformat()}, headers=ifm(row["row_version"], w["mgr"]))
    assert done.status_code == 201
    assert await _codes(env, w["mgr"], "TRAINING_OVERDUE") == []          # completata: si chiude
    renewal = await _codes(env, w["mgr"], "TRAINING_RENEWAL_DUE")          # validità di 1 mese: scade entro l'orizzonte
    assert len(renewal) == 1 and renewal[0]["entity_type"] == "training_completion"

    await _assign(env, w["mgr"], course["id"], ["op"], due=20)             # rinnovo assegnato
    assert await _codes(env, w["mgr"], "TRAINING_RENEWAL_DUE") == []

    # un corso facoltativo non genera scadenze
    optional = await _course(env, w["mgr"], title="Facoltativo", mandatory=False)
    opt = (await _assign(env, w["mgr"], optional["id"], ["op2"]))["created"][0]
    async with env["maker"]() as db:
        item = await db.get(TrainingAssignment, opt["id"])
        item.due_date = TODAY - timedelta(days=9)
        await db.commit()
    assert await _codes(env, w["mgr"], "TRAINING_OVERDUE") == []


# ================================================================ autorizzati

async def _profile(env, who, name="Consultazione"):
    r = await env["client"].post(f"{AU}/profiles", json={"name": name}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


def _auth(env, profile, **override):
    return {"user_id": "op", "processing_activity_id": env["ids"]["proc_a"], "authorization_profile_id": profile["id"],
            "valid_from": iso(-10), "valid_to": iso(300), **override}


@pytest.mark.asyncio
async def test_profiles_and_authorization_rules(env, w):
    c = env["client"]
    profile = await _profile(env, w["mgr"])
    assert (await c.post(f"{AU}/profiles", json={"name": " Consultazione "}, headers=w["mgr"])).status_code == 400
    assert (await c.post(f"{AU}/profiles", json={"name": "X"}, headers=w["op"])).status_code == 403
    assert [p["name"] for p in (await c.get(f"{AU}/profiles", headers=w["ref-a"])).json()] == ["Consultazione"]
    assert (await c.get(f"{AU}/profiles", headers=w["op"])).status_code == 403          # elenco delle persone autorizzate: non per tutti

    created = await c.post(AU, json=_auth(env, profile), headers=w["ref-a"])
    assert created.status_code == 201, created.text
    auth = created.json()
    assert auth["active"] is True and auth["granted_by"] == "ref-a"

    assert (await c.post(AU, json=_auth(env, profile), headers=w["ref-a"])).status_code == 400            # equivalente e sovrapposta
    assert (await c.post(AU, json=_auth(env, profile, user_id="op2", valid_to=iso(-20)), headers=w["ref-a"])).status_code == 400
    assert (await c.post(AU, json=_auth(env, profile, processing_activity_id=None), headers=w["ref-a"])).status_code == 400
    assert (await c.post(AU, json=_auth(env, {"id": "nessuno"}), headers=w["ref-a"])).status_code == 400
    assert (await c.post(AU, json=_auth(env, profile, processing_activity_id="nessuno"), headers=w["ref-a"])).status_code == 400

    # il ruolo vale sulla propria struttura: ref-a non autorizza su un trattamento di unit-b, né altri enti
    assert (await c.post(AU, json=_auth(env, profile, processing_activity_id=env["ids"]["proc_b"]), headers=w["ref-a"])).status_code == 403
    assert (await c.post(AU, json=_auth(env, profile, processing_activity_id=env["ids"]["proc_b"]), headers=w["ref-b"])).status_code == 201
    assert (await c.post(AU, json=_auth(env, profile, processing_activity_id=env["ids"]["proc_x"]), headers=w["mgr"])).status_code == 400
    assert (await c.post(AU, json=_auth(env, profile), headers=w["aud"])).status_code == 403

    # lettura per scope del trattamento
    assert [a["processing_activity_id"] for a in (await c.get(AU, headers=w["ref-a"])).json()] == [env["ids"]["proc_a"]]
    assert len((await c.get(AU, headers=w["mgr"])).json()) == 2
    assert len((await c.get(AU, params={"user_id": "op2"}, headers=w["mgr"])).json()) == 0
    assert (await c.get(f"{AU}/{auth['id']}", headers=w["ref-b"])).status_code == 403
    assert (await c.get(f"{AU}/{auth['id']}", headers=w["b"])).status_code == 404
    assert (await c.get(AU, headers=w["b"])).json() == []

    # il profilo in uso non si elimina
    assert (await c.delete(f"{AU}/profiles/{profile['id']}", headers=w["mgr"])).status_code == 400
    assert (await c.delete(f"{AU}/profiles/{(await _profile(env, w['mgr'], 'Inutilizzato'))['id']}", headers=w["mgr"])).status_code == 204


@pytest.mark.asyncio
async def test_update_revoke_and_review_are_versioned_and_reasoned(env, w):
    c = env["client"]
    profile = await _profile(env, w["mgr"])
    auth = (await c.post(AU, json=_auth(env, profile), headers=w["ref-a"])).json()
    url = f"{AU}/{auth['id']}"

    body = _auth(env, profile, valid_to=iso(100))
    assert (await c.patch(url, json=body, headers=w["ref-a"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(auth["row_version"] + 4, w["ref-a"]))).status_code == 409
    assert (await c.patch(url, json=_auth(env, profile, user_id="op2"), headers=ifm(auth["row_version"], w["ref-a"]))).status_code == 400
    assert (await c.patch(url, json=body, headers=ifm(auth["row_version"], w["ref-b"]))).status_code == 403
    changed = await c.patch(url, json=body, headers=ifm(auth["row_version"], w["ref-a"]))
    assert changed.status_code == 200 and changed.json()["valid_to"] == iso(100)

    version = changed.json()["row_version"]
    assert (await c.post(f"{url}/revoke", json={"reason": "Cambio di mansione"}, headers=ifm(version, w["op"]))).status_code == 403
    assert (await c.post(f"{url}/revoke", json={"reason": "  "}, headers=ifm(version, w["ref-a"]))).status_code == 400
    reviewed = await c.post(f"{url}/confirm-review", headers=ifm(version, w["ref-a"]))
    assert reviewed.status_code == 200 and reviewed.json()["last_reviewed_by"] == "ref-a"
    revoked = await c.post(f"{url}/revoke", json={"reason": "Cambio di mansione"}, headers=ifm(reviewed.json()["row_version"], w["ref-a"]))
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False and revoked.json()["revoked_by"] == "ref-a" and revoked.json()["revoked_reason"] == "Cambio di mansione"

    version = revoked.json()["row_version"]
    assert (await c.post(f"{url}/revoke", json={"reason": "ancora"}, headers=ifm(version, w["ref-a"]))).status_code == 400
    assert (await c.patch(url, json=body, headers=ifm(version, w["ref-a"]))).status_code == 400
    assert (await c.post(f"{url}/confirm-review", headers=ifm(version, w["ref-a"]))).status_code == 400
    # la revoca libera il periodo: si può concedere di nuovo
    assert (await c.post(AU, json=_auth(env, profile), headers=w["ref-a"])).status_code == 201
    assert [a["active"] for a in (await c.get(AU, params={"active_only": "true"}, headers=w["mgr"])).json()] == [True]


@pytest.mark.asyncio
async def test_authorization_on_asset_uses_the_owner_unit(env, w):
    c = env["client"]
    profile = await _profile(env, w["mgr"])
    asset = await c.post(f"{API}/assets", json={"name": "Gestionale", "asset_type": "APPLICATION", "owner_unit_id": "unit-a"}, headers=w["mgr"])
    assert asset.status_code == 201, asset.text
    body = {"user_id": "op", "asset_id": asset.json()["id"], "authorization_profile_id": profile["id"], "valid_from": iso(-1)}
    assert (await c.post(AU, json=body, headers=w["ref-b"])).status_code == 403
    assert (await c.post(AU, json=body, headers=w["ref-a"])).status_code == 201
    assert (await c.post(AU, json={**body, "asset_id": "nessuno"}, headers=w["mgr"])).status_code == 400
    assert len((await c.get(AU, params={"asset_id": asset.json()["id"]}, headers=w["ref-b"])).json()) == 1      # sistema: registro condiviso


@pytest.mark.asyncio
async def test_authorization_rules(env, w):
    c = env["client"]
    profile = await _profile(env, w["mgr"])
    expired = (await c.post(AU, json=_auth(env, profile, valid_from=iso(-60), valid_to=iso(-2)), headers=w["ref-a"])).json()
    findings = await _codes(env, w["mgr"], "AUTHORIZATION_EXPIRED_NOT_REVOKED")
    assert len(findings) == 1 and findings[0]["severity"] == "HIGH" and findings[0]["entity_id"] == expired["id"]
    # visibile solo a chi vede il trattamento
    assert len(await _codes(env, w["ref-a"], "AUTHORIZATION_EXPIRED_NOT_REVOKED")) == 1
    assert await _codes(env, w["ref-b"], "AUTHORIZATION_EXPIRED_NOT_REVOKED") == []
    revoked = await c.post(f"{AU}/{expired['id']}/revoke", json={"reason": "Scaduta"}, headers=ifm(expired["row_version"], w["ref-a"]))
    assert revoked.status_code == 200
    assert await _codes(env, w["mgr"], "AUTHORIZATION_EXPIRED_NOT_REVOKED") == []

    # cambio di struttura del trattamento dopo la concessione
    active = (await c.post(AU, json=_auth(env, profile, user_id="op2"), headers=w["ref-a"])).json()
    assert await _codes(env, w["mgr"], "AUTHORIZATION_REVIEW_UNIT_CHANGED") == []
    url = f"{API}/processing-activities/{env['ids']['proc_a']}"
    body = {"title": "Trattamento A", "organizational_unit_id": "unit-b", "description": "Descrizione", "purposes": ["Sicurezza urbana"],
            "legal_basis_codes": ["GDPR_6_1_E"], "data_subject_ids": ["subject-1"], "data_category_ids": ["category-1"]}
    moved = await c.patch(url, json=body, headers=ifm(await version_of(c, url), w["mgr"]))
    assert moved.status_code == 200, moved.text
    review = await _codes(env, w["mgr"], "AUTHORIZATION_REVIEW_UNIT_CHANGED")
    assert len(review) == 1 and review[0]["entity_id"] == active["id"]
    # la conferma della verifica chiude la segnalazione
    current = (await c.get(f"{AU}/{active['id']}", headers=w["mgr"])).json()
    assert (await c.post(f"{AU}/{active['id']}/confirm-review", headers=ifm(current["row_version"], w["mgr"]))).status_code == 200
    assert await _codes(env, w["mgr"], "AUTHORIZATION_REVIEW_UNIT_CHANGED") == []
