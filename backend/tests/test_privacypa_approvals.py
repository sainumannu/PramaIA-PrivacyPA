"""Workflow di approvazione (§165), segregazione dei compiti (§166) e AC-REG-003."""
import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_processing import ProcessingStatus
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm, version_of

BASE = "/api/privacypa/processing-activities"
APPR = "/api/privacypa/approvals"
POLICY = "/api/privacypa/policy"
ADMIN = _h(TA, "root", "admin")


@pytest.fixture
def users():
    return {"req": _h(TA, "req"), "dpo": _h(TA, "dpo"), "both": _h(TA, "both"), "ed": _h(TA, "ed")}


@pytest_asyncio.fixture(autouse=True)
async def _roles(env):
    grant = env["grant"]
    await grant(TA, "req", R.OPERATOR)
    await grant(TA, "dpo", R.DPO)
    await grant(TA, "both", R.OPERATOR)
    await grant(TA, "both", R.DPO)
    await grant(TA, "ed", R.OPERATOR)
    await grant(TA, "ed", R.DPO)


async def _status(c, pid, target, who, notes=None):
    version = await version_of(c, f"{BASE}/{pid}")
    body = {"status": target} if notes is None else {"status": target, "notes": notes}
    return await c.post(f"{BASE}/{pid}/status", json=body, headers=ifm(version, who))


