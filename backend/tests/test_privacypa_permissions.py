"""Matrice dei permessi per ente (§10.1) e permessi effettivi dell'utente."""
import pytest

from backend.auth.privacypa_permissions import (
    DEFAULT_MATRIX,
    LOCKED_PERMISSIONS,
    effective_roles,
    validate_overrides,
)
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm, version_of

BASE = "/api/privacypa/processing-activities"
MATRIX = "/api/privacypa/policy/permissions"
ADMIN_A = _h(TA, "root", "admin")
ADMIN_B = _h(TB, "root-b", "admin")

BODY = {
    "title": "Trattamento", "organizational_unit_id": "unit-a", "description": "Descrizione",
    "legal_basis_codes": ["GDPR_6_1_E"], "data_subject_ids": ["subject-1"], "data_category_ids": ["category-1"],
}


async def _matrix_version(c, who=ADMIN_A):
    return (await c.get(MATRIX, headers=who)).json()["row_version"]


async def _override(c, overrides, who=ADMIN_A, tenant_version=None):
    version = await _matrix_version(c, who) if tenant_version is None else tenant_version
    return await c.put(MATRIX, json={"overrides": overrides}, headers=ifm(version, who))


# ---------------------------------------------------------------- catalogo dei default

def test_default_matrix_keeps_historical_rules():
    assert R.DPO in DEFAULT_MATRIX["processing.approve"]
    assert R.PRIVACY_REFERENT not in DEFAULT_MATRIX["processing.approve"]  # esempio di §10
    assert R.OPERATOR in DEFAULT_MATRIX["processing.edit"]
    assert R.ICT not in DEFAULT_MATRIX["processing.edit"] and R.ICT in DEFAULT_MATRIX["asset.edit"]
    assert R.AUDITOR in DEFAULT_MATRIX["audit.view"] and R.OPERATOR not in DEFAULT_MATRIX["audit.view"]
    assert set(DEFAULT_MATRIX["policy.administer"]) == {R.SUPER_ADMIN, R.TENANT_ADMIN}


def test_every_override_keeps_the_admins_and_rejects_bad_input():
    clean = validate_overrides({"processing.approve": ["DPO"]})
    assert clean == {"processing.approve": ["SUPER_ADMIN", "TENANT_ADMIN", "DPO"]}
    # un override identico al default non viene memorizzato
    assert validate_overrides({"processing.approve": [r.value for r in DEFAULT_MATRIX["processing.approve"]]}) == {}
    with pytest.raises(ValueError):
        validate_overrides({"processing.approve": ["NOT_A_ROLE"]})
    with pytest.raises(ValueError):
        validate_overrides({"processing.fly": ["DPO"]})
    for locked in LOCKED_PERMISSIONS:
        with pytest.raises(ValueError):
            validate_overrides({locked: ["DPO"]})
    assert effective_roles("policy.administer", {"policy.administer": ["DPO"]}) == DEFAULT_MATRIX["policy.administer"]


# ---------------------------------------------------------------- azioni distinte per modulo

@pytest.mark.asyncio
async def test_read_only_and_auditor_cannot_write(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "reader", R.READ_ONLY)
    await grant(TA, "auditor", R.AUDITOR)
    reader, auditor = _h(TA, "reader"), _h(TA, "auditor")

    assert (await c.get(BASE, headers=reader)).status_code == 200
    assert (await c.post(BASE, json=BODY, headers=reader)).status_code == 403
    assert (await c.post("/api/privacypa/tasks", json={"title": "T"}, headers=reader)).status_code == 403
    assert (await c.get("/api/privacypa/audit", headers=reader)).status_code == 403
    assert (await c.get("/api/privacypa/audit", headers=auditor)).status_code == 200
    assert (await c.post(BASE, json=BODY, headers=auditor)).status_code == 403


