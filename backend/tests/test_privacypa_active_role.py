"""Ruolo attivo (header X-Active-Role, selettore della sidebar): restringe davvero i permessi, anche sul server."""
import pytest

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, _h, ifm

API = "/api/privacypa"
NOTICE = {"title": "Informativa", "owner_unit_id": "unit-a", "version_label": "1.0", "content": "Testo"}


def _as(headers: dict, role: str) -> dict:
    return {**headers, "X-Active-Role": role}


async def _me(c, headers) -> dict:
    r = await c.get(f"{API}/me/permissions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_a_user_with_several_roles_acts_with_the_chosen_one(env):
    c = env["client"]
    await env["grant"](TA, "anna", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await env["grant"](TA, "anna", R.DPO)
    anna = _h(TA, "anna")

    me = await _me(c, anna)
    assert me["available_roles"] == ["DPO", "OPERATOR"] and me["active_role"] is None
    assert me["permissions"]["notice.approve"]["allowed"] and me["permissions"]["notice.create"]["tenant_wide"]

    as_operator = await _me(c, _as(anna, "operator"))
    assert as_operator["active_role"] == "OPERATOR" and as_operator["available_roles"] == ["DPO", "OPERATOR"]
    assert not as_operator["permissions"]["notice.approve"]["allowed"]
    assert as_operator["permissions"]["notice.create"] == {"allowed": True, "tenant_wide": False, "unit_ids": ["unit-a"]}
    assert len(as_operator["assignments"]) == 2                      # i ruoli reali restano tutti visibili

    # anche il server applica il ruolo scelto: da operatore solo sulla propria struttura e senza approvare
    blocked = await c.post(f"{API}/notices", json={**NOTICE, "owner_unit_id": "unit-b"}, headers=_as(anna, "OPERATOR"))
    assert blocked.status_code == 403
    assert (await c.post(f"{API}/notices", json={**NOTICE, "owner_unit_id": "unit-b"}, headers=anna)).status_code == 201
    assert (await c.get(f"{API}/dpo/workspace", headers=_as(anna, "OPERATOR"))).status_code == 403
    assert (await c.get(f"{API}/dpo/workspace", headers=_as(anna, "DPO"))).status_code == 200

    roles = (await c.get(f"{API}/users/anna/roles", headers=_as(anna, "OPERATOR"))).json()
    assert sorted(r["role_code"] for r in roles) == ["DPO", "OPERATOR"]


@pytest.mark.asyncio
async def test_a_portal_admin_acting_with_a_role_loses_the_shortcut(env):
    c = env["client"]
    await env["grant"](TA, "root", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    admin = _h(TA, "root", "admin")

    me = await _me(c, admin)
    assert me["is_admin"] and me["is_portal_admin"]
    assert "DPO" in me["available_roles"] and "OPERATOR" in me["available_roles"]          # tutti i ruoli privacy
    assert not {"INTEGRATION", "INTEGRATION_HELPDESK"} & set(me["available_roles"])         # non gli account di servizio
    acting = await _me(c, _as(admin, "PRIVACY_REFERENT"))
    assert not acting["is_admin"] and acting["is_portal_admin"]
    assert not acting["permissions"]["policy.administer"]["allowed"]
    # il ruolo assegnato davvero vale con il suo scope
    assert acting["permissions"]["notice.create"] == {"allowed": True, "tenant_wide": False, "unit_ids": ["unit-a"]}
    assert (await c.get(f"{API}/policy/permissions", headers=_as(admin, "PRIVACY_REFERENT"))).status_code == 403
    assert (await c.get(f"{API}/policy/permissions", headers=admin)).status_code == 200


@pytest.mark.asyncio
async def test_a_portal_admin_can_act_with_a_role_not_assigned_on_the_whole_entity(env):
    c, admin = env["client"], _h(TA, "root", "admin")
    as_operator = await _me(c, _as(admin, "OPERATOR"))
    assert as_operator["active_role"] == "OPERATOR" and as_operator["assignments"] == []    # nessuna assegnazione salvata
    assert as_operator["permissions"]["notice.create"]["tenant_wide"]
    assert not as_operator["permissions"]["notice.approve"]["allowed"]
    assert (await c.post(f"{API}/notices", json={**NOTICE, "owner_unit_id": "unit-b"}, headers=_as(admin, "OPERATOR"))).status_code == 201
    assert (await c.get(f"{API}/dpo/workspace", headers=_as(admin, "OPERATOR"))).status_code == 403
    assert (await c.get(f"{API}/dpo/workspace", headers=_as(admin, "DPO"))).status_code == 200

    # il parere del DPO non si delega: serve il ruolo assegnato davvero
    await env["grant"](TA, "ref", R.PRIVACY_REFERENT)
    o = (await c.post(f"{API}/dpo/opinions", json={"entity_type": "processing_activity", "entity_id": env["ids"]["proc_a"]},
                      headers=_h(TA, "ref"))).json()
    issue = await c.post(f"{API}/dpo/opinions/{o['id']}/issue", json={"text": "Favorevole"},
                         headers=ifm(o["row_version"], _as(admin, "DPO")))
    assert issue.status_code == 403
    # un utente qualsiasi non ottiene ruoli non assegnati
    await env["grant"](TA, "op", R.OPERATOR)
    assert not (await _me(c, _as(_h(TA, "op"), "DPO")))["permissions"]["dpo.view"]["allowed"]


@pytest.mark.asyncio
async def test_the_active_role_only_restricts(env):
    c = env["client"]
    await env["grant"](TA, "op", R.OPERATOR)
    op = _h(TA, "op")
    # un ruolo valido ma non assegnato non concede nulla; uno inesistente è rifiutato
    none = await _me(c, _as(op, "TENANT_ADMIN"))
    assert not any(p["allowed"] for p in none["permissions"].values())
    assert (await c.post(f"{API}/notices", json=NOTICE, headers=_as(op, "TENANT_ADMIN"))).status_code == 403
    bad = await c.get(f"{API}/me/permissions", headers=_as(op, "CAPO"))
    assert bad.status_code == 400 and bad.json()["detail"] == "Ruolo attivo non valido"