async def _pending(c, who, **params):
    r = await c.get(APPR, params={"state": "PENDING", **params}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def _set_policy(c, **rules):
    r = await c.put(f"{POLICY}/segregation", json=rules, headers=ifm(await _policy_version(c), ADMIN))
    assert r.status_code == 200, r.text


async def _policy_version(c):
    return (await c.get(f"{POLICY}/segregation", headers=ADMIN)).json()["row_version"]


@pytest.mark.asyncio
async def test_submit_creates_pending_approval_bound_to_version(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    assert (await _status(c, pid, "IN_REVIEW", users["req"], notes="Pronto per la revisione")).status_code == 200

    items = await _pending(c, users["dpo"])
    assert len(items) == 1
    item = items[0]
    assert item["entity_type"] == "processing_activity" and item["entity_id"] == pid
    assert item["requested_by"] == "req" and item["request_notes"] == "Pronto per la revisione"
    assert item["entity_version"] == await version_of(c, f"{BASE}/{pid}")
    assert item["organizational_unit_id"] == "unit-a"
    assert item["stale"] is False and item["can_decide"] is True


@pytest.mark.asyncio
async def test_approve_records_decision_and_moves_entity(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    approval = (await _pending(c, users["dpo"]))[0]

    ok = await c.post(f"{APPR}/{approval['id']}/decision", json={"decision": "APPROVED", "notes": "Ok"}, headers=users["dpo"])
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["state"] == "DECIDED" and body["decision"] == "APPROVED"
    assert body["approver_id"] == "dpo" and body["decision_at"] and body["notes"] == "Ok"
    assert (await c.get(f"{BASE}/{pid}", headers=users["dpo"])).json()["status"] == "APPROVED"

    again = await c.post(f"{APPR}/{approval['id']}/decision", json={"decision": "APPROVED"}, headers=users["dpo"])
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_reject_requires_note_and_returns_to_draft(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    first = (await _pending(c, users["dpo"]))[0]

    assert (await c.post(f"{APPR}/{first['id']}/decision", json={"decision": "REJECTED"}, headers=users["dpo"])).status_code == 400
    rejected = await c.post(
        f"{APPR}/{first['id']}/decision", json={"decision": "REJECTED", "notes": "Base giuridica errata"}, headers=users["dpo"]
    )
    assert rejected.status_code == 200 and rejected.json()["decision"] == "REJECTED"
    assert (await c.get(f"{BASE}/{pid}", headers=users["dpo"])).json()["status"] == "DRAFT"

    # nuovo invio: nuova richiesta, la precedente resta nello storico
    await _status(c, pid, "IN_REVIEW", users["req"])
    assert len(await _pending(c, users["dpo"])) == 1
    history = (await c.get(APPR, params={"entity_id": pid}, headers=users["dpo"])).json()
    assert sorted(h["state"] for h in history) == ["DECIDED", "PENDING"]


@pytest.mark.asyncio
async def test_changes_requested_through_status_endpoint_and_withdrawal(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])

    no_note = await _status(c, pid, "DRAFT", users["dpo"])
    assert no_note.status_code == 400
    back = await _status(c, pid, "DRAFT", users["dpo"], notes="Aggiungere la conservazione")
    assert back.status_code == 200 and back.json()["status"] == "DRAFT"
    decided = (await c.get(APPR, params={"entity_id": pid}, headers=users["dpo"])).json()[0]
    assert decided["decision"] == "CHANGES_REQUESTED" and decided["notes"] == "Aggiungere la conservazione"

    # il richiedente che ritira la richiesta non produce una decisione
    await _status(c, pid, "IN_REVIEW", users["req"])
    assert (await _status(c, pid, "DRAFT", users["req"])).status_code == 200
    states = sorted(h["state"] for h in (await c.get(APPR, params={"entity_id": pid}, headers=users["dpo"])).json())
    assert states == ["CANCELLED", "DECIDED"]
    assert await _pending(c, users["dpo"]) == []


@pytest.mark.asyncio
async def test_content_changed_after_request_makes_decision_stale(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    approval = (await _pending(c, users["dpo"]))[0]

    edited = await c.patch(
        f"{BASE}/{pid}",
        json={
            "title": "Titolo modificato", "organizational_unit_id": "unit-a", "description": "Descrizione",
            "legal_basis_codes": ["GDPR_6_1_E"], "data_subject_ids": ["subject-1"], "data_category_ids": ["category-1"],
        },
        headers=ifm(await version_of(c, f"{BASE}/{pid}"), users["req"]),
    )
    assert edited.status_code == 200, edited.text

    listed = (await _pending(c, users["dpo"]))[0]
    assert listed["stale"] is True and listed["can_decide"] is False and listed["blocked_reason"]
    assert (await c.post(f"{APPR}/{approval['id']}/decision", json={"decision": "APPROVED"}, headers=users["dpo"])).status_code == 409
    assert (await _status(c, pid, "APPROVED", users["dpo"])).status_code == 409

    # il richiedente lo rimanda in revisione: la nuova richiesta è valida
    await _status(c, pid, "DRAFT", users["req"])
    await _status(c, pid, "IN_REVIEW", users["req"])
    fresh = (await _pending(c, users["dpo"]))[0]
    assert fresh["id"] != approval["id"] and fresh["stale"] is False
    assert (await c.post(f"{APPR}/{fresh['id']}/decision", json={"decision": "APPROVED"}, headers=users["dpo"])).status_code == 200


# ---------------------------------------------------------------- segregazione dei compiti (§166)

@pytest.mark.asyncio
async def test_segregation_is_configurable_and_off_by_default(env, users):
    c, ids = env["client"], env["ids"]
    # senza policy chi ha inviato può anche approvare
    await _status(c, ids["proc_a"], "IN_REVIEW", users["both"])
    assert (await _status(c, ids["proc_a"], "APPROVED", users["both"])).status_code == 200

    await _set_policy(c, approver_not_requester=True)
    await _status(c, ids["proc_b"], "IN_REVIEW", users["both"])
    listed = (await c.get(APPR, params={"state": "PENDING"}, headers=users["both"])).json()[0]
    assert listed["can_decide"] is False and "sé stessi" in listed["blocked_reason"]

    denied = await c.post(f"{APPR}/{listed['id']}/decision", json={"decision": "APPROVED"}, headers=users["both"])
    assert denied.status_code == 403
    assert (await _status(c, ids["proc_b"], "APPROVED", users["both"])).status_code == 403
    assert (await c.get(f"{BASE}/{ids['proc_b']}", headers=users["dpo"])).json()["status"] == "IN_REVIEW"

    assert (await c.post(f"{APPR}/{listed['id']}/decision", json={"decision": "APPROVED"}, headers=users["dpo"])).status_code == 200


@pytest.mark.asyncio
async def test_segregation_blocks_approving_own_edits(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _set_policy(c, approver_not_editor=True)
    edited = await c.patch(
        f"{BASE}/{pid}",
        json={
            "title": "Modificato da ed", "organizational_unit_id": "unit-a", "description": "Descrizione",
            "legal_basis_codes": ["GDPR_6_1_E"], "data_subject_ids": ["subject-1"], "data_category_ids": ["category-1"],
        },
        headers=ifm(await version_of(c, f"{BASE}/{pid}"), users["ed"]),
    )
    assert edited.status_code == 200
    await _status(c, pid, "IN_REVIEW", users["req"])

    assert (await _status(c, pid, "APPROVED", users["ed"])).status_code == 403
    assert (await _status(c, pid, "APPROVED", users["dpo"])).status_code == 200


# ---------------------------------------------------------------- permesso e scope

@pytest.mark.asyncio
async def test_decision_needs_approve_role_on_the_record_unit(env, users):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "mixed", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await grant(TA, "mixed", R.DPO, ScopeType.UNIT, "unit-b")
    await grant(TA, "dpo-b", R.DPO, ScopeType.UNIT, "unit-b")
    mixed, dpo_b = _h(TA, "mixed"), _h(TA, "dpo-b")
    await _status(c, ids["proc_a"], "IN_REVIEW", users["req"])
    approval_id = (await _pending(c, users["dpo"]))[0]["id"]

    # vede la richiesta (scope di lettura su A) ma non può deciderla
    assert (await _pending(c, mixed, decidable="true")) == []
    assert (await c.post(f"{APPR}/{approval_id}/decision", json={"decision": "APPROVED"}, headers=mixed)).status_code == 403
    # chi ha scope solo su B non la vede né la decide
    assert (await _pending(c, dpo_b)) == []
    assert (await c.get(f"{APPR}/{approval_id}", headers=dpo_b)).status_code == 403
    assert (await c.post(f"{APPR}/{approval_id}/decision", json={"decision": "APPROVED"}, headers=dpo_b)).status_code == 403
    assert (await c.get(f"{BASE}/{ids['proc_a']}", headers=users["dpo"])).json()["status"] == "IN_REVIEW"


@pytest.mark.asyncio
async def test_roleless_user_cannot_read_or_decide(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    approval_id = (await _pending(c, users["dpo"]))[0]["id"]
    stranger = _h(TA, "stranger")
    assert (await c.get(APPR, headers=stranger)).status_code == 403
    assert (await c.post(f"{APPR}/{approval_id}/decision", json={"decision": "APPROVED"}, headers=stranger)).status_code == 403


@pytest.mark.asyncio
async def test_operator_cannot_approve_even_through_the_decision_endpoint(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    approval_id = (await _pending(c, users["dpo"]))[0]["id"]
    # AC-REG-003: solo chi ha `processing.approve`
    assert (await c.post(f"{APPR}/{approval_id}/decision", json={"decision": "APPROVED"}, headers=users["req"])).status_code == 403
    assert (await _status(c, pid, "APPROVED", users["req"])).status_code == 403


@pytest.mark.asyncio
async def test_approvals_are_isolated_between_tenants(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    approval_id = (await _pending(c, users["dpo"]))[0]["id"]
    other = _h(TB, "b-admin", "admin")
    assert (await c.get(APPR, headers=other)).json() == []
    assert (await c.get(f"{APPR}/{approval_id}", headers=other)).status_code == 404
    assert (await c.post(f"{APPR}/{approval_id}/decision", json={"decision": "APPROVED"}, headers=other)).status_code == 404


# ---------------------------------------------------------------- DPIA, record già in revisione, audit

@pytest.mark.asyncio
async def test_dpia_approval_flow(env, users):
    c, maker, pid = env["client"], env["maker"], env["ids"]["proc_a"]
    async with maker() as db:
        dpia = await DpiaService(db).create(
            TA, "seed", "DPIA A", processing_activity_id=pid,
            controller_decision="Procedere", screening_rationale="Necessaria",
        )
        await db.commit()
    url = f"/api/privacypa/dpias/{dpia.id}"

    async def move(target, who, notes=None):
        body = {"status": target} if notes is None else {"status": target, "notes": notes}
        return await c.post(f"{url}/status", json=body, headers=ifm(await version_of(c, url), who))

    for target in ("IN_PROGRESS", "DPO_REVIEW", "AWAITING_DECISION"):
        assert (await move(target, users["req"])).status_code == 200
    item = (await _pending(c, users["dpo"], entity_type="dpia"))[0]
    assert item["entity_id"] == dpia.id and item["organizational_unit_id"] == "unit-a"

    rejected = await c.post(f"{APPR}/{item['id']}/decision", json={"decision": "REJECTED", "notes": "Rischi incompleti"}, headers=users["dpo"])
    assert rejected.status_code == 200
    assert (await c.get(url, headers=users["dpo"])).json()["status"] == "IN_PROGRESS"

    for target in ("DPO_REVIEW", "AWAITING_DECISION"):
        assert (await move(target, users["req"])).status_code == 200
    item = (await _pending(c, users["dpo"], entity_type="dpia"))[0]
    assert (await c.post(f"{APPR}/{item['id']}/decision", json={"decision": "APPROVED"}, headers=users["req"])).status_code == 403
    assert (await c.post(f"{APPR}/{item['id']}/decision", json={"decision": "APPROVED"}, headers=users["dpo"])).status_code == 200
    approved = (await c.get(url, headers=users["dpo"])).json()
    assert approved["status"] == "APPROVED" and approved["approved_by"] == "dpo"


@pytest.mark.asyncio
async def test_record_already_in_review_gets_an_implicit_request(env, users):
    c, maker, pid = env["client"], env["maker"], env["ids"]["proc_a"]
    async with maker() as db:  # stato precedente all'introduzione delle approvazioni: nessuna richiesta registrata
        await ProcessingService(db).transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await db.commit()
    assert (await _status(c, pid, "APPROVED", users["dpo"])).status_code == 200
    history = (await c.get(APPR, params={"entity_id": pid}, headers=users["dpo"])).json()
    assert len(history) == 1 and history[0]["decision"] == "APPROVED"
    assert history[0]["requested_by"] == "system:legacy" and "implicita" in history[0]["request_notes"]


@pytest.mark.asyncio
async def test_approval_events_are_audited(env, users):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _status(c, pid, "IN_REVIEW", users["req"])
    await _status(c, pid, "APPROVED", users["dpo"])
    events = {e["event_type"] for e in (await c.get("/api/privacypa/audit", params={"entity_id": pid}, headers=ADMIN)).json()}
    assert {"approval.requested", "approval.approved", "processing_activity.status.approved"} <= events
