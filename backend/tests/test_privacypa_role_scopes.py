"""Assegnazione dei ruoli privacy: ambito dell'ente, di una struttura o di un sottoalbero; il singolo record non si assegna."""
import pytest

from backend.models.privacypa_identity import PrivacyRoleCatalog, PrivacyRoleCode as R
from backend.tests.conftest import TA, _h

API = "/api/privacypa"
ADMIN = _h(TA, "root", "admin")


@pytest.mark.asyncio
async def test_role_scopes(env):
    c = env["client"]
    async with env["maker"]() as db:
        db.add(PrivacyRoleCatalog(code=R.MANAGER, name="Dirigente"))
        await db.commit()

    def assign(user, scope, unit=None):
        return c.post(f"{API}/roles/assign", json={"user_id": user, "role_code": "MANAGER", "scope_type": scope,
                                                   "organizational_unit_id": unit}, headers=ADMIN)

    assert (await assign("anna", "UNIT")).status_code == 400              # struttura obbligatoria
    assert (await assign("anna", "RECORD")).status_code == 400            # non darebbe accesso a nulla
    r = await assign("anna", "SUBTREE", "unit-a")
    assert r.status_code == 201, r.text
    tenant = await assign("bruno", "TENANT", "unit-a")                    # sull'intero ente la struttura non conta
    assert tenant.status_code == 201 and tenant.json()["organizational_unit_id"] is None