@pytest.mark.asyncio
async def test_referent_can_edit_but_not_approve(env):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "ref", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    ref = _h(TA, "ref")
    version = await version_of(c, f"{BASE}/{ids['proc_a']}")
    assert (await c.post(f"{BASE}/{ids['proc_a']}/status", json={"status": "IN_REVIEW"}, headers=ifm(version, ref))).status_code == 200
    version = await version_of(c, f"{BASE}/{ids['proc_a']}")
    assert (await c.post(f"{BASE}/{ids['proc_a']}/status", json={"status": "APPROVED"}, headers=ifm(version, ref))).status_code == 403


# ---------------------------------------------------------------- override per ente

@pytest.mark.asyncio
async def test_override_changes_who_can_approve(env):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "mgr", R.MANAGER)
    await grant(TA, "dpo", R.DPO)
    mgr, dpo = _h(TA, "mgr"), _h(TA, "dpo")
    pid = ids["proc_a"]

    v = await version_of(c, f"{BASE}/{pid}")
    await c.post(f"{BASE}/{pid}/status", json={"status": "IN_REVIEW"}, headers=ifm(v, mgr))
    v = await version_of(c, f"{BASE}/{pid}")
    assert (await c.post(f"{BASE}/{pid}/status", json={"status": "APPROVED"}, headers=ifm(v, mgr))).status_code == 403

    # l'ente decide che l'approvazione spetta anche ai dirigenti e non più al DPO
    assert (await _override(c, {"processing.approve": ["MANAGER"]})).status_code == 200
    assert (await c.post(f"{BASE}/{pid}/status", json={"status": "APPROVED"}, headers=ifm(v, dpo))).status_code == 403
    done = await c.post(f"{BASE}/{pid}/status", json={"status": "APPROVED"}, headers=ifm(v, mgr))
    assert done.status_code == 200 and done.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_override_binds_write_scope_to_the_permission_roles(env):
    """Un ruolo abilitato altrove non estende lo scope di un ruolo che non ha più il permesso."""
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "mixed", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await grant(TA, "mixed", R.DPO, ScopeType.UNIT, "unit-b")
    mixed = _h(TA, "mixed")

    v = await version_of(c, f"{BASE}/{ids['proc_a']}")
    assert (await c.patch(f"{BASE}/{ids['proc_a']}", json=BODY, headers=ifm(v, mixed))).status_code == 200

    # ora solo il DPO può modificare: il ruolo OPERATOR su A non basta, e il DPO su B non vale su A
    assert (await _override(c, {"processing.edit": ["DPO"]})).status_code == 200
    v = await version_of(c, f"{BASE}/{ids['proc_a']}")
    assert (await c.patch(f"{BASE}/{ids['proc_a']}", json=BODY, headers=ifm(v, mixed))).status_code == 403
    vb = await version_of(c, f"{BASE}/{ids['proc_b']}")
    assert (await c.patch(f"{BASE}/{ids['proc_b']}", json={**BODY, "organizational_unit_id": "unit-b"}, headers=ifm(vb, mixed))).status_code == 200


@pytest.mark.asyncio
async def test_override_is_per_tenant(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "op-a", R.OPERATOR)
    await grant(TB, "op-b", R.OPERATOR)
    assert (await _override(c, {"processing.create": ["DPO"]})).status_code == 200
    assert (await c.post(BASE, json=BODY, headers=_h(TA, "op-a"))).status_code == 403
    created = await c.post(BASE, json={**BODY, "organizational_unit_id": "unit-x"}, headers=_h(TB, "op-b"))
    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_override_can_remove_view_and_locks_administration(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "reader", R.READ_ONLY)
    assert (await c.get(BASE, headers=_h(TA, "reader"))).status_code == 200
    assert (await _override(c, {"processing.view": ["DPO"]})).status_code == 200
    assert (await c.get(BASE, headers=_h(TA, "reader"))).status_code == 403
    # ripristino: un override vuoto riporta ai default
    assert (await _override(c, {})).status_code == 200
    assert (await c.get(BASE, headers=_h(TA, "reader"))).status_code == 200

    locked = await _override(c, {"policy.administer": ["DPO"]})
    assert locked.status_code == 400
    assert (await _override(c, {"unknown.thing": ["DPO"]})).status_code == 422
    assert (await _override(c, {"processing.view": ["WIZARD"]})).status_code == 400


