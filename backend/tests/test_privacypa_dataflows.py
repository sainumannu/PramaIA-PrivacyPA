"""Flussi dei dati (§20): validazione degli estremi, scope, locking, grafo e regola di coerenza con i collegamenti."""
import json
import io
import zipfile

import pytest
import pytest_asyncio

from backend.models.privacypa_assets import Asset
from backend.models.privacypa_catalog import DataCategoryCatalog, GdprDataClass
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_processing import ProcessingStatus
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
ADMIN = _h(TA, "root", "admin")


@pytest_asyncio.fixture
async def w(env):
    grant, c, ids = env["grant"], env["client"], env["ids"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("op-a", R.OPERATOR, ScopeType.UNIT, "unit-a"), ("op-b", R.OPERATOR, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    async with env["maker"]() as db:
        cat = DataCategoryCatalog(tenant_id=None, code="ANAG", name="Dati anagrafici", gdpr_class=GdprDataClass.ORDINARY)
        other_cat = DataCategoryCatalog(tenant_id=None, code="FIN", name="Dati finanziari", gdpr_class=GdprDataClass.ORDINARY)
        asset = Asset(tenant_id=TA, code="AS-000001", name="Gestionale")
        db.add_all([cat, other_cat, asset])
        await db.flush()
        await ProcessingService(db).update_activity(
            TA, ids["proc_a"], "seed", title="Trattamento A", organizational_unit_id="unit-a", description="Descrizione",
            legal_basis_codes=["GDPR_6_1_E"], data_subject_ids=["subject-1"], data_category_ids=[cat.id],
        )
        await db.commit()
        refs = {"cat": cat.id, "other_cat": other_cat.id, "asset": asset.id}
    party = (await c.post(f"{API}/parties", json={"legal_name": "Fornitore Cloud", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=_h(TA, "mgr"))).json()
    return {**refs, "party": party["id"], **{k: _h(TA, k) for k in ("mgr", "reader", "op-a", "op-b")}, "b": _h(TB, "b-mgr")}


def _url(env, suffix="", pid=None):
    return f"{API}/processing-activities/{pid or env['ids']['proc_a']}/data-flows{suffix}"


def _flow(w, **override):
    return {
        "source_type": "DATA_SUBJECT", "source_label": "Cittadini", "target_type": "ASSET", "target_id": w["asset"],
        "frequency": "DAILY", "channel": "WEB_FORM", "description": "Invio delle istanze", "data_category_ids": [w["cat"]], **override,
    }


async def _create(env, w, who=None, **override):
    return await env["client"].post(_url(env), json=_flow(w, **override), headers=who or w["mgr"])


@pytest.mark.asyncio
async def test_create_read_update_and_delete_a_flow(env, w):
    c = env["client"]
    created = await _create(env, w)
    assert created.status_code == 201, created.text
    flow = created.json()
    assert flow["source_label"] == "Cittadini" and flow["target_id"] == w["asset"] and flow["data_category_ids"] == [w["cat"]]
    assert flow["row_version"] == 1 and created.headers["etag"] == '"1"'
    assert [f["id"] for f in (await c.get(_url(env), headers=w["reader"])).json()] == [flow["id"]]
    assert (await c.get(f"{API}/data-flows/{flow['id']}", headers=w["reader"])).headers["etag"] == '"1"'

    body = _flow(w, description="Invio quotidiano delle istanze", frequency="REALTIME", data_category_ids=[])
    assert (await c.patch(f"{API}/data-flows/{flow['id']}", json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(f"{API}/data-flows/{flow['id']}", json=body, headers=ifm(9, w["mgr"]))).status_code == 409
    updated = await c.patch(f"{API}/data-flows/{flow['id']}", json=body, headers=ifm(1, w["mgr"]))
    assert updated.status_code == 200
    assert updated.json()["frequency"] == "REALTIME" and updated.json()["data_category_ids"] == [] and updated.json()["row_version"] == 2

    assert (await c.delete(f"{API}/data-flows/{flow['id']}", headers=w["reader"])).status_code == 403
    assert (await c.delete(f"{API}/data-flows/{flow['id']}", headers=w["mgr"])).status_code == 204
    assert (await c.get(_url(env), headers=w["mgr"])).json() == []
    events = (await c.get(f"{API}/audit", params={"entity_type": "data_flow"}, headers=ADMIN)).json()
    assert sorted(e["action"] for e in events) == ["create", "delete", "update"]


@pytest.mark.asyncio
@pytest.mark.parametrize("override,fragment", [
    ({"source_label": None}, "etichetta"),                                         # interessati senza etichetta
    ({"target_type": "EXTERNAL", "target_id": None, "target_label": "  "}, "etichetta"),
    ({"target_id": None}, "deve indicare"),                                        # sistema senza riferimento
    ({"target_id": "non-esiste"}, "non trovato"),
    ({"source_type": "EXTERNAL", "source_id": "xx", "source_label": "Ente"}, "non si riferisce"),
    ({"source_type": "ASSET", "source_id": "@asset", "source_label": None}, "coincidono"),
    ({"data_category_ids": ["@other"]}, "non è associata"),
    ({"data_category_ids": ["nessuna"]}, "non trovata"),
    ({"description": "   "}, "descrizione"),
])
async def test_flow_validation(env, w, override, fragment):
    override = {k: (w["asset"] if v == "@asset" else v) for k, v in override.items()}
    if override.get("data_category_ids") == ["@other"]:
        override["data_category_ids"] = [w["other_cat"]]
    r = await _create(env, w, **override)
    assert r.status_code in (400, 422), r.text
    assert fragment in json.dumps(r.json(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_referenced_endpoints_must_belong_to_the_tenant(env, w):
    async with env["maker"]() as db:
        foreign = Asset(tenant_id=TB, code="AS-000001", name="Sistema di B")
        db.add(foreign)
        await db.commit()
        foreign_id = foreign.id
    assert (await _create(env, w, target_id=foreign_id)).status_code == 400
    assert (await _create(env, w, source_type="PARTY", source_id=w["party"], source_label=None)).status_code == 201
    assert (await _create(env, w, source_type="ORGANIZATIONAL_UNIT", source_id="unit-a", source_label=None)).status_code == 201
    assert (await _create(env, w, source_type="ORGANIZATIONAL_UNIT", source_id="unit-x", source_label=None)).status_code == 400


@pytest.mark.asyncio
async def test_flows_follow_the_processing_scope_and_permissions(env, w):
    c, ids = env["client"], env["ids"]
    assert (await _create(env, w, who=w["op-a"])).status_code == 201
    assert (await _create(env, w, who=w["op-b"])).status_code == 403
    assert (await _create(env, w, who=w["reader"])).status_code == 403
    assert (await c.get(_url(env), headers=w["op-b"])).status_code == 403
    assert (await c.get(_url(env, "/graph"), headers=w["op-b"])).status_code == 403
    assert (await _create(env, w, who=w["b"])).status_code == 404                  # trattamento di un altro ente
    assert (await c.get(_url(env, pid=ids["proc_x"]), headers=w["mgr"])).status_code == 404
    flow = (await c.get(_url(env), headers=w["mgr"])).json()[0]
    assert (await c.get(f"{API}/data-flows/{flow['id']}", headers=w["op-b"])).status_code == 403
    assert (await c.get(f"{API}/data-flows/{flow['id']}", headers=w["b"])).status_code == 404
    assert (await c.patch(f"{API}/data-flows/{flow['id']}", json=_flow(w), headers=ifm(1, w["op-b"]))).status_code == 403


@pytest.mark.asyncio
async def test_flow_changes_update_the_processing_version(env, w):
    c = env["client"]
    url = f"{API}/processing-activities/{env['ids']['proc_a']}"
    before = await version_of(c, url)
    flow = (await _create(env, w)).json()
    after_create = await version_of(c, url)
    await c.delete(f"{API}/data-flows/{flow['id']}", headers=w["mgr"])
    assert before < after_create < await version_of(c, url)
    detail = (await c.get(url, headers=w["mgr"])).json()
    assert detail["data_flows"] == []


@pytest.mark.asyncio
async def test_graph_merges_nodes_and_resolves_names(env, w):
    c = env["client"]
    await _create(env, w)                                                                          # Cittadini -> Gestionale
    await _create(env, w, source_type="ASSET", source_id=w["asset"], source_label=None, target_type="PARTY",
                  target_id=w["party"], target_label=None, channel="API", description="Backup verso il fornitore")
    await _create(env, w, source_type="DATA_SUBJECT", source_label="cittadini", target_type="EXTERNAL", target_id=None, target_label="Prefettura",
                  description="Comunicazione", channel="PEC")
    graph = (await c.get(_url(env, "/graph"), headers=w["reader"])).json()
    labels = {n["label"] for n in graph["nodes"]}
    assert labels == {"Cittadini", "Gestionale", "Fornitore Cloud", "Prefettura"}     # "Cittadini" e "cittadini" sono lo stesso nodo
    assert len(graph["nodes"]) == 4 and len(graph["edges"]) == 3
    by_desc = {e["description"]: e for e in graph["edges"]}
    nodes = {n["key"]: n["label"] for n in graph["nodes"]}
    backup = by_desc["Backup verso il fornitore"]
    assert (nodes[backup["source"]], nodes[backup["target"]], backup["channel"]) == ("Gestionale", "Fornitore Cloud", "API")
    assert by_desc["Invio delle istanze"]["categories"] == ["Dati anagrafici"]


@pytest.mark.asyncio
async def test_rule_flags_endpoints_not_declared_by_the_processing(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()
    await _create(env, w)                                                                          # verso il sistema
    await _create(env, w, source_type="PARTY", source_id=w["party"], source_label=None, target_type="EXTERNAL", target_id=None, target_label="Ente terzo")

    async def findings():
        r = await c.get(f"{API}/findings", params={"code": "DATA_FLOW_UNLINKED_ENDPOINT"}, headers=w["mgr"])
        return sorted(f["params"]["endpoint"] for f in r.json())

    assert await findings() == ["Fornitore Cloud", "Gestionale"]
    url = f"{API}/processing-activities/{pid}"
    r = await c.put(f"{url}/relations", json={"assets": [{"asset_id": w["asset"]}]}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert r.status_code == 200
    assert await findings() == ["Fornitore Cloud"]
    await c.put(f"{url}/relations", json={"recipients": [{"party_id": w["party"]}]}, headers=ifm(await version_of(c, url), w["mgr"]))
    assert await findings() == []
    (rule,) = [r for r in (await c.get(f"{API}/compliance/rules", headers=w["mgr"])).json() if r["code"] == "DATA_FLOW_UNLINKED_ENDPOINT"]
    assert rule["default_severity"] == "LOW"


@pytest.mark.asyncio
async def test_flows_are_part_of_the_tenant_export(env, w):
    await _create(env, w)
    zf = zipfile.ZipFile(io.BytesIO((await env["client"].post(f"{API}/export/tenant", headers=ADMIN)).content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["tables"]["privacypa_data_flows"]["rows"] == 1
    assert manifest["tables"]["privacypa_data_flow_categories"]["rows"] == 1
    assert manifest["tables"]["privacypa_privacy_notices"]["rows"] == 0
