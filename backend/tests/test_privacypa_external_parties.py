"""Soggetti esterni: identificazione senza ambiguità, durata dell'incarico di responsabili e contitolari, archivio."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.models.privacypa_parties import Party, PartyType
from backend.models.privacypa_processing import ProcessingStatus
from backend.services import privacypa_directory as directory
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import put_links, TA, _h, ifm
from backend.utils.tax_codes import check_identifiers, is_numeric_code, is_person_code

API = "/api/privacypa"
PARTIES = f"{API}/parties"
TODAY = date.today()
CF_ENTE = "80078750587"          # INPS
CF_SOCIETA = "01234567897"
CF_PERSONA = "RSSMRA80A01H501U"


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def mgr(env):
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    return _h(TA, "mgr")


async def _post(env, who, **body):
    return await env["client"].post(PARTIES, json={"legal_name": "Soggetto", **body}, headers=who)


async def _findings(env, who, code):
    r = await env["client"].get(f"{API}/findings", params={"code": code}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- identificazione

def test_tax_code_check_characters():
    assert is_person_code(CF_PERSONA) and not is_person_code("RSSMRA80A01H501X")
    assert is_numeric_code(CF_ENTE) and is_numeric_code(CF_SOCIETA)
    assert not is_numeric_code("01234567890") and not is_numeric_code("00000000000")
    assert check_identifiers("IT", True, " rssmra 80a01 h501u ", None) == (CF_PERSONA, None)
    assert check_identifiers("it", False, CF_SOCIETA, "IT01234567897") == (CF_SOCIETA, CF_SOCIETA)
    assert check_identifiers("DE", False, None, "DE123456789") == (None, "DE123456789")


@pytest.mark.asyncio
async def test_every_party_must_be_identifiable(env, mgr):
    missing = await _post(env, mgr, party_type="RECIPIENT")
    assert missing.status_code == 400 and "codice fiscale" in missing.json()["detail"]
    assert (await _post(env, mgr, party_type="RECIPIENT", tax_id="01234567890")).status_code == 400   # controllo errato
    assert (await _post(env, mgr, party_type="OTHER", natural_person=True, tax_id=CF_SOCIETA)).status_code == 400
    assert (await _post(env, mgr, party_type="SUPPLIER", country_code="DE")).status_code == 400

    person = await _post(env, mgr, legal_name="Mario Rossi", party_type="OTHER", natural_person=True, tax_id="rssmra80a01h501u")
    assert person.status_code == 201, person.text
    assert (person.json()["tax_id"], person.json()["natural_person"], person.json()["identified"]) == (CF_PERSONA, True, True)
    # una ditta individuale usa il codice fiscale del titolare
    assert (await _post(env, mgr, party_type="SUPPLIER", tax_id=CF_PERSONA)).status_code == 201
    foreign = await _post(env, mgr, party_type="RECIPIENT", country_code="de", vat_id="DE123456789")
    assert foreign.status_code == 201 and foreign.json()["country_code"] == "DE"

    # in modifica vale la stessa regola: non si toglie il codice fiscale
    p = person.json()
    r = await env["client"].patch(f"{PARTIES}/{p['id']}", json={"legal_name": "Mario Rossi", "party_type": "OTHER",
                                                                 "natural_person": True, "tax_id": None},
                                  headers=ifm(p["row_version"], mgr))
    assert r.status_code == 400


# ---------------------------------------------------------------- durata dell'incarico

@pytest.mark.asyncio
async def test_processor_needs_a_term_from_a_contract_or_a_date(env, mgr):
    c = env["client"]
    assert (await _post(env, mgr, party_type="PROCESSOR", tax_id=CF_SOCIETA)).status_code == 400
    assert (await _post(env, mgr, party_type="JOINT_CONTROLLER", tax_id=CF_ENTE)).status_code == 400
    bad = await _post(env, mgr, party_type="PROCESSOR", tax_id=CF_SOCIETA,
                      processor_profile={"appointment_start": iso(10), "appointment_end": iso(5)})
    assert bad.status_code == 400

    by_date = await _post(env, mgr, party_type="PROCESSOR", tax_id=CF_SOCIETA, processor_profile={"appointment_end": iso(400)})
    assert by_date.status_code == 201, by_date.text
    assert (by_date.json()["appointment"]["source"], by_date.json()["appointment"]["state"]) == ("DATE", "ACTIVE")

    # il contratto registrato insieme al soggetto: l'incarico dura quanto il contratto
    by_contract = await _post(env, mgr, legal_name="Gestionale Srl", party_type="PROCESSOR", tax_id=CF_SOCIETA,
                              new_contract={"title": "Gestionale protocollo", "cig": "Z1234567AB", "start_date": iso(-30), "end_date": iso(30)})
    assert by_contract.status_code == 201, by_contract.text
    party = by_contract.json()
    contracts = (await c.get(f"{API}/contracts", params={"party_id": party["id"]}, headers=mgr)).json()
    assert [(x["title"], x["status"], x["end_date"]) for x in contracts] == [("Gestionale protocollo", "ACTIVE", iso(30))]
    appointment = party["appointment"]
    assert (appointment["source"], appointment["until"], appointment["state"], appointment["contract_id"]) == (
        "CONTRACT", iso(30), "EXPIRING", contracts[0]["id"])

    # rinnovo del contratto: l'incarico lo segue
    contract = contracts[0]
    r = await c.patch(f"{API}/contracts/{contract['id']}", json={**{k: contract[k] for k in ("party_id", "title", "cig", "start_date", "status")},
                                                                    "end_date": iso(700)}, headers=ifm(contract["row_version"], mgr))
    assert r.status_code == 200, r.text
    again = (await c.get(f"{PARTIES}/{party['id']}", headers=mgr)).json()["appointment"]
    assert (again["until"], again["state"]) == (iso(700), "ACTIVE")

    # il contratto di un altro soggetto non vale
    other = by_date.json()
    r = await c.patch(f"{PARTIES}/{other['id']}", json={"legal_name": "Soggetto", "party_type": "PROCESSOR", "tax_id": CF_SOCIETA,
                                                         "processor_profile": {"appointment_contract_id": contract["id"]}},
                      headers=ifm(other["row_version"], mgr))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ceased_processors_go_to_the_archive_and_are_flagged_where_still_used(env, mgr):
    c, pid = env["client"], env["ids"]["proc_a"]
    party = (await _post(env, mgr, legal_name="Cloud Srl", party_type="PROCESSOR", tax_id=CF_SOCIETA,
                         processor_profile={"appointment_end": iso(400)})).json()
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()
    assert (await put_links(c, f"{API}/processing-activities/{pid}/processors", json=[{"party_id": party["id"]}], headers=mgr)).status_code == 200
    assert await _findings(env, mgr, "PROCESSOR_CEASED_IN_USE") == []

    ceased = await c.put(f"{PARTIES}/{party['id']}/cessation", json={"ceased_on": iso(0), "note": "Recesso anticipato"},
                         headers=ifm(party["row_version"], mgr))
    assert ceased.status_code == 200, ceased.text
    p = ceased.json()
    assert (p["appointment"]["state"], p["appointment"]["ceased_on"], p["appointment"]["cessation_note"]) == ("CEASED", iso(0), "Recesso anticipato")
    found = await _findings(env, mgr, "PROCESSOR_CEASED_IN_USE")
    assert [(f["entity_id"], f["severity"], f["params"]["party"]) for f in found] == [(pid, "HIGH", "Cloud Srl")]

    # una modifica dell'anagrafica (PATCH = PUT) non annulla la cessazione
    r = await c.patch(f"{PARTIES}/{p['id']}", json={"legal_name": "Cloud S.r.l.", "party_type": "PROCESSOR", "tax_id": CF_SOCIETA,
                                                     "processor_profile": {"appointment_end": iso(400)}},
                      headers=ifm(p["row_version"], mgr))
    assert r.status_code == 200 and r.json()["appointment"]["state"] == "CEASED"

    back = await c.put(f"{PARTIES}/{p['id']}/cessation", json={"ceased_on": None}, headers=ifm(r.json()["row_version"], mgr))
    assert back.status_code == 200 and back.json()["appointment"]["state"] == "ACTIVE"
    assert back.json()["appointment"]["cessation_note"] is None
    assert await _findings(env, mgr, "PROCESSOR_CEASED_IN_USE") == []

    # solo responsabili e contitolari hanno un incarico da cessare
    other = (await _post(env, mgr, party_type="RECIPIENT", tax_id=CF_ENTE)).json()
    assert (await c.put(f"{PARTIES}/{other['id']}/cessation", json={"ceased_on": iso(0)},
                        headers=ifm(other["row_version"], mgr))).status_code == 400


@pytest.mark.asyncio
async def test_term_expiry_and_incomplete_parties_are_flagged(env, mgr):
    expired = (await _post(env, mgr, legal_name="Vecchio Srl", party_type="PROCESSOR", tax_id=CF_SOCIETA,
                           processor_profile={"appointment_start": iso(-400), "appointment_end": iso(-1)})).json()
    assert expired["appointment"]["state"] == "CEASED"
    expiring = (await _post(env, mgr, legal_name="Breve Srl", party_type="PROCESSOR", tax_id=CF_SOCIETA,
                            processor_profile={"appointment_end": iso(20)})).json()
    assert expiring["appointment"]["state"] == "EXPIRING"
    found = await _findings(env, mgr, "APPOINTMENT_EXPIRING")
    assert [(f["entity_id"], f["due_date"]) for f in found] == [(expiring["id"], iso(20))]

    # soggetti registrati prima delle regole (o importati): senza codice fiscale e senza durata
    async with env["maker"]() as db:
        db.add(Party(id="legacy-1", tenant_id=TA, code="FO-900001", legal_name="Storico Srl", party_type=PartyType.PROCESSOR))
        await db.commit()
    from backend.services.privacypa_party_service import PartyService
    async with env["maker"]() as db:
        listed = {p["id"]: p for p in await PartyService(db).describe(TA, await PartyService(db).list_parties(TA))}
    assert listed["legacy-1"]["identified"] is False and listed["legacy-1"]["appointment"] is None
    incomplete = {(f["entity_id"], f["params"]["missing"]) for f in await _findings(env, mgr, "PARTY_DATA_INCOMPLETE")}
    assert ("legacy-1", "identifier") in incomplete and expired["id"] not in {e for e, _ in incomplete}


@pytest.mark.asyncio
async def test_external_people_can_be_chosen_even_when_the_portal_is_down(env, mgr, monkeypatch):
    c, admin = env["client"], _h(TA, "root", "admin")
    await _post(env, mgr, legal_name="Anna Bianchi", party_type="OTHER", natural_person=True, tax_id=CF_PERSONA)
    await _post(env, mgr, legal_name="Ente Srl", party_type="SUPPLIER", tax_id=CF_SOCIETA)       # non è una persona

    monkeypatch.setattr(directory, "people", AsyncMock(return_value=[{"id": "u1", "name": "Luca Neri", "email": None, "active": True, "units": []}]))
    up = (await c.get(f"{API}/authorizations/people", headers=admin)).json()
    assert up["available"] is True
    assert [(p["name"], p.get("external", False), p.get("tax_id")) for p in up["people"]] == [
        ("Luca Neri", False, None), ("Anna Bianchi", True, CF_PERSONA)]

    monkeypatch.setattr(directory, "people", AsyncMock(side_effect=directory.DirectoryUnavailable("Portal giù")))
    down = (await c.get(f"{API}/authorizations/people", headers=admin)).json()
    assert down["available"] is False and [p["name"] for p in down["people"]] == ["Anna Bianchi"]