# ---------------------------------------------------------------- amministrazione della policy

@pytest.mark.asyncio
async def test_only_admins_manage_policy(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "manager", R.PRIVACY_MANAGER)
    await grant(TA, "tenant-admin", R.TENANT_ADMIN)
    manager = _h(TA, "manager")
    assert (await c.get(MATRIX, headers=manager)).status_code == 403
    assert (await c.put(MATRIX, json={"overrides": {}}, headers=ifm(0, manager))).status_code == 403
    assert (await c.get("/api/privacypa/policy/segregation", headers=manager)).status_code == 403
    assert (await c.get(MATRIX, headers=_h(TA, "tenant-admin"))).status_code == 200


@pytest.mark.asyncio
async def test_policy_updates_use_optimistic_locking(env):
    c = env["client"]
    first = await c.get(MATRIX, headers=ADMIN_A)
    assert first.json()["row_version"] == 0 and first.headers["etag"] == '"0"'

    assert (await c.put(MATRIX, json={"overrides": {}}, headers=ADMIN_A)).status_code == 428
    saved = await _override(c, {"processing.approve": ["MANAGER"]}, tenant_version=0)
    assert saved.status_code == 200 and saved.json()["row_version"] == 1

    stale = await _override(c, {"processing.approve": ["DPO"]}, tenant_version=0)
    assert stale.status_code == 409 and stale.headers["etag"] == '"1"'
    assert (await _override(c, {"processing.approve": ["DPO"]}, tenant_version=1)).status_code == 200


@pytest.mark.asyncio
async def test_policy_changes_are_audited(env):
    c = env["client"]
    await _override(c, {"processing.approve": ["MANAGER"]})
    events = (await c.get("/api/privacypa/audit", params={"entity_type": "tenant_policy"}, headers=ADMIN_A)).json()
    assert [e["event_type"] for e in events] == ["policy.permissions.updated"]
    assert events[0]["new_values"]["permission_overrides"]["processing.approve"] == ["SUPER_ADMIN", "TENANT_ADMIN", "MANAGER"]
    assert events[0]["actor_user_id"] == "root"


@pytest.mark.asyncio
async def test_matrix_payload_lists_defaults_and_overrides(env):
    c = env["client"]
    await _override(c, {"processing.approve": ["MANAGER"]})
    payload = (await c.get(MATRIX, headers=ADMIN_A)).json()
    row = next(p for p in payload["permissions"] if p["code"] == "processing.approve")
    assert row["overridden"] is True and "MANAGER" in row["roles"] and "DPO" in row["default_roles"] and "DPO" not in row["roles"]
    assert next(p for p in payload["permissions"] if p["code"] == "policy.administer")["locked"] is True
    assert set(payload["roles"]) == {r.value for r in R}


# ---------------------------------------------------------------- permessi dell'utente corrente

@pytest.mark.asyncio
async def test_my_permissions_reflect_roles_scope_and_overrides(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "ref", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    ref = _h(TA, "ref")
    me = (await c.get("/api/privacypa/me/permissions", headers=ref)).json()
    perms = me["permissions"]
    assert me["is_admin"] is False and [a["role_code"] for a in me["assignments"]] == ["PRIVACY_REFERENT"]
    assert perms["processing.edit"] == {"allowed": True, "tenant_wide": False, "unit_ids": ["unit-a"]}
    assert perms["processing.approve"]["allowed"] is False
    assert perms["import.create"]["allowed"] is False and perms["policy.administer"]["allowed"] is False

    await _override(c, {"processing.approve": ["PRIVACY_REFERENT"]})
    assert (await c.get("/api/privacypa/me/permissions", headers=ref)).json()["permissions"]["processing.approve"]["allowed"] is True

    admin = (await c.get("/api/privacypa/me/permissions", headers=ADMIN_A)).json()
    assert admin["is_admin"] is True and all(p["allowed"] and p["tenant_wide"] for p in admin["permissions"].values())

    nobody = (await c.get("/api/privacypa/me/permissions", headers=_h(TA, "nobody"))).json()
    assert not any(p["allowed"] for p in nobody["permissions"].values())
