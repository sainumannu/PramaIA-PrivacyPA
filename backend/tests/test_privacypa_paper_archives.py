"""Archivi cartacei: campi propri dell'asset, misure fisiche spuntate dalla scheda, regola PAPER_ARCHIVE_INCOMPLETE."""
import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.tests.conftest import TA, _h, ifm

API = "/api/privacypa"
ASSETS = f"{API}/assets"


@pytest_asyncio.fixture
async def mgr(env):
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    return _h(TA, "mgr")


async def _measure(env, who, name, category="PHYSICAL"):
    r = await env["client"].post(f"{API}/security-measures", json={"name": name, "category": category}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _implemented(env, who, asset_id):
    r = await env["client"].get(f"{API}/measure-implementations", params={"entity_type": "asset", "entity_id": asset_id}, headers=who)
    return {(i["measure_id"], i["implementation_status"]) for i in r.json()}


async def _paper_findings(env, who, asset_id):
    r = await env["client"].get(f"{API}/findings", params={"code": "PAPER_ARCHIVE_INCOMPLETE"}, headers=who)
    assert r.status_code == 200, r.text
    return {f["params"]["missing"] for f in r.json() if f["entity_id"] == asset_id}


@pytest.mark.asyncio
async def test_paper_archive_keeps_its_own_fields_and_physical_measures(env, mgr):
    c = env["client"]
    cabinets, fire = await _measure(env, mgr, "Armadi chiusi"), await _measure(env, mgr, "Antincendio")
    other = await _measure(env, mgr, "Formazione", category="TRAINING")

    created = await c.post(ASSETS, json={
        "name": "Archivio di deposito anagrafe", "asset_type": "PAPER_ARCHIVE",
        "location_site": " Palazzo comunale, via Roma 1 ", "location_room": "Seminterrato, stanza 3",
        "archive_phase": "DEPOSIT", "archive_extent": "120 faldoni", "custodian_user_id": "u-custode",
        "environmental_risks": ["HUMIDITY", "FLOOD", "HUMIDITY"], "physical_measure_ids": [cabinets],
    }, headers=mgr)
    assert created.status_code == 201, created.text
    a = created.json()
    assert (a["location_site"], a["archive_phase"], a["environmental_risks"]) == ("Palazzo comunale, via Roma 1", "DEPOSIT", ["FLOOD", "HUMIDITY"])
    assert await _implemented(env, mgr, a["id"]) == {(cabinets, "IMPLEMENTED")} and a["physical_measure_ids"] == [cabinets]
    catalog = {m["id"] for m in (await c.get(f"{ASSETS}/physical-measures", headers=mgr)).json()}
    assert {cabinets, fire} <= catalog and other not in catalog

    # misure di altra categoria o solo pianificate (dai rischi o dall'API) restano; le attuate seguono la scheda
    alarm = await _measure(env, mgr, "Allarme")
    for measure in (other, alarm):
        assert (await c.post(f"{API}/measure-implementations", json={
            "measure_id": measure, "entity_type": "asset", "entity_id": a["id"], "implementation_status": "PLANNED"}, headers=mgr)).status_code == 201
    r = await c.patch(f"{ASSETS}/{a['id']}", json={**a, "physical_measure_ids": [fire]}, headers=ifm(a["row_version"], mgr))
    assert r.status_code == 200, r.text
    assert await _implemented(env, mgr, a["id"]) == {(fire, "IMPLEMENTED"), (other, "PLANNED"), (alarm, "PLANNED")}
    a = r.json()
    r = await c.patch(f"{ASSETS}/{a['id']}", json={**a, "physical_measure_ids": [fire, alarm]}, headers=ifm(a["row_version"], mgr))
    assert r.json()["physical_measure_ids"] == sorted([fire, alarm])
    a = r.json()
    r = await c.patch(f"{ASSETS}/{a['id']}", json={**a, "physical_measure_ids": [fire]}, headers=ifm(a["row_version"], mgr))
    assert await _implemented(env, mgr, a["id"]) == {(fire, "IMPLEMENTED"), (other, "PLANNED")}

    # senza l'elenco le misure restano com'erano; una misura non fisica non si spunta dalla scheda
    a = r.json()
    r = await c.patch(f"{ASSETS}/{a['id']}", json={**a, "physical_measure_ids": None}, headers=ifm(a["row_version"], mgr))
    assert r.status_code == 200 and await _implemented(env, mgr, a["id"]) == {(fire, "IMPLEMENTED"), (other, "PLANNED")}
    a = r.json()
    assert (await c.patch(f"{ASSETS}/{a['id']}", json={**a, "physical_measure_ids": [other]},
                          headers=ifm(a["row_version"], mgr))).status_code == 400

    # un sistema che non è un archivio cartaceo non conserva i campi dell'archivio
    app = await c.post(ASSETS, json={"name": "Protocollo", "location_site": "CED", "archive_phase": "CURRENT"}, headers=mgr)
    assert (app.json()["location_site"], app.json()["archive_phase"], app.json()["environmental_risks"]) == (None, None, [])


@pytest.mark.asyncio
async def test_incomplete_paper_archive_is_flagged_until_completed(env, mgr):
    c = env["client"]
    cabinets = await _measure(env, mgr, "Armadi chiusi")
    a = (await c.post(ASSETS, json={"name": "Archivio corrente", "asset_type": "PAPER_ARCHIVE"}, headers=mgr)).json()
    assert await _paper_findings(env, mgr, a["id"]) == {"location", "custodian", "measures"}

    r = await c.patch(f"{ASSETS}/{a['id']}", json={**a, "location_site": "Sede centrale", "custodian_user_id": "u-custode",
                                                     "physical_measure_ids": [cabinets]}, headers=ifm(a["row_version"], mgr))
    assert r.status_code == 200, r.text
    assert await _paper_findings(env, mgr, a["id"]) == set()

    # custodito da una ditta di archiviazione: basta il custode esterno
    party = await c.post(f"{API}/parties", json={"legal_name": "Archivi Srl", "party_type": "SUPPLIER"}, headers=mgr)
    assert party.status_code == 201, party.text
    b = (await c.post(ASSETS, json={"name": "Archivio storico", "asset_type": "PAPER_ARCHIVE", "location_site": "Magazzino esterno",
                                     "supplier_id": party.json()["id"], "physical_measure_ids": [cabinets]}, headers=mgr)).json()
    assert await _paper_findings(env, mgr, b["id"]) == set()
