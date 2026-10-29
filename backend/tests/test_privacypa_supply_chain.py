"""Contratti (§18.3), sub-responsabili (§19) e verifiche di filiera (AC-PROC-002, AC-PROC-003)."""
from datetime import date, timedelta

import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_processing import ProcessingStatus
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import put_links, TA, TB, _h, ifm

API = "/api/privacypa"
PARTIES = f"{API}/parties"
CONTRACTS = f"{API}/contracts"
SUBS = f"{API}/subprocessors"
FINDINGS = f"{API}/findings"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def world(env):
    grant, c = env["grant"], env["client"]
    await grant(TA, "mgr", R.PRIVACY_MANAGER)
    await grant(TA, "reader", R.READ_ONLY)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    mgr = _h(TA, "mgr")
    parties = {}
    for key, name in (("p1", "Cloud Uno"), ("p2", "Hosting Due"), ("p3", "Backup Tre")):
        r = await c.post(PARTIES, json={"legal_name": name, "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=mgr)
        assert r.status_code == 201, r.text
        parties[key] = r.json()["id"]
    return {"mgr": mgr, "reader": _h(TA, "reader"), "b": _h(TB, "b-mgr"), **parties}


async def _contract(env, w, party="p1", who=None, **override):
    body = {"party_id": w[party], "title": "Servizio cloud", "status": "ACTIVE", "start_date": iso(-100), "end_date": iso(200), **override}
    return await env["client"].post(CONTRACTS, json=body, headers=who or w["mgr"])


async def _agreement(env, w, party="p1", **override):
    body = {"party_id": w[party], "status": "SIGNED", "signed_date": iso(-90), "effective_from": iso(-90), **override}
    r = await env["client"].post(f"{PARTIES}/agreements", json=body, headers=w["mgr"])
    assert r.status_code == 201, r.text
    return r.json()


async def _sub(env, w, processor="p1", sub="p2", **override):
    body = {"processor_party_id": w[processor], "subprocessor_party_id": w[sub], "service_description": "Hosting",
            "authorization_type": "SPECIFIC", "authorization_date": iso(-30), "status": "ACTIVE", **override}
    return await env["client"].post(SUBS, json=body, headers=w["mgr"])


# ---------------------------------------------------------------- contratti

@pytest.mark.asyncio
async def test_contract_crud_with_codes_normalization_and_locking(env, world):
    c, w = env["client"], world
    first = await _contract(env, w, cig="abc123def4", cup="a1b2c3d4e5f6g7h")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["code"] == "CT-000001" and body["cig"] == "ABC123DEF4" and body["row_version"] == 1
    assert (await _contract(env, w)).json()["code"] == "CT-000002"

    got = await c.get(f"{CONTRACTS}/{body['id']}", headers=w["reader"])
    assert got.headers["etag"] == '"1"'
    payload = {"party_id": w["p1"], "title": "Nuovo titolo", "status": "ACTIVE"}
    assert (await c.patch(f"{CONTRACTS}/{body['id']}", json=payload, headers=w["mgr"])).status_code == 428
    assert (await c.patch(f"{CONTRACTS}/{body['id']}", json=payload, headers=ifm(7, w["mgr"]))).status_code == 409
    ok = await c.patch(f"{CONTRACTS}/{body['id']}", json=payload, headers=ifm(1, w["mgr"]))
    assert ok.status_code == 200 and ok.json()["title"] == "Nuovo titolo" and ok.json()["row_version"] == 2
    moved = await c.patch(f"{CONTRACTS}/{body['id']}", json={**payload, "party_id": w["p2"]}, headers=ifm(2, w["mgr"]))
    assert moved.status_code == 400

    assert len((await c.get(CONTRACTS, params={"party_id": w["p1"]}, headers=w["reader"])).json()) == 2
    assert (await c.delete(f"{CONTRACTS}/{body['id']}", headers=w["mgr"])).status_code == 204
    assert (await c.get(f"{CONTRACTS}/{body['id']}", headers=w["mgr"])).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"cig": "TROPPO-LUNGO-1"}, {"cup": "CORTO"}, {"start_date": "2030-01-01", "end_date": "2029-01-01"},
    {"renewal_date": "2000-01-01", "start_date": "2020-01-01"}, {"organizational_unit_id": "unit-x"},
    {"document_id": "no-such-document"},
])
async def test_contract_validation(env, world, override):
    assert (await _contract(env, world, **override)).status_code == 400


