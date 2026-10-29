"""Collegamenti del trattamento (sistemi, responsabili, destinatari): If-Match, audit e effetto sull'approvazione."""
import pytest
import pytest_asyncio

from backend.models.privacypa_assets import Asset
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"


@pytest_asyncio.fixture
async def w(env):
    grant, c = env["grant"], env["client"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("op-a", R.OPERATOR, ScopeType.UNIT, "unit-a"), ("op-b", R.OPERATOR, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    async with env["maker"]() as db:
        asset = Asset(tenant_id=TA, code="AS-000001", name="Gestionale")
        db.add(asset)
        await db.commit()
        asset_id = asset.id
    party = (await c.post(f"{API}/parties", json={"legal_name": "Fornitore", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=_h(TA, "mgr"))).json()
    return {"mgr": _h(TA, "mgr"), "reader": _h(TA, "reader"), "op-a": _h(TA, "op-a"), "op-b": _h(TA, "op-b"),
            "b": _h(TB, "b-mgr"), "asset": asset_id, "party": party["id"]}


def _url(env, suffix=""):
    return f"{API}/processing-activities/{env['ids']['proc_a']}{suffix}"


async def _put(env, who, body, version=None, suffix="/relations"):
    v = await version_of(env["client"], _url(env)) if version is None else version
    return await env["client"].put(_url(env, suffix), json=body, headers=ifm(v, who))


@pytest.mark.asyncio
async def test_relations_require_if_match_and_return_the_new_version(env, w):
    c = env["client"]
    body = {"assets": [{"asset_id": w["asset"], "role": "STORAGE"}], "processors": [{"party_id": w["party"]}],
            "recipients": [{"party_id": w["party"], "communication_type": "DISCLOSURE"}]}
    assert (await c.put(_url(env, "/relations"), json=body, headers=w["mgr"])).status_code == 428
    v = await version_of(c, _url(env))
    assert (await _put(env, w["mgr"], body, version=v + 5)).status_code == 409

    ok = await _put(env, w["mgr"], body, version=v)
    assert ok.status_code == 200, ok.text
    assert ok.headers["etag"] == f'"{v + 1}"' and (await version_of(c, _url(env))) == v + 1
    data = ok.json()
    assert [a["asset_id"] for a in data["assets"]] == [w["asset"]]
    assert [p["party_id"] for p in data["processors"]] == [w["party"]]
    assert data["recipients"][0]["communication_type"] == "DISCLOSURE"
    # la stessa versione non vale più
    assert (await _put(env, w["mgr"], body, version=v)).status_code == 409


@pytest.mark.asyncio
async def test_unchanged_relations_do_not_bump_the_version_or_audit(env, w):
    c = env["client"]
    body = {"processors": [{"party_id": w["party"]}]}
    assert (await _put(env, w["mgr"], body)).status_code == 200
    v = await version_of(c, _url(env))
    again = await _put(env, w["mgr"], body, version=v)
    assert again.status_code == 200 and await version_of(c, _url(env)) == v
    events = (await c.get(f"{API}/audit", params={"entity_id": env["ids"]["proc_a"]}, headers=w["mgr"])).json()
    assert len([e for e in events if e["event_type"] == "processing_activity.relations.updated"]) == 1


@pytest.mark.asyncio
async def test_omitted_groups_are_left_untouched_and_changes_are_audited(env, w):
    c = env["client"]
    await _put(env, w["mgr"], {"assets": [{"asset_id": w["asset"]}], "processors": [{"party_id": w["party"]}]})
    only_recipients = await _put(env, w["mgr"], {"recipients": [{"party_id": w["party"]}]})
    data = only_recipients.json()
    assert len(data["assets"]) == 1 and len(data["processors"]) == 1 and len(data["recipients"]) == 1

    await _put(env, w["mgr"], {"processors": []})
    events = (await c.get(f"{API}/audit", params={"entity_id": env["ids"]["proc_a"]}, headers=w["mgr"])).json()
    last = next(e for e in events if e["event_type"] == "processing_activity.relations.updated")  # più recente
    assert list(last["old_values"]) == ["processors"] and last["old_values"]["processors"][0]["party_id"] == w["party"]
    assert last["new_values"] == {"processors": []} and last["actor_user_id"] == "mgr"


@pytest.mark.asyncio
async def test_changing_relations_invalidates_a_pending_approval(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    mgr = w["mgr"]
    v = await version_of(c, _url(env))
    assert (await c.post(_url(env, "/status"), json={"status": "IN_REVIEW"}, headers=ifm(v, mgr))).status_code == 200
    pending = (await c.get(f"{API}/approvals", params={"state": "PENDING"}, headers=mgr)).json()[0]
    assert pending["stale"] is False
    await _put(env, mgr, {"processors": [{"party_id": w["party"]}]})
    pending = (await c.get(f"{API}/approvals", params={"state": "PENDING"}, headers=mgr)).json()[0]
    assert pending["stale"] is True and pending["entity_id"] == pid


@pytest.mark.asyncio
async def test_legacy_endpoints_share_the_same_rules(env, w):
    c = env["client"]
    assert (await c.put(_url(env, "/processors"), json=[], headers=w["mgr"])).status_code == 428
    v = await version_of(c, _url(env))
    ok = await c.put(_url(env, "/processors"), json=[{"party_id": w["party"]}], headers=ifm(v, w["mgr"]))
    assert ok.status_code == 200 and ok.json()[0]["party_id"] == w["party"]
    assert await version_of(c, _url(env)) == v + 1


@pytest.mark.asyncio
async def test_relations_permissions_scope_and_validation(env, w):
    body = {"processors": [{"party_id": w["party"]}]}
    assert (await _put(env, w["reader"], body)).status_code == 403
    assert (await _put(env, w["op-b"], body)).status_code == 403          # struttura diversa
    assert (await env["client"].put(_url(env, "/relations"), json=body, headers=ifm(1, w["b"]))).status_code == 404
    assert (await _put(env, w["op-a"], body)).status_code == 200

    assert (await _put(env, w["mgr"], {"assets": [{"asset_id": "nope"}]})).status_code == 400
    assert (await _put(env, w["mgr"], {"recipients": [{"party_id": "nope"}]})).status_code == 400
    assert (await _put(env, w["mgr"], {"processors": [{"party_id": w["party"], "processor_agreement_id": "nope"}]})).status_code == 400
    # una richiesta non valida non lascia modifiche a metà
    assert len((await env["client"].get(_url(env), headers=w["mgr"])).json()["processors"]) == 1
