"""Tag (§78), campi custom (§77), note e commenti (§79)."""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
TAGS, CF, ENT, CM, FINDINGS = f"{API}/tags", f"{API}/custom-fields", f"{API}/entities", f"{API}/comments", f"{API}/findings"
ADMIN = _h(TA, "root", "admin")


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("op", R.OPERATOR, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("aud", R.AUDITOR, ScopeType.TENANT, None), ("ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a"),
        ("ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "op", "reader", "aud", "ref-a", "ref-b")} | {"b": _h(TB, "b-mgr")}


async def _tag(env, who, name="PNRR", **extra):
    r = await env["client"].post(TAGS, json={"name": name, **extra}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


def _proc(env, key="proc_a"):
    return f"{ENT}/processing_activity/{env['ids'][key]}"


async def _definition(env, who, **override):
    body = {"entity_type": "processing_activity", "key": "cig_progetto", "label": "CIG del progetto", "field_type": "TEXT", **override}
    r = await env["client"].post(CF, json=body, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


# ================================================================ tag

@pytest.mark.asyncio
async def test_tag_catalog_rules_and_permissions(env, w):
    c = env["client"]
    tag = await _tag(env, w["op"], "PNRR", color="#10b981")
    assert tag["name"] == "PNRR" and tag["active"] is True and tag["usage"] == 0
    for who in ("reader", "aud"):
        assert (await c.post(TAGS, json={"name": "X"}, headers=w[who])).status_code == 403
    assert (await c.post(TAGS, json={"name": " pnrr "}, headers=w["mgr"])).status_code == 400          # stesso nome, senza maiuscole
    assert (await c.post(TAGS, json={"name": "Colore", "color": "verde"}, headers=w["mgr"])).status_code == 400
    assert (await c.post(TAGS, json={"name": ""}, headers=w["mgr"])).status_code == 422
    assert (await c.post(TAGS, json={"name": "x" * 61}, headers=w["mgr"])).status_code == 422

    assert [t["name"] for t in (await c.get(TAGS, headers=w["reader"])).json()] == ["PNRR"]
    assert (await c.get(TAGS, headers=w["b"])).json() == []

    url = f"{TAGS}/{tag['id']}"
    body = {"name": "PNRR 2026", "color": "#3b82f6", "description": "Progetti PNRR"}
    assert (await c.patch(url, json=body, headers=ifm(tag["row_version"], w["op"]))).status_code == 403     # il catalogo lo cura il governo
    assert (await c.patch(url, json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(tag["row_version"] + 4, w["mgr"]))).status_code == 409
    changed = await c.patch(url, json=body, headers=ifm(tag["row_version"], w["mgr"]))
    assert changed.status_code == 200 and changed.json()["name"] == "PNRR 2026"
    assert (await c.patch(f"{TAGS}/nessuno", json=body, headers=ifm(1, w["mgr"]))).status_code == 404
    assert (await c.patch(url, json=body, headers=ifm(1, w["b"]))).status_code == 404

    other = await _tag(env, w["mgr"], "Minori")
    assert (await c.patch(f"{TAGS}/{other['id']}", json={"name": "pnrr 2026"}, headers=ifm(other["row_version"], w["mgr"]))).status_code == 400
    assert (await c.delete(f"{TAGS}/{other['id']}", headers=w["op"])).status_code == 403
    assert (await c.delete(f"{TAGS}/{other['id']}", headers=w["mgr"])).status_code == 204


@pytest.mark.asyncio
async def test_assigning_tags_follows_the_visibility_of_the_element(env, w):
    c, ids = env["client"], env["ids"]
    pnrr, minori = await _tag(env, w["mgr"], "PNRR"), await _tag(env, w["mgr"], "Minori")
    url = f"{_proc(env)}/tags"

    assert (await c.put(url, json={"tag_ids": [pnrr["id"]]}, headers=w["ref-a"])).status_code == 200
    assert (await c.put(url, json={"tag_ids": [pnrr["id"]]}, headers=w["ref-b"])).status_code == 403     # elemento di un'altra struttura
    assert (await c.put(url, json={"tag_ids": [pnrr["id"]]}, headers=w["aud"])).status_code == 403       # può vederlo, non assegnare tag
    assert (await c.put(url, json={"tag_ids": [pnrr["id"]]}, headers=w["reader"])).status_code == 403
    assert (await c.put(url, json={"tag_ids": [pnrr["id"]]}, headers=w["b"])).status_code == 404
    assert (await c.put(f"{ENT}/processing_activity/{ids['proc_x']}/tags", json={"tag_ids": []}, headers=w["mgr"])).status_code == 404   # altro ente
    assert (await c.put(f"{ENT}/widget/{ids['proc_a']}/tags", json={"tag_ids": []}, headers=w["mgr"])).status_code == 404
    assert (await c.put(url, json={"tag_ids": ["nessuno"]}, headers=w["mgr"])).status_code == 400
    other_tenant = await _tag(env, w["b"], "Altro ente")
    assert (await c.put(url, json={"tag_ids": [other_tenant["id"]]}, headers=w["mgr"])).status_code == 400

    # l'insieme si sostituisce; l'assegnazione è ripetibile senza effetti
    done = await c.put(url, json={"tag_ids": [pnrr["id"], minori["id"], pnrr["id"]]}, headers=w["op"])
    assert sorted(t["name"] for t in done.json()) == ["Minori", "PNRR"]
    assert [t["name"] for t in (await c.put(url, json={"tag_ids": [minori["id"]]}, headers=w["mgr"])).json()] == ["Minori"]

    usage = {t["name"]: t["usage"] for t in (await c.get(TAGS, headers=w["mgr"])).json()}
    assert usage == {"Minori": 1, "PNRR": 0}
    assert (await c.delete(f"{TAGS}/{minori['id']}", headers=w["mgr"])).status_code == 400                # in uso: si disattiva

    # un tag disattivato resta dove è, ma non si assegna più
    off = await c.patch(f"{TAGS}/{pnrr['id']}", json={"name": "PNRR", "active": False}, headers=ifm(pnrr["row_version"], w["mgr"]))
    assert off.status_code == 200
    assert (await c.put(url, json={"tag_ids": [minori["id"], pnrr["id"]]}, headers=w["mgr"])).status_code == 400
    many = [(await _tag(env, w["mgr"], f"t{i}"))["id"] for i in range(21)]
    assert (await c.put(url, json={"tag_ids": many}, headers=w["mgr"])).status_code == 400


@pytest.mark.asyncio
async def test_finding_elements_by_tag_shows_only_what_the_user_can_see(env, w):
    c, ids = env["client"], env["ids"]
    tag = await _tag(env, w["mgr"], "Cloud")
    asset = (await c.post(f"{API}/assets", json={"name": "Gestionale", "asset_type": "APPLICATION"}, headers=w["mgr"])).json()
    for url in (f"{_proc(env)}/tags", f"{_proc(env, 'proc_b')}/tags", f"{ENT}/asset/{asset['id']}/tags"):
        assert (await c.put(url, json={"tag_ids": [tag["id"]]}, headers=w["mgr"])).status_code == 200

    everything = (await c.get(f"{TAGS}/{tag['id']}/entities", headers=w["mgr"])).json()
    assert sorted((e["entity_type"], e["entity_id"]) for e in everything) == sorted([
        ("processing_activity", ids["proc_a"]), ("processing_activity", ids["proc_b"]), ("asset", asset["id"])])
    assert all(e["code"] and e["title"] for e in everything)
    a_only = (await c.get(f"{TAGS}/{tag['id']}/entities", headers=w["ref-a"])).json()
    assert sorted(e["entity_type"] for e in a_only) == ["asset", "processing_activity"]                   # il registro condiviso resta visibile
    assert [e["entity_id"] for e in a_only if e["entity_type"] == "processing_activity"] == [ids["proc_a"]]
    assert (await c.get(f"{TAGS}/{tag['id']}/entities", headers=w["b"])).status_code == 404

    # le annotazioni di un elemento riportano i suoi tag
    ann = (await c.get(f"{_proc(env)}/annotations", headers=w["ref-a"])).json()
    assert [t["name"] for t in ann["tags"]] == ["Cloud"] and ann["entity"]["entity_id"] == ids["proc_a"]
    assert (await c.get(f"{_proc(env, 'proc_b')}/annotations", headers=w["ref-a"])).status_code == 403


# ================================================================ campi custom

@pytest.mark.asyncio
async def test_definitions_are_limited_typed_and_administered_by_governance(env, w):
    c = env["client"]
    d = await _definition(env, w["mgr"])
    assert d["key"] == "cig_progetto" and d["required"] is False and d["active"] is True
    for who in ("op", "ref-a", "dpo", "aud"):
        assert (await c.post(CF, json={"entity_type": "asset", "key": "x1", "label": "X", "field_type": "TEXT"}, headers=w[who])).status_code == 403
    assert (await c.get(CF, headers=w["reader"])).status_code == 200                                    # tutti le vedono

    base = {"entity_type": "processing_activity", "label": "Etichetta", "field_type": "TEXT"}
    for bad_key in ("Maiuscola", "1inizia", "a", "con spazio", "x" * 41):
        assert (await c.post(CF, json={**base, "key": bad_key}, headers=w["mgr"])).status_code in (400, 422)
    assert (await c.post(CF, json={**base, "key": "cig_progetto"}, headers=w["mgr"])).status_code == 400
    assert (await c.post(CF, json={**base, "entity_type": "data_breach", "key": "nota"}, headers=w["mgr"])).status_code == 400   # dati sensibili
    assert (await c.post(CF, json={**base, "entity_type": "widget", "key": "nota"}, headers=w["mgr"])).status_code == 400
    assert (await c.post(CF, json={**base, "key": "scelta", "field_type": "SELECT"}, headers=w["mgr"])).status_code == 400          # senza opzioni
    assert (await c.post(CF, json={**base, "key": "scelta", "field_type": "SELECT", "options": ["a", "a", " "]}, headers=w["mgr"])).status_code == 201
    assert (await c.get(f"{CF}/types", headers=w["op"])).json().count("asset") == 1

    for i in range(18):
        assert (await c.post(CF, json={**base, "key": f"campo_{i:02d}"}, headers=w["mgr"])).status_code == 201
    over = await c.post(CF, json={**base, "key": "troppo"}, headers=w["mgr"])
    assert over.status_code == 400 and "20" in over.json()["detail"]                                # i campi custom non sostituiscono il modello
    assert (await c.post(CF, json={**base, "entity_type": "asset", "key": "troppo"}, headers=w["mgr"])).status_code == 201     # il limite è per tipo
    assert (await c.get(CF, params={"entity_type": "asset"}, headers=w["mgr"])).json()[0]["key"] == "troppo"
    assert (await c.get(CF, headers=w["b"])).json() == []

    url = f"{CF}/{d['id']}"
    body = {**base, "key": "cig_progetto", "label": "CIG", "required": True}
    assert (await c.patch(url, json=body, headers=w["mgr"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(d["row_version"] + 2, w["mgr"]))).status_code == 409
    assert (await c.patch(url, json={**body, "key": "altra"}, headers=ifm(d["row_version"], w["mgr"]))).status_code == 400
    assert (await c.patch(url, json={**body, "field_type": "NUMBER"}, headers=ifm(d["row_version"], w["mgr"]))).status_code == 400
    assert (await c.patch(url, json=body, headers=ifm(d["row_version"], w["op"]))).status_code == 403
    ok = await c.patch(url, json=body, headers=ifm(d["row_version"], w["mgr"]))
    assert ok.status_code == 200 and ok.json()["label"] == "CIG" and ok.json()["required"] is True
    assert (await c.delete(url, headers=w["op"])).status_code == 403
    assert (await c.delete(url, headers=w["mgr"])).status_code == 204


@pytest.mark.asyncio
async def test_values_are_validated_by_type_and_follow_edit_permissions(env, w):
    c = env["client"]
    for key, kind, extra in (("importo", "NUMBER", {}), ("scadenza", "DATE", {}), ("pnrr", "BOOLEAN", {}), ("ciclo", "SELECT", {"options": ["A", "B"]}),
                             ("note_int", "TEXT", {})):
        await _definition(env, w["mgr"], key=key, label=key.capitalize(), field_type=kind, **extra)
    url = f"{_proc(env)}/custom-fields"

    ok = await c.put(url, json={"values": {"importo": 1500.5, "scadenza": "2027-03-31", "pnrr": True, "ciclo": "A", "note_int": "  nota  "}}, headers=w["ref-a"])
    assert ok.status_code == 200, ok.text
    by_key = {f["key"]: f["value"] for f in ok.json()}
    assert by_key == {"importo": 1500.5, "scadenza": "2027-03-31", "pnrr": True, "ciclo": "A", "note_int": "nota"}

    for bad in ({"importo": "tanti"}, {"importo": True}, {"scadenza": "31/03/2027"}, {"pnrr": "si"}, {"ciclo": "C"}, {"note_int": "x" * 1001},
                {"inesistente": 1}):
        assert (await c.put(url, json={"values": bad}, headers=w["ref-a"])).status_code == 400, bad
    # i valori validi restano quelli di prima; un valore vuoto cancella
    assert {f["key"]: f["value"] for f in (await c.get(f"{_proc(env)}/annotations", headers=w["ref-a"])).json()["custom_fields"]}["importo"] == 1500.5
    cleared = await c.put(url, json={"values": {"importo": None, "ciclo": ""}}, headers=w["ref-a"])
    assert {f["key"]: f["value"] for f in cleared.json()}["importo"] is None and {f["key"]: f["value"] for f in cleared.json()}["ciclo"] is None

    # scrive chi può modificare l'elemento sulla sua struttura
    assert (await c.put(url, json={"values": {"pnrr": False}}, headers=w["ref-b"])).status_code == 403
    for who in ("aud", "reader"):
        assert (await c.put(url, json={"values": {"pnrr": False}}, headers=w[who])).status_code == 403
        assert (await c.get(f"{_proc(env)}/annotations", headers=w[who])).status_code == 200              # ma li vedono
    assert (await c.put(url, json={"values": {"pnrr": False}}, headers=w["b"])).status_code == 404
    assert (await c.put(f"{ENT}/data_breach/{env['ids']['proc_a']}/custom-fields", json={"values": {}}, headers=w["mgr"])).status_code == 400
    assert (await c.put(f"{ENT}/processing_activity/{env['ids']['proc_x']}/custom-fields", json={"values": {}}, headers=w["mgr"])).status_code == 404

    async with env["maker"]() as db:                                                                     # i valori (dati potenzialmente personali) non vanno in audit
        logs = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "custom_field"))).scalars().all()
    raw = json.dumps([log.new_values for log in logs])
    assert "nota" not in raw and "1500" not in raw and "keys" in raw


@pytest.mark.asyncio
async def test_required_fields_options_deactivation_and_the_rule(env, w):
    c, ids = env["client"], env["ids"]
    text = await _definition(env, w["mgr"], key="cig_progetto", label="CIG", required=True)
    pick = await _definition(env, w["mgr"], key="ciclo", label="Ciclo", field_type="SELECT", options=["A", "B"])
    url = f"{_proc(env)}/custom-fields"

    # un campo obbligatorio va compilato: non si può salvare altro lasciandolo vuoto
    assert (await c.put(url, json={"values": {"ciclo": "A"}}, headers=w["ref-a"])).status_code == 400
    findings = (await c.get(FINDINGS, params={"code": "CUSTOM_FIELD_REQUIRED_MISSING"}, headers=w["mgr"])).json()
    assert {f["entity_id"] for f in findings} == {ids["proc_a"], ids["proc_b"]} and {f["severity"] for f in findings} == {"LOW"}
    assert [f["entity_id"] for f in (await c.get(FINDINGS, params={"code": "CUSTOM_FIELD_REQUIRED_MISSING"}, headers=w["ref-b"])).json()] == [ids["proc_b"]]

    assert (await c.put(url, json={"values": {"cig_progetto": "ABC1234567", "ciclo": "B"}}, headers=w["ref-a"])).status_code == 200
    assert [f["entity_id"] for f in (await c.get(FINDINGS, params={"code": "CUSTOM_FIELD_REQUIRED_MISSING"}, headers=w["mgr"])).json()] == [ids["proc_b"]]
    assert (await c.put(url, json={"values": {"cig_progetto": None}}, headers=w["ref-a"])).status_code == 400       # non si svuota un obbligatorio

    # un'opzione in uso non si toglie; si può aggiungerne
    body = {"entity_type": "processing_activity", "key": "ciclo", "label": "Ciclo", "field_type": "SELECT"}
    assert (await c.patch(f"{CF}/{pick['id']}", json={**body, "options": ["A"]}, headers=ifm(pick["row_version"], w["mgr"]))).status_code == 400
    more = await c.patch(f"{CF}/{pick['id']}", json={**body, "options": ["A", "B", "C"]}, headers=ifm(pick["row_version"], w["mgr"]))
    assert more.status_code == 200 and more.json()["options"] == ["A", "B", "C"]
    assert (await c.delete(f"{CF}/{pick['id']}", headers=w["mgr"])).status_code == 400                             # ha valori

    # disattivare: il valore resta visibile ma non si modifica, e non si pretende più
    off = await c.patch(f"{CF}/{text['id']}", json={**{"entity_type": "processing_activity", "key": "cig_progetto", "field_type": "TEXT"},
                                                     "label": "CIG", "required": True, "active": False}, headers=ifm(text["row_version"], w["mgr"]))
    assert off.status_code == 200
    fields = {f["key"]: f for f in (await c.get(f"{_proc(env)}/annotations", headers=w["ref-a"])).json()["custom_fields"]}
    assert fields["cig_progetto"]["value"] == "ABC1234567" and fields["cig_progetto"]["active"] is False
    assert (await c.put(url, json={"values": {"cig_progetto": "nuovo"}}, headers=w["ref-a"])).status_code == 400
    assert "cig_progetto" not in {f["key"] for f in (await c.get(f"{_proc(env, 'proc_b')}/annotations", headers=w["mgr"])).json()["custom_fields"]}
    assert (await c.get(FINDINGS, params={"code": "CUSTOM_FIELD_REQUIRED_MISSING"}, headers=w["mgr"])).json() == []


# ================================================================ commenti

async def _comment(env, who, body="Da verificare con l'ufficio", visibility="TEAM", key="proc_a"):
    return await env["client"].post(f"{_proc(env, key)}/comments", json={"body": body, "visibility": visibility}, headers=who)


async def _visible_bodies(env, who, key="proc_a"):
    r = await env["client"].get(f"{_proc(env, key)}/annotations", headers=who)
    assert r.status_code == 200, r.text
    return sorted(c["visibility"] for c in r.json()["comments"])


@pytest.mark.asyncio
async def test_comment_visibility_levels(env, w):
    c = env["client"]
    for visibility in ("TEAM", "DPO_ONLY", "PRIVATE", "ALL_AUTHORIZED"):
        assert (await _comment(env, w["ref-a"], visibility=visibility)).status_code == 201

    # chi lavora sull'elemento (TEAM), il DPO (DPO_ONLY), l'autore (PRIVATE), chiunque lo veda (ALL_AUTHORIZED)
    assert await _visible_bodies(env, w["ref-a"]) == ["ALL_AUTHORIZED", "DPO_ONLY", "PRIVATE", "TEAM"]        # è l'autore
    assert await _visible_bodies(env, w["mgr"]) == ["ALL_AUTHORIZED", "TEAM"]
    assert await _visible_bodies(env, w["op"]) == ["ALL_AUTHORIZED", "TEAM"]
    assert await _visible_bodies(env, w["dpo"]) == ["ALL_AUTHORIZED", "DPO_ONLY", "TEAM"]                    # il DPO ha anche il permesso di modifica
    assert await _visible_bodies(env, w["aud"]) == ["ALL_AUTHORIZED"]
    assert await _visible_bodies(env, w["reader"]) == ["ALL_AUTHORIZED"]
    assert await _visible_bodies(env, ADMIN) == ["ALL_AUTHORIZED", "TEAM"]                                   # l'amministratore non è il DPO
    assert (await c.get(f"{_proc(env)}/annotations", headers=w["ref-b"])).status_code == 403                 # fuori scope
    assert (await c.get(f"{_proc(env)}/annotations", headers=w["b"])).status_code == 404

    # un commento DPO_ONLY di un altro autore: il DPO lo legge, gli altri no
    assert (await _comment(env, w["mgr"], "Nota per il DPO", "DPO_ONLY")).status_code == 201
    assert "DPO_ONLY" in await _visible_bodies(env, w["dpo"])
    assert (await _visible_bodies(env, w["ref-a"])).count("DPO_ONLY") == 1                                    # solo il proprio
    assert (await _visible_bodies(env, w["mgr"])).count("DPO_ONLY") == 1


@pytest.mark.asyncio
async def test_comment_creation_edit_and_deletion(env, w):
    c = env["client"]
    assert (await _comment(env, w["reader"])).status_code == 403                                             # sola lettura non commenta
    assert (await _comment(env, w["ref-b"])).status_code == 403
    assert (await _comment(env, w["ref-a"], "   ")).status_code in (400, 422)
    assert (await _comment(env, w["ref-a"], "x" * 4001)).status_code in (400, 422)
    assert (await c.post(f"{ENT}/widget/{env['ids']['proc_a']}/comments", json={"body": "x"}, headers=w["mgr"])).status_code == 404
    aud = await _comment(env, w["aud"], "Osservazione dell'auditor", "ALL_AUTHORIZED")
    assert aud.status_code == 201

    mine = (await _comment(env, w["ref-a"], "Primo testo", "TEAM")).json()
    assert mine["own"] is True and mine["author_id"] == "ref-a" and mine["edited_at"] is None
    url = f"{CM}/{mine['id']}"
    body = {"body": "Testo corretto", "visibility": "ALL_AUTHORIZED"}
    assert (await c.patch(url, json=body, headers=w["ref-a"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(mine["row_version"] + 3, w["ref-a"]))).status_code == 409
    assert (await c.patch(url, json=body, headers=ifm(mine["row_version"], w["mgr"]))).status_code == 403     # neppure il governo modifica gli altri
    assert (await c.patch(url, json=body, headers=ifm(mine["row_version"], w["ref-b"]))).status_code == 403
    edited = await c.patch(url, json=body, headers=ifm(mine["row_version"], w["ref-a"]))
    assert edited.status_code == 200 and edited.json()["body"] == "Testo corretto" and edited.json()["edited_at"]
    assert (await c.patch(f"{CM}/nessuno", json=body, headers=ifm(1, w["ref-a"]))).status_code == 404
    assert (await c.patch(url, json=body, headers=ifm(1, w["b"]))).status_code == 404

    # eliminare: l'autore il proprio; chi modera (governo) ciò che può leggere; nessuno ciò che non vede
    assert (await c.delete(f"{CM}/{aud.json()['id']}", headers=w["ref-a"])).status_code == 403
    private = (await _comment(env, w["ref-a"], "Riservato", "PRIVATE")).json()
    assert (await c.delete(f"{CM}/{private['id']}", headers=w["mgr"])).status_code == 404                     # il governo non vede i privati altrui
    assert (await c.delete(f"{CM}/{private['id']}", headers=w["ref-a"])).status_code == 204
    assert (await c.delete(f"{CM}/{aud.json()['id']}", headers=w["mgr"])).status_code == 204
    assert (await c.delete(f"{CM}/{aud.json()['id']}", headers=w["mgr"])).status_code == 404
    listed = (await c.get(f"{_proc(env)}/annotations", headers=w["mgr"])).json()["comments"]
    assert [x["body"] for x in listed] == ["Testo corretto"] and listed[0]["can_delete"] is True and listed[0]["own"] is False
    assert (await c.get(f"{_proc(env)}/annotations", headers=w["ref-a"])).json()["comments"][0]["can_delete"] is True


@pytest.mark.asyncio
async def test_comments_on_shared_registers_and_privacy_of_audit(env, w):
    c = env["client"]
    asset = (await c.post(f"{API}/assets", json={"name": "Gestionale", "asset_type": "APPLICATION"}, headers=w["mgr"])).json()
    url = f"{ENT}/asset/{asset['id']}/comments"
    secret = "Contiene il nome di una persona"
    assert (await c.post(url, json={"body": secret, "visibility": "TEAM"}, headers=w["mgr"])).status_code == 201
    ann = f"{ENT}/asset/{asset['id']}/annotations"
    assert len((await c.get(ann, headers=w["ref-b"])).json()["comments"]) == 1                             # registro condiviso: chi lavora sugli asset lo vede
    assert (await c.get(ann, headers=w["reader"])).json()["comments"] == []
    assert (await c.get(ann, headers=w["b"])).status_code == 404

    async with env["maker"]() as db:
        logs = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "comment"))).scalars().all()
    assert logs and secret not in json.dumps([[log.new_values, log.old_values] for log in logs])
    assert secret not in json.dumps((await c.get(f"{API}/outbox", headers=ADMIN)).json())