@pytest.mark.asyncio
async def test_contract_needs_an_existing_party_and_respects_tenant_and_role(env, world):
    c, w = env["client"], world
    assert (await c.post(CONTRACTS, json={"party_id": "nope", "title": "X"}, headers=w["mgr"])).status_code == 400
    assert (await _contract(env, w, who=w["reader"])).status_code == 403
    created = (await _contract(env, w)).json()
    assert (await c.get(f"{CONTRACTS}/{created['id']}", headers=w["b"])).status_code == 404
    assert (await c.get(CONTRACTS, headers=w["b"])).json() == []
    assert (await _contract(env, w, party="p1", who=w["b"])).status_code == 400  # fornitore di un altro ente


@pytest.mark.asyncio
async def test_contract_links_to_agreements_and_processing_processors(env, world):
    c, w, pid = env["client"], world, env["ids"]["proc_a"]
    contract = (await _contract(env, w, party="p1")).json()
    other = (await _contract(env, w, party="p2")).json()

    ok = await _agreement(env, w, contract_id=contract["id"])
    assert ok["contract_id"] == contract["id"]
    bad = await c.post(f"{PARTIES}/agreements", json={"party_id": w["p1"], "contract_id": other["id"]}, headers=w["mgr"])
    assert bad.status_code == 400  # il contratto è di un altro fornitore

    url = f"{PROC_URL(pid)}/processors"
    assert (await put_links(c, url, json=[{"party_id": w["p1"], "contract_id": other["id"]}], headers=w["mgr"])).status_code == 400
    linked = await put_links(c, url, json=[{"party_id": w["p1"], "contract_id": contract["id"]}], headers=w["mgr"])
    assert linked.status_code == 200 and linked.json()[0]["contract_id"] == contract["id"]

    # un contratto in uso non si elimina finché è collegato
    blocked = await c.delete(f"{CONTRACTS}/{contract['id']}", headers=w["mgr"])
    assert blocked.status_code == 400 and "collegato" in blocked.json()["detail"]
    await put_links(c, url, json=[], headers=w["mgr"])
    await c.patch(f"{PARTIES}/agreements/{ok['id']}", json={"party_id": w["p1"], "contract_id": None}, headers=w["mgr"])
    assert (await c.delete(f"{CONTRACTS}/{contract['id']}", headers=w["mgr"])).status_code == 204


def PROC_URL(pid):
    return f"{API}/processing-activities/{pid}"


# ---------------------------------------------------------------- sub-responsabili

@pytest.mark.asyncio
async def test_subprocessor_crud_and_normalization(env, world):
    c, w = env["client"], world
    created = await _sub(env, w, countries=["it", "DE", "it"])
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["countries"] == ["IT", "DE"] and row["row_version"] == 1

    assert (await c.get(SUBS, params={"processor_party_id": w["p1"]}, headers=w["reader"])).json()[0]["id"] == row["id"]
    assert (await c.get(f"{SUBS}/{row['id']}", headers=w["reader"])).headers["etag"] == '"1"'
    payload = {k: row[k] for k in ("processor_party_id", "subprocessor_party_id", "service_description", "authorization_type")}
    payload.update(status="SUSPENDED", countries=["FR"])
    assert (await c.patch(f"{SUBS}/{row['id']}", json=payload, headers=w["mgr"])).status_code == 428
    ok = await c.patch(f"{SUBS}/{row['id']}", json=payload, headers=ifm(1, w["mgr"]))
    assert ok.status_code == 200 and ok.json()["countries"] == ["FR"] and ok.json()["row_version"] == 2
    swapped = {**payload, "processor_party_id": w["p3"]}
    assert (await c.patch(f"{SUBS}/{row['id']}", json=swapped, headers=ifm(2, w["mgr"]))).status_code == 400

    assert (await c.delete(f"{SUBS}/{row['id']}", headers=w["mgr"])).status_code == 204
    assert (await _sub(env, w)).status_code == 201  # dopo l'eliminazione la coppia si può registrare di nuovo


