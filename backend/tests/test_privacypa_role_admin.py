"""Gestione dei ruoli privacy: elenco dell'ente, scadenza, modifica dell'ambito e chi può vedere i ruoli degli altri."""
from datetime import datetime, timedelta

import pytest

from backend.models.privacypa_identity import PrivacyRoleCatalog, PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
ADMIN = _h(TA, "root", "admin")


async def _catalog(env, *codes):
    async with env["maker"]() as db:
        for code in codes:
            db.add(PrivacyRoleCatalog(code=code, name=code.value))
        await db.commit()


@pytest.mark.asyncio
async def test_a_privacy_admin_sees_who_has_which_role(env):
    c = env["client"]
    await env["grant"](TA, "capo", R.TENANT_ADMIN)              # amministratore privacy, non del Portal
    await env["grant"](TA, "anna", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await env["grant"](TA, "bruno", R.DPO)
    await env["grant"](TB, "altro", R.DPO)
    capo = _h(TA, "capo")

    everyone = (await c.get(f"{API}/roles/assignments", headers=capo)).json()
    assert sorted(a["user_id"] for a in everyone) == ["anna", "bruno", "capo"]          # solo il proprio ente
    dpo = (await c.get(f"{API}/roles/assignments", params={"role_code": "DPO"}, headers=capo)).json()
    assert [a["user_id"] for a in dpo] == ["bruno"]
    unit = (await c.get(f"{API}/roles/assignments", params={"organizational_unit_id": "unit-a"}, headers=capo)).json()
    assert [a["user_id"] for a in unit] == ["anna"]

    # prima vedeva i ruoli degli altri solo un amministratore del Portal: chi li assegna deve poterli vedere
    assert (await c.get(f"{API}/users/anna/roles", headers=capo)).status_code == 200
    anna = _h(TA, "anna")
    assert (await c.get(f"{API}/users/anna/roles", headers=anna)).status_code == 200      # i propri sì
    assert (await c.get(f"{API}/users/bruno/roles", headers=anna)).status_code == 403
    assert (await c.get(f"{API}/roles/assignments", headers=anna)).status_code == 403
    # un amministratore del Portal che agisce da operatore non li vede
    assert (await c.get(f"{API}/users/anna/roles", headers={**ADMIN, "X-Active-Role": "OPERATOR"})).status_code == 403


@pytest.mark.asyncio
async def test_a_role_can_expire(env):
    c = env["client"]
    await _catalog(env, R.OPERATOR)
    tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)
    r = await c.post(f"{API}/roles/assign", json={"user_id": "anna", "role_code": "OPERATOR", "valid_to": tomorrow.isoformat()},
                     headers=ADMIN)
    assert r.status_code == 201, r.text
    assert r.json()["valid_to"] == tomorrow.isoformat()
    me = (await c.get(f"{API}/me/permissions", headers=_h(TA, "anna"))).json()
    assert me["permissions"]["notice.create"]["allowed"]

    past = (datetime.utcnow() - timedelta(days=1)).isoformat()
    late = await c.post(f"{API}/roles/assign", json={"user_id": "bruno", "role_code": "OPERATOR", "valid_to": past},
                        headers=ADMIN)
    assert late.status_code == 400 and "scadenza" in late.json()["detail"]


@pytest.mark.asyncio
async def test_an_assignment_changes_scope_and_expiry(env):
    c = env["client"]
    await _catalog(env, R.OPERATOR)
    created = (await c.post(f"{API}/roles/assign", json={"user_id": "anna", "role_code": "OPERATOR", "scope_type": "UNIT",
                                                          "organizational_unit_id": "unit-a"}, headers=ADMIN)).json()
    url = f"{API}/roles/{created['id']}"
    body = {"scope_type": "SUBTREE", "organizational_unit_id": "unit-b", "valid_to": "2030-12-31T23:59:59"}

    assert (await c.put(url, json=body, headers=ADMIN)).status_code == 428
    r = await c.put(url, json=body, headers=ifm(created["row_version"], ADMIN))
    assert r.status_code == 200, r.text
    updated = r.json()
    assert (updated["scope_type"], updated["organizational_unit_id"], updated["valid_to"]) == ("SUBTREE", "unit-b", "2030-12-31T23:59:59")
    assert updated["row_version"] == created["row_version"] + 1 and updated["id"] == created["id"]
    assert (await c.put(url, json=body, headers=ifm(created["row_version"], ADMIN))).status_code == 409

    # sull'intero ente la struttura non conta e la scadenza omessa si toglie
    r = await c.put(url, json={"scope_type": "TENANT", "organizational_unit_id": "unit-b"}, headers=ifm(updated["row_version"], ADMIN))
    assert r.status_code == 200 and r.json()["organizational_unit_id"] is None and r.json()["valid_to"] is None
    tenant = r.json()

    # struttura di un altro ente, ambito senza struttura, doppione: rifiutati
    assert (await c.put(url, json={"scope_type": "UNIT", "organizational_unit_id": "unit-x"},
                        headers=ifm(tenant["row_version"], ADMIN))).status_code == 400
    assert (await c.put(url, json={"scope_type": "UNIT"}, headers=ifm(tenant["row_version"], ADMIN))).status_code == 400
    other = (await c.post(f"{API}/roles/assign", json={"user_id": "anna", "role_code": "OPERATOR", "scope_type": "UNIT",
                                                        "organizational_unit_id": "unit-a"}, headers=ADMIN)).json()
    dup = await c.put(f"{API}/roles/{other['id']}", json={"scope_type": "TENANT"}, headers=ifm(other["row_version"], ADMIN))
    assert dup.status_code == 400 and "già esistente" in dup.json()["detail"]

    # chi non amministra i ruoli non li modifica; un'assegnazione di un altro ente non si trova
    await env["grant"](TA, "op", R.OPERATOR)
    assert (await c.put(url, json={"scope_type": "TENANT"}, headers=ifm(tenant["row_version"], _h(TA, "op")))).status_code == 403
    assert (await c.put(url, json={"scope_type": "TENANT"},
                        headers=ifm(tenant["row_version"], _h(TB, "root", "admin")))).status_code == 404

    log = (await c.get(f"{API}/audit", params={"entity_type": "user_role_assignment", "entity_id": created["id"]},
                       headers=ADMIN)).json()
    rows = log["items"] if isinstance(log, dict) else log
    assert [e["action"] for e in rows].count("update") == 2
