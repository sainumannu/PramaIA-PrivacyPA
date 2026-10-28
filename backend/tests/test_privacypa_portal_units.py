"""Strutture dall'organigramma del Portal: elenco, collegamento a quelle esistenti, creazione con le strutture sopra."""
from unittest.mock import AsyncMock

import pytest

from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.services import privacypa_directory as directory
from backend.services.privacypa_organization_service import guess_unit_type
from backend.tests.conftest import TA, _h

API = "/api/privacypa"
TREE = {"root": {"id": "root", "node_id": "ENTE", "name": "Comune", "members": [], "children": [
    {"id": "n-area", "node_id": "A01", "name": "Area Servizi al Cittadino", "members": [], "children": [
        {"id": "n-anag", "node_id": "A01.1", "name": "Ufficio Anagrafe", "members": [], "children": []},
        {"id": "n-unit", "node_id": "A01.2", "name": "unit-a", "members": [], "children": []},
    ]},
]}}


@pytest.fixture
def portal(monkeypatch):
    async def get(path):
        return TREE if path.endswith("/org/tree") else []

    monkeypatch.setattr(directory, "_get", get)
    monkeypatch.setattr(directory, "_users", {})
    monkeypatch.setattr(directory, "_org", {})


@pytest.mark.asyncio
async def test_portal_units_are_listed_without_the_root(portal):
    nodes = await directory.org_units(TA)
    assert [(n["id"], n["parent_id"], n["path"]) for n in nodes] == [
        ("n-area", None, "Area Servizi al Cittadino"),
        ("n-anag", "n-area", "Area Servizi al Cittadino › Ufficio Anagrafe"),
        ("n-unit", "n-area", "Area Servizi al Cittadino › unit-a"),
    ]
    assert nodes[1]["code"] == "A01.1"


@pytest.mark.asyncio
async def test_choosing_a_portal_unit_creates_it_with_its_parents_or_links_the_same_name(env, portal):
    c = env["client"]
    await env["grant"](TA, "op", R.OPERATOR)
    await env["grant"](TA, "reader", R.READ_ONLY)

    listed = await c.get(f"{API}/organizational-units/portal", headers=_h(TA, "reader"))
    assert listed.status_code == 200 and listed.json()["available"] is True
    assert all(n["unit_id"] is None for n in listed.json()["nodes"])

    assert (await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "n-anag"},
                         headers=_h(TA, "reader"))).status_code == 403

    r = await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "n-anag"}, headers=_h(TA, "op"))
    assert r.status_code == 200, r.text
    anag = r.json()
    assert anag["name"] == "Ufficio Anagrafe" and anag["code"] == "A01.1" and anag["unit_type"] == "UFFICIO"
    assert anag["portal_node_id"] == "n-anag"
    units = {u["id"]: u for u in (await c.get(f"{API}/organizational-units", headers=_h(TA, "op"))).json()}
    area = units[anag["parent_id"]]
    assert area["portal_node_id"] == "n-area" and area["unit_type"] == "AREA" and area["parent_id"] is None

    again = await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "n-anag"}, headers=_h(TA, "op"))
    assert again.json()["id"] == anag["id"]                                        # nessun doppione

    same_name = await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "n-unit"}, headers=_h(TA, "op"))
    assert same_name.json()["id"] == "unit-a" and same_name.json()["portal_node_id"] == "n-unit"   # collegata, non ricreata

    listed = {n["id"]: n["unit_id"] for n in (await c.get(f"{API}/organizational-units/portal", headers=_h(TA, "op"))).json()["nodes"]}
    assert listed == {"n-area": area["id"], "n-anag": anag["id"], "n-unit": "unit-a"}

    assert (await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "inventato"},
                         headers=_h(TA, "op"))).status_code == 404


@pytest.mark.asyncio
async def test_portal_units_when_the_portal_is_down(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "op", R.OPERATOR)
    monkeypatch.setattr(directory, "org_units", AsyncMock(side_effect=directory.DirectoryUnavailable("Portal giù")))
    listed = await c.get(f"{API}/organizational-units/portal", headers=_h(TA, "op"))
    assert listed.status_code == 200 and listed.json() == {"available": False, "nodes": [], "detail": "Portal giù"}
    assert (await c.post(f"{API}/organizational-units/from-portal", json={"node_id": "n-anag"},
                         headers=_h(TA, "op"))).status_code == 503


def test_unit_type_from_the_name():
    assert guess_unit_type("Settore Tributi").value == "SETTORE"
    assert guess_unit_type("Servizi Sociali").value == "SERVIZIO"
    assert guess_unit_type("Segreteria del Sindaco").value == "UNITA"