@pytest.mark.asyncio
async def test_subprocessor_rules(env, world):
    w = world
    assert (await _sub(env, w, sub="p1")).status_code == 400                       # sé stesso
    assert (await _sub(env, w, sub="p2", authorization_date=None)).status_code == 400  # attivo senza autorizzazione
    assert (await _sub(env, w, status="TO_REVIEW", authorization_date=None, valid_from=iso(10), valid_to=iso(1))).status_code == 400
    assert (await _sub(env, w, countries=["ITA"])).status_code == 400
    assert (await _sub(env, w, service_description="  ")).status_code in (400, 422)
    assert (await env["client"].post(SUBS, json={"processor_party_id": w["p1"], "subprocessor_party_id": "nope",
            "service_description": "x", "authorization_type": "GENERAL"}, headers=w["mgr"])).status_code == 400
    assert (await _sub(env, w)).status_code == 201
    assert (await _sub(env, w)).status_code == 400                                   # duplicato
    assert (await _sub(env, w, processor="p2", sub="p1")).status_code == 400          # ciclo diretto


@pytest.mark.asyncio
async def test_subprocessor_cycles_and_tree(env, world):
    c, w = env["client"], world
    assert (await _sub(env, w, "p1", "p2")).status_code == 201
    assert (await _sub(env, w, "p2", "p3")).status_code == 201
    assert (await _sub(env, w, "p3", "p1")).status_code == 400   # ciclo indiretto
    tree = (await c.get(f"{SUBS}/tree/{w['p1']}", headers=w["reader"])).json()
    assert tree["party_name"] == "Cloud Uno"
    assert [x["party_name"] for x in tree["children"]] == ["Hosting Due"]
    assert [x["party_name"] for x in tree["children"][0]["children"]] == ["Backup Tre"]
    assert (await c.get(f"{SUBS}/tree/nope", headers=w["reader"])).status_code == 404


@pytest.mark.asyncio
async def test_subprocessor_permissions_and_tenant(env, world):
    c, w = env["client"], world
    assert (await _sub(env, w)).status_code == 201
    body = {"processor_party_id": w["p1"], "subprocessor_party_id": w["p3"], "service_description": "x",
            "authorization_type": "GENERAL"}
    assert (await c.post(SUBS, json=body, headers=w["reader"])).status_code == 403
    assert (await c.get(SUBS, headers=w["b"])).json() == []
    assert (await c.get(f"{SUBS}/tree/{w['p1']}", headers=w["b"])).status_code == 404


# ---------------------------------------------------------------- verifiche (AC-PROC-002, AC-PROC-003)

async def _approve(env, pid):
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()


