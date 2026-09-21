"""Regole di conservazione (§21): validazione, scope, locking, effetto sull'approvazione e sul report art. 30."""
import pytest
import pytest_asyncio

from backend.models.privacypa_catalog import DataCategoryCatalog, GdprDataClass
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_processing import ProcessingStatus
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm, version_of

PROC = "/api/privacypa/processing-activities"
RULES = "/api/privacypa/retention-rules"
ADMIN = _h(TA, "root", "admin")

RULE = {
    "retention_value": 10, "retention_unit": "YEARS", "retention_criterion": "Termine di prescrizione ordinaria",
    "start_trigger": "cessazione del rapporto", "legal_reference": "art. 2946 c.c.", "disposal_method": "DELETION",
}


@pytest_asyncio.fixture
async def data(env):
    """Categoria di dati collegata al trattamento A; utenti con ruoli diversi."""
    maker, ids, grant = env["maker"], env["ids"], env["grant"]
    async with maker() as db:
        cat = DataCategoryCatalog(tenant_id=None, code="ANAG", name="Dati anagrafici", gdpr_class=GdprDataClass.ORDINARY)
        other = DataCategoryCatalog(tenant_id=None, code="FIN", name="Dati finanziari", gdpr_class=GdprDataClass.ORDINARY)
        db.add_all([cat, other])
        await db.flush()
        await ProcessingService(db).update_activity(
            TA, ids["proc_a"], "seed", title="Trattamento A", organizational_unit_id="unit-a",
            description="Descrizione", legal_basis_codes=["GDPR_6_1_E"], data_subject_ids=["subject-1"],
            data_category_ids=[cat.id],
        )
        await db.commit()
        out = {"cat": cat.id, "other_cat": other.id}
    await grant(TA, "mgr", R.PRIVACY_MANAGER)
    await grant(TA, "op-a", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await grant(TA, "op-b", R.OPERATOR, ScopeType.UNIT, "unit-b")
    await grant(TA, "reader", R.READ_ONLY)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return out


def _url(env):
    return f"{PROC}/{env['ids']['proc_a']}/retention-rules"


async def _create(env, who=None, **override):
    return await env["client"].post(_url(env), json={**RULE, **override}, headers=who or _h(TA, "mgr"))


@pytest.mark.asyncio
async def test_create_list_and_expose_on_the_processing(env, data):
    c = env["client"]
    created = await _create(env, data_category_id=data["cat"], record_class="fascicolo")
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["period_text"] == "10 anni da: cessazione del rapporto" and rule["disposal_text"] == "cancellazione"
    assert rule["created_by"] == "mgr" and rule["row_version"] == 1

    listed = (await c.get(_url(env), headers=_h(TA, "reader"))).json()
    assert [r["id"] for r in listed] == [rule["id"]]
    got = await c.get(f"{RULES}/{rule['id']}", headers=_h(TA, "reader"))
    assert got.headers["etag"] == '"1"'
    full = (await c.get(f"{PROC}/{env['ids']['proc_a']}", headers=_h(TA, "reader"))).json()
    assert [r["id"] for r in full["retention_rules"]] == [rule["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("override,fragment", [
    ({"retention_value": None}, "durata"),
    ({"retention_value": 0}, "durata"),
    ({"retention_value": 101}, "plausibile"),
    ({"retention_unit": "EVENT_BASED", "retention_value": 5}, "durata"),
    ({"retention_unit": "EVENT_BASED", "retention_value": None, "start_trigger": None}, "evento"),
    ({"retention_unit": "PERMANENT", "retention_value": None}, "cancellazione"),
    ({"retention_criterion": "   "}, "criterio"),
])
async def test_rule_validation(env, data, override, fragment):
    r = await _create(env, **override)
    assert r.status_code == 400, r.text
    assert fragment in r.json()["detail"]


@pytest.mark.asyncio
async def test_valid_variants_and_period_text(env, data):
    ok = await _create(env, retention_unit="EVENT_BASED", retention_value=None, start_trigger="chiusura del procedimento")
    assert ok.status_code == 201 and ok.json()["period_text"] == "fino a: chiusura del procedimento"
    perm = await _create(env, retention_unit="PERMANENT", retention_value=None, disposal_method="ARCHIVAL",
                         retention_criterion="Interesse storico (art. 89 GDPR)")
    assert perm.status_code == 201 and perm.json()["period_text"] == "conservazione illimitata"
    assert (await _create(env, retention_value=1, retention_unit="MONTHS", start_trigger=None)).json()["period_text"] == "1 mese"


@pytest.mark.asyncio
async def test_category_must_exist_and_belong_to_the_processing(env, data):
    unknown = await _create(env, data_category_id="does-not-exist")
    not_linked = await _create(env, data_category_id=data["other_cat"])
    assert unknown.status_code == 400 and "non trovata" in unknown.json()["detail"]
    assert not_linked.status_code == 400 and "non è associata" in not_linked.json()["detail"]


@pytest.mark.asyncio
async def test_update_uses_if_match_and_touches_the_processing(env, data):
    c = env["client"]
    rule = (await _create(env)).json()
    before = await version_of(c, f"{PROC}/{env['ids']['proc_a']}")
    body = {**RULE, "retention_value": 5}

    assert (await c.patch(f"{RULES}/{rule['id']}", json=body, headers=_h(TA, "mgr"))).status_code == 428
    assert (await c.patch(f"{RULES}/{rule['id']}", json=body, headers=ifm(9, _h(TA, "mgr")))).status_code == 409
    ok = await c.patch(f"{RULES}/{rule['id']}", json=body, headers=ifm(rule["row_version"], _h(TA, "mgr")))
    assert ok.status_code == 200 and ok.json()["retention_value"] == 5 and ok.json()["row_version"] == 2
    assert await version_of(c, f"{PROC}/{env['ids']['proc_a']}") > before


@pytest.mark.asyncio
async def test_changing_retention_invalidates_a_pending_approval(env, data):
    c, pid = env["client"], env["ids"]["proc_a"]
    mgr = _h(TA, "mgr")
    v = await version_of(c, f"{PROC}/{pid}")
    assert (await c.post(f"{PROC}/{pid}/status", json={"status": "IN_REVIEW"}, headers=ifm(v, mgr))).status_code == 200
    pending = (await c.get("/api/privacypa/approvals", params={"state": "PENDING"}, headers=mgr)).json()[0]
    assert pending["stale"] is False

    await _create(env)
    pending = (await c.get("/api/privacypa/approvals", params={"state": "PENDING"}, headers=mgr)).json()[0]
    assert pending["stale"] is True


@pytest.mark.asyncio
async def test_scope_and_permissions(env, data):
    c = env["client"]
    assert (await _create(env, who=_h(TA, "op-b"))).status_code == 403
    assert (await _create(env, who=_h(TA, "reader"))).status_code == 403
    assert (await _create(env, who=_h(TB, "b-mgr"))).status_code == 404  # trattamento di un altro ente
    created = await _create(env, who=_h(TA, "op-a"))
    assert created.status_code == 201
    rule_id = created.json()["id"]

    assert (await c.get(_url(env), headers=_h(TA, "op-b"))).status_code == 403
    assert (await c.get(f"{RULES}/{rule_id}", headers=_h(TA, "op-b"))).status_code == 403
    assert (await c.get(f"{RULES}/{rule_id}", headers=_h(TB, "b-mgr"))).status_code == 404
    assert (await c.patch(f"{RULES}/{rule_id}", json=RULE, headers=ifm(1, _h(TA, "op-b")))).status_code == 403
    assert (await c.delete(f"{RULES}/{rule_id}", headers=_h(TA, "op-b"))).status_code == 403
    assert (await c.delete(f"{RULES}/{rule_id}", headers=_h(TA, "reader"))).status_code == 403


@pytest.mark.asyncio
async def test_delete_is_logical_and_audited(env, data):
    c = env["client"]
    rule = (await _create(env)).json()
    assert (await c.delete(f"{RULES}/{rule['id']}", headers=_h(TA, "mgr"))).status_code == 204
    assert (await c.get(_url(env), headers=_h(TA, "mgr"))).json() == []
    assert (await c.get(f"{RULES}/{rule['id']}", headers=_h(TA, "mgr"))).status_code == 404
    events = (await c.get("/api/privacypa/audit", params={"entity_type": "retention_rule"}, headers=ADMIN)).json()
    assert sorted(e["action"] for e in events) == ["create", "delete"]


@pytest.mark.asyncio
async def test_art30_report_includes_retention(env, data):
    c = env["client"]
    await _create(env, data_category_id=data["cat"])
    body = (await c.get("/api/privacypa/reports/art30", headers=ADMIN)).json()
    assert body["unavailable_sections"] == []
    with_rule = next(r for r in body["processing_activities"] if r["id"] == env["ids"]["proc_a"])
    assert with_rule["retention_resolved"] == [{
        "category": "Dati anagrafici", "record_class": None, "period": "10 anni da: cessazione del rapporto",
        "criterion": "Termine di prescrizione ordinaria", "legal_reference": "art. 2946 c.c.", "disposal": "cancellazione",
    }]
    without = next(r for r in body["processing_activities"] if r["id"] == env["ids"]["proc_b"])
    assert without["retention_resolved"] == []

    csv_text = (await c.get("/api/privacypa/reports/art30", params={"format": "csv"}, headers=ADMIN)).text
    assert "Dati anagrafici: 10 anni da: cessazione del rapporto (Termine di prescrizione ordinaria) - cancellazione [art. 2946 c.c.]" in csv_text
    assert "non definita" in csv_text
    pdf = (await c.get("/api/privacypa/reports/art30", params={"format": "pdf"}, headers=ADMIN)).content
    assert b"Conservazione" in pdf


@pytest.mark.asyncio
async def test_finding_for_approved_processing_without_retention(env, data):
    c, maker, pid = env["client"], env["maker"], env["ids"]["proc_a"]
    async with maker() as db:
        svc = ProcessingService(db)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()
    findings = (await c.get("/api/privacypa/findings", params={"code": "RETENTION_MISSING"}, headers=_h(TA, "mgr"))).json()
    assert [f["entity_id"] for f in findings] == [pid] and findings[0]["severity"] == "MEDIUM"

    await _create(env)
    assert (await c.get("/api/privacypa/findings", params={"code": "RETENTION_MISSING"}, headers=_h(TA, "mgr"))).json() == []