async def _findings(env, who, **params):
    r = await env["client"].get(FINDINGS, params=params, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_processor_without_valid_agreement_generates_a_finding(env, world):
    c, w, pid = env["client"], world, env["ids"]["proc_a"]
    await _approve(env, pid)
    assert (await put_links(c, f"{PROC_URL(pid)}/processors", json=[{"party_id": w["p1"]}], headers=w["mgr"])).status_code == 200

    found = await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT")
    assert [(f["entity_id"], f["severity"], f["params"]["party"]) for f in found] == [(pid, "HIGH", "Cloud Uno")]

    draft = await _agreement(env, w, status="DRAFT")
    assert await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT")  # una bozza non basta
    await env["client"].patch(f"{PARTIES}/agreements/{draft['id']}", json={"party_id": w["p1"], "status": "SIGNED", "expiry_date": iso(-1)}, headers=w["mgr"])
    assert await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT")  # scaduto
    valid = await _agreement(env, w, expiry_date=iso(400))
    assert await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT") == []

    # se il collegamento indica un atto preciso, deve essere quello a essere valido
    await put_links(c, f"{PROC_URL(pid)}/processors", json=[{"party_id": w["p1"], "processor_agreement_id": draft["id"]}], headers=w["mgr"])
    assert await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT")
    await put_links(c, f"{PROC_URL(pid)}/processors", json=[{"party_id": w["p1"], "processor_agreement_id": valid["id"]}], headers=w["mgr"])
    assert await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT") == []


@pytest.mark.asyncio
async def test_agreement_expiry_findings_and_horizon(env, world):
    w = world
    await _agreement(env, w, "p1", expiry_date=iso(-3))
    await _agreement(env, w, "p2", expiry_date=iso(30))
    await _agreement(env, w, "p3", expiry_date=iso(200))
    by_code = {f["code"]: f for f in await _findings(env, w["reader"]) if f["code"].startswith("AGREEMENT")}
    assert by_code["AGREEMENT_EXPIRED"]["severity"] == "HIGH" and by_code["AGREEMENT_EXPIRED"]["params"]["party"] == "Cloud Uno"
    assert by_code["AGREEMENT_EXPIRING"]["due_date"] == iso(30)
    admin = _h(TA, "root", "admin")

    async def set_horizon(days):
        rules = (await env["client"].get(f"{API}/compliance/rules", headers=admin)).json()
        version = next(r["version"] for r in rules if r["code"] == "AGREEMENT_EXPIRING")
        r = await env["client"].put(f"{API}/compliance/rules/AGREEMENT_EXPIRING", json={"params": {"horizon_days": days}}, headers=ifm(version, admin))
        assert r.status_code == 200, r.text

    await set_horizon(10)
    assert not [f for f in await _findings(env, w["reader"]) if f["code"] == "AGREEMENT_EXPIRING"]
    await set_horizon(365)
    assert len([f for f in await _findings(env, w["reader"]) if f["code"] == "AGREEMENT_EXPIRING"]) == 2


@pytest.mark.asyncio
async def test_contract_and_subprocessor_findings(env, world):
    w = world
    await _contract(env, w, "p1", end_date=iso(-5))
    await _contract(env, w, "p2", end_date=iso(400), renewal_date=iso(20))
    await _contract(env, w, "p3", end_date=iso(-5), status="TERMINATED")
    codes = [(f["code"], f["entity_code"]) for f in await _findings(env, w["reader"]) if f["code"].startswith("CONTRACT")]
    assert codes == [("CONTRACT_EXPIRED", "CT-000001"), ("CONTRACT_EXPIRING", "CT-000002")]

    await _agreement(env, w, "p1", expiry_date=iso(400), subprocessor_clause=False)
    await _sub(env, w, "p1", "p2", valid_to=iso(-1))
    got = {f["code"] for f in await _findings(env, w["reader"])}
    assert {"SUBPROCESSOR_EXPIRED", "SUBPROCESSOR_NO_CLAUSE"} <= got
    only_high = await _findings(env, w["reader"], severity="HIGH")
    assert only_high and all(f["severity"] == "HIGH" for f in only_high)


@pytest.mark.asyncio
async def test_findings_respect_processing_scope_and_role(env, world):
    c, w, ids, grant = env["client"], world, env["ids"], env["grant"]
    for pid in (ids["proc_a"], ids["proc_b"]):
        await _approve(env, pid)
        await put_links(c, f"{PROC_URL(pid)}/processors", json=[{"party_id": w["p1"]}], headers=w["mgr"])
    await grant(TA, "ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    ref = _h(TA, "ref-a")

    everyone = await _findings(env, w["mgr"], code="PROCESSOR_NO_VALID_AGREEMENT")
    assert {f["entity_id"] for f in everyone} == {ids["proc_a"], ids["proc_b"]}
    assert {f["entity_id"] for f in await _findings(env, ref, code="PROCESSOR_NO_VALID_AGREEMENT")} == {ids["proc_a"]}
    assert (await c.get(FINDINGS, headers=_h(TA, "stranger"))).status_code == 403
    assert (await c.get(FINDINGS, params={"severity": "URGENT"}, headers=w["mgr"])).status_code == 422
    assert await _findings(env, w["b"]) == []  # altro ente


@pytest.mark.asyncio
async def test_notifications_and_dashboard_surface_findings(env, world):
    c, w, pid = env["client"], world, env["ids"]["proc_a"]
    await _approve(env, pid)
    await put_links(c, f"{PROC_URL(pid)}/processors", json=[{"party_id": w["p1"]}], headers=w["mgr"])
    await _contract(env, w, "p1", end_date=iso(-2))

    items = (await c.get(f"{API}/notifications", headers=w["mgr"])).json()["items"]
    kinds = {i["kind"] for i in items}
    assert {"processor_no_valid_agreement", "contract_expired"} <= kinds
    assert all("Cloud Uno" not in i["title"] for i in items)  # nessun nome nel testo delle notifiche
    assert (await c.get(f"{API}/dashboard", headers=w["mgr"])).json()["findings_high"] >= 2
