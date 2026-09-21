"""Autorizzazione di ogni rotta (§142) e isolamento tra enti (§141)."""
import inspect
import re
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio

from backend.auth.portal_jwt import get_current_user
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
ADMIN, ADMIN_B = _h(TA, "root", "admin"), _h(TB, "root", "admin")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Controlli fatti dentro il gestore o in una funzione di appoggio (`_guard`, `_entity`, ...): riconoscibili dal testo del gestore.
INLINE_CHECKS = ("assert_permission", "require_permission", "assert_permission_for_unit", "can_for_unit", "assert_role", "assert_effective_role",
                 "_scope(", "_authorized_scope", "_guard(", "_entity(", "_action_guard(", "_load(", "_locked(")
# POST che in realtà leggono (non modificano): le può usare anche chi ha solo la lettura.
READ_ONLY_POSTS = {"/api/privacypa/queries/run"}


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _walk(dep)


def _routes(app):
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/privacypa") and hasattr(route, "dependant"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, path, route


# ================================================================ analisi statica dell'applicazione vera

def test_every_route_requires_authentication_and_every_mutation_is_authorized():
    from backend.main import app

    calls = lambda route: list(_walk(route.dependant))
    anonymous = [(m, p) for m, p, r in _routes(app) if get_current_user not in calls(r) and not any(getattr(c, "permission_code", None) for c in calls(r))]
    assert anonymous == [], f"Rotte senza autenticazione: {anonymous}"

    unchecked = []
    for method, path, route in _routes(app):
        if method not in MUTATING or path in READ_ONLY_POSTS:
            continue
        dependency_level = any(getattr(c, "permission_code", None) and not getattr(c, "reads_only", False) for c in calls(route))
        if not dependency_level and not any(marker in inspect.getsource(route.endpoint) for marker in INLINE_CHECKS):
            unchecked.append((method, path))
    assert unchecked == [], f"Mutazioni senza alcun controllo di permesso riconoscibile: {unchecked}"


def test_permission_codes_used_by_routes_exist_in_the_matrix():
    from backend.auth.privacypa_permissions import DEFAULT_MATRIX
    from backend.main import app

    used = {c.permission_code for _, _, r in _routes(app) for c in _walk(r.dependant) if getattr(c, "permission_code", None)}
    assert used and used <= set(DEFAULT_MATRIX)


# ================================================================ chi non ha permessi non modifica nulla (dinamico)

def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest_asyncio.fixture
async def people(env):
    await env["grant"](TA, "reader", R.READ_ONLY, ScopeType.TENANT, None)
    return {"reader": _h(TA, "reader"), "nobody": _h(TA, "nobody")}      # sola lettura; nessun ruolo assegnato


@pytest.mark.asyncio
async def test_users_without_write_permissions_never_change_anything(env, people):
    app = env["client"]._transport.app
    c = env["client"]
    problems = []
    checked = 0
    for method, path, _ in _routes(app):
        if method not in MUTATING or path in READ_ONLY_POSTS:
            continue
        for who, headers in people.items():
            try:
                r = await c.request(method, _url(path), json={}, headers={**headers, "If-Match": '"1"'})
            except Exception as e:                                # un'eccezione non gestita è un difetto anche qui
                problems.append((who, method, path, f"eccezione {type(e).__name__}"))
                continue
            checked += 1
            if 200 <= r.status_code < 300 or r.status_code >= 500:
                problems.append((who, method, path, r.status_code))
    assert checked > 200
    assert problems == [], problems


@pytest.mark.asyncio
async def test_role_matrix_on_representative_writes_and_reads(env):
    """ruolo × azione × entità (§142): la matrice dei permessi predefinita si riflette sulle rotte, a livello ente."""
    from backend.auth.privacypa_permissions import DEFAULT_MATRIX

    c = env["client"]
    users = {}
    for role in R:
        if role in (R.SUPER_ADMIN, R.TENANT_ADMIN):
            continue
        users[role] = _h(TA, f"u-{role.value}")
        await env["grant"](TA, f"u-{role.value}", role, ScopeType.TENANT, None)
    cases = [
        # (permesso, metodo, percorso, corpo) — corpi validi, perché il permesso si controlla prima della validazione
        ("breach.view", "GET", f"{API}/breaches", None),
        ("dsr.view", "GET", f"{API}/requests", None),
        ("auditplan.view", "GET", f"{API}/audits", None),
        ("audit.view", "GET", f"{API}/audit", None),
        ("authorization.view", "GET", f"{API}/authorizations", None),
        ("dpo.view", "GET", f"{API}/dpo/workspace", None),
        ("processing.view", "GET", f"{API}/processing-activities", None),
        ("document.view", "GET", f"{API}/documents", None),
        ("dpia.create", "POST", f"{API}/dpias", {"title": "Prova"}),
        ("task.create", "POST", f"{API}/tasks", {"title": "Prova"}),
        ("asset.create", "POST", f"{API}/assets", {"name": "Prova"}),
        ("party.create", "POST", f"{API}/parties", {"legal_name": "Prova", "party_type": "SUPPLIER"}),
        ("tag.create", "POST", f"{API}/tags", {"name": "prova"}),
        ("project.create", "POST", f"{API}/projects", {"title": "P", "proposing_unit_id": "unit-a", "project_owner_id": "u"}),
        ("breach.create", "POST", f"{API}/breaches", {"title": "V", "organizational_unit_id": "unit-a", "aware_at": (datetime.utcnow() - timedelta(hours=1)).isoformat(), "description": "d"}),
        ("customfield.create", "POST", f"{API}/custom-fields", {"entity_type": "asset", "key": "campo_prova", "label": "Prova", "field_type": "TEXT"}),
    ]
    mismatches = []
    for code, method, path, body in cases:
        allowed = set(DEFAULT_MATRIX[code])
        for role, headers in users.items():
            r = await c.request(method, path, json=body, headers=headers)
            granted = r.status_code != 403
            if granted != (role in allowed):
                mismatches.append((code, role.value, r.status_code))
    assert mismatches == []


# ================================================================ isolamento tra enti

async def _create(c, url, body, who):
    r = await c.post(url, json=body, headers=who)
    assert r.status_code == 201, (url, r.text)
    return r.json()


@pytest_asyncio.fixture
async def tenant_a(env):
    """Un record per tipo nell'ente A, creato da un utente dell'ente A."""
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None)
    mgr = _h(TA, "mgr")
    soon = (date.today() + timedelta(days=10)).isoformat()
    aware = (datetime.utcnow() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    proc = env["ids"]["proc_a"]
    rows = {
        "processing-activities": {"id": proc},
        "assets": await _create(c, f"{API}/assets", {"name": "Sistema riservato di A"}, mgr),
        "parties": await _create(c, f"{API}/parties", {"legal_name": "Fornitore riservato di A", "party_type": "PROCESSOR"}, mgr),
        "notices": await _create(c, f"{API}/notices", {"title": "Informativa riservata di A", "owner_unit_id": "unit-a"}, mgr),
        "tasks": await _create(c, f"{API}/tasks", {"title": "Task riservato di A"}, mgr),
        "documents": await _create(c, f"{API}/documents", {"title": "Documento riservato di A"}, mgr),
        "dpias": await _create(c, f"{API}/dpias", {"title": "DPIA riservata di A", "processing_activity_id": proc}, mgr),
        "breaches": await _create(c, f"{API}/breaches", {"title": "Violazione riservata di A", "organizational_unit_id": "unit-a", "aware_at": aware, "description": "Testo"}, mgr),
        "requests": await _create(c, f"{API}/requests", {"organizational_unit_id": "unit-a", "request_type": "ACCESS", "received_at": aware, "channel": "PEC", "requester_reference": "riservato-di-a"}, mgr),
        "audits": await _create(c, f"{API}/audits", {"title": "Audit riservato di A", "audit_type": "INTERNAL", "organizational_unit_id": "unit-a", "planned_date": soon, "lead_auditor_user_id": "auditor"}, mgr),
        "corrective-actions": await _create(c, f"{API}/corrective-actions", {"source_type": "MANUAL", "title": "Azione riservata di A", "description": "d", "owner_user_id": "mgr", "due_date": soon}, mgr),
        "projects": await _create(c, f"{API}/projects", {"title": "Progetto riservato di A", "proposing_unit_id": "unit-a", "project_owner_id": "mgr"}, mgr),
        "tags": await _create(c, f"{API}/tags", {"name": "tag-riservato-di-a"}, mgr),
    }
    return rows


@pytest.mark.asyncio
async def test_another_tenant_cannot_read_change_or_delete_records_by_id(env, tenant_a):
    c = env["client"]
    leaks = []
    for kind, row in tenant_a.items():
        url = f"{API}/{kind}/{row['id']}"
        assert (await c.get(url, headers=ADMIN)).status_code in (200, 405), kind                              # l'ente A lo vede (dove esiste il dettaglio)
        for who_label, who in (("admin B", ADMIN_B),):
            for method in ("GET", "PUT", "PATCH", "DELETE", "POST"):
                r = await c.request(method, url, json={}, headers=ifm(1, who))
                if r.status_code < 400 or r.status_code >= 500:
                    leaks.append((who_label, method, kind, r.status_code))
                elif method == "GET" and r.status_code not in (404, 403, 405):
                    leaks.append((who_label, method, kind, r.status_code))
                assert row["id"] not in r.text or r.status_code in (404, 405, 422), (kind, method, r.text)       # l'errore non rivela metadati
    assert leaks == []


@pytest.mark.asyncio
async def test_another_tenant_never_sees_records_in_lists_search_audit_or_tags(env, tenant_a):
    c = env["client"]
    await env["grant"](TB, "b-mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None)
    mine = _h(TB, "b-mgr")
    secrets = ["riservato di A", "riservata di A", "riservato-di-a", "Testo"] + [row["id"] for row in tenant_a.values()]
    for who in (ADMIN_B, mine):
        for path in ("processing-activities", "assets", "parties", "notices", "tasks", "documents", "dpias", "breaches", "requests", "audits",
                     "corrective-actions", "projects", "tags", "audit", "findings", "approvals", "dashboard", "notifications", "dpo/workspace", "authorizations"):
            r = await c.get(f"{API}/{path}", headers=who)
            assert r.status_code in (200, 403, 404), (path, r.status_code)
            for secret in secrets:
                assert secret not in r.text, (path, secret)
        found = await c.get(f"{API}/search", params={"q": "riservat"}, headers=who)
        assert found.status_code == 200 and found.json()["total"] == 0
        q = await c.post(f"{API}/queries/run", json={"entity": "data_breach", "conditions": []}, headers=who)
        assert q.status_code in (200, 403) and (q.json()["total"] == 0 if q.status_code == 200 else True)
    assert (await c.get(f"{API}/search", params={"q": "riservat"}, headers=ADMIN)).json()["total"] >= 9        # controllo positivo: A li trova


@pytest.mark.asyncio
async def test_records_of_another_tenant_cannot_be_referenced_from_ours(env, tenant_a):
    """Un identificativo di un altro ente in un collegamento si comporta come uno inesistente: 400/404, mai 2xx."""
    c = env["client"]
    await env["grant"](TB, "b-mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None)
    b = _h(TB, "b-mgr")
    soon = (date.today() + timedelta(days=10)).isoformat()
    aware = (datetime.utcnow() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    foreign_proc, foreign_party, foreign_doc, foreign_asset = env["ids"]["proc_a"], tenant_a["parties"]["id"], tenant_a["documents"]["id"], tenant_a["assets"]["id"]
    mine = env["ids"]["proc_x"]

    async def make(url, body):
        r = await c.post(f"{API}{url}", json=body, headers=b)
        assert r.status_code == 201, (url, r.text)
        return r.json()

    b_risk = (await make("/risks", {"title": "Rischio B"}))["id"]
    b_measure = (await make("/security-measures", {"name": "Misura B"}))["id"]
    b_notice = await make("/notices", {"title": "Informativa B", "owner_unit_id": "unit-x"})
    b_breach = await make("/breaches", {"title": "Violazione B", "organizational_unit_id": "unit-x", "aware_at": aware, "description": "d"})
    b_party = (await make("/parties", {"legal_name": "Fornitore B", "party_type": "PROCESSOR"}))["id"]
    b_asset = await make("/assets", {"name": "Sistema B"})
    b_task = await make("/tasks", {"title": "Task B"})
    b_transfer = await make("/transfers", {"title": "Trasferimento B", "country_code": "US"})
    transfer = {"title": "T", "country_code": "US"}
    risk = {"entity_type": "processing_activity", "entity_id": foreign_proc, "risk_id": b_risk, "likelihood": 2, "impact": 2, "assessment_rationale": "motivazione"}
    flow = {"source_type": "ASSET", "source_id": foreign_asset, "target_type": "EXTERNAL", "target_label": "x", "frequency": "DAILY", "channel": "API", "description": "d"}
    attempts = [
        # (metodo, percorso, corpo, versione If-Match)
        ("POST", "/dpias", {"title": "X", "processing_activity_id": foreign_proc}, 1),
        ("POST", "/contracts", {"party_id": foreign_party, "title": "X"}, 1),
        ("POST", "/contracts", {"party_id": b_party, "title": "X", "document_id": foreign_doc}, 1),
        ("POST", "/contracts", {"party_id": b_party, "title": "X", "organizational_unit_id": "unit-a"}, 1),
        ("POST", "/documents", {"title": "X", "links": [{"entity_type": "processing_activity", "entity_id": foreign_proc}]}, 1),
        ("POST", "/documents", {"title": "X", "links": [{"entity_type": "dpia", "entity_id": tenant_a["dpias"]["id"]}]}, 1),
        ("POST", "/assets", {"name": "X", "supplier_id": foreign_party}, 1),
        ("POST", "/assets", {"name": "X", "owner_unit_id": "unit-a"}, 1),
        ("PATCH", f"/assets/{b_asset['id']}", {"name": "X", "supplier_id": foreign_party}, b_asset["row_version"]),
        ("POST", "/tasks", {"title": "X", "source_type": "PROCESSING", "source_id": foreign_proc}, 1),
        ("POST", "/tasks", {"title": "X", "assigned_unit_id": "unit-a"}, 1),
        ("PATCH", f"/tasks/{b_task['id']}", {"title": "X", "source_type": "ASSET", "source_id": foreign_asset}, b_task["row_version"]),
        ("POST", "/transfers", {**transfer, "exporter_party_id": foreign_party}, 1),
        ("POST", "/transfers", {**transfer, "importer_party_id": foreign_party}, 1),
        ("POST", "/transfers", {**transfer, "tia_document_id": foreign_doc}, 1),
        ("POST", "/transfers", {**transfer, "links": [{"entity_type": "processing_activity", "entity_id": foreign_proc}]}, 1),
        ("PATCH", f"/transfers/{b_transfer['id']}", {**transfer, "exporter_party_id": foreign_party}, b_transfer["row_version"]),
        ("POST", "/risk-assessments", risk, 1),
        ("POST", "/risk-assessments", {**risk, "entity_id": mine, "risk_id": "risk-di-un-altro-ente"}, 1),
        ("POST", "/measure-implementations", {"measure_id": b_measure, "entity_type": "processing_activity", "entity_id": foreign_proc}, 1),
        ("POST", "/measure-implementations", {"measure_id": b_measure, "entity_type": "asset", "entity_id": foreign_asset}, 1),
        ("POST", "/measure-implementations", {"measure_id": b_measure, "entity_type": "processing_activity", "entity_id": mine, "evidence_document_id": foreign_doc}, 1),
        ("POST", f"/processing-activities/{foreign_proc}/data-flows", flow, 1),
        ("POST", f"/processing-activities/{mine}/data-flows", flow, 1),
        ("POST", f"/processing-activities/{foreign_proc}/retention-rules", {"retention_unit": "YEARS", "retention_value": 5, "retention_criterion": "c", "disposal_method": "DELETION"}, 1),
        ("POST", "/parties/agreements", {"party_id": foreign_party}, 1),
        ("POST", "/parties/agreements", {"party_id": b_party, "document_id": foreign_doc}, 1),
        ("POST", "/subprocessors", {"processor_party_id": foreign_party, "subprocessor_party_id": b_party, "service_description": "s", "authorization_type": "GENERAL"}, 1),
        ("PUT", f"/notices/{b_notice['id']}/processing-links", {"processing_activity_ids": [foreign_proc]}, b_notice["row_version"]),
        ("PUT", f"/breaches/{b_breach['id']}/links", {"links": [{"entity_type": "processing_activity", "entity_id": foreign_proc}]}, b_breach["row_version"]),
        ("PUT", f"/processing-activities/{mine}/processors", [{"party_id": foreign_party}], 1),
        ("PUT", f"/processing-activities/{mine}/recipients", [{"party_id": foreign_party}], 1),
        ("PUT", f"/processing-activities/{mine}/assets", [{"asset_id": foreign_asset, "role": "PROCESSING"}], 1),
        ("POST", "/corrective-actions", {"source_type": "BREACH", "source_id": tenant_a["breaches"]["id"], "title": "X", "description": "d", "owner_user_id": "x", "due_date": soon}, 1),
        ("POST", "/corrective-actions", {"source_type": "MANUAL", "title": "X", "description": "d", "owner_user_id": "x", "due_date": soon, "evidence_document_id": foreign_doc}, 1),
        ("POST", "/projects", {"title": "X", "proposing_unit_id": "unit-x", "project_owner_id": "b", "supplier_id": foreign_party}, 1),
        ("POST", "/projects", {"title": "X", "proposing_unit_id": "unit-a", "project_owner_id": "b"}, 1),
        ("POST", "/breaches", {"title": "X", "organizational_unit_id": "unit-a", "aware_at": aware, "description": "d"}, 1),
        ("POST", "/requests", {"organizational_unit_id": "unit-a", "request_type": "ACCESS", "received_at": aware, "channel": "PEC"}, 1),
        ("POST", "/audits", {"title": "X", "audit_type": "INTERNAL", "organizational_unit_id": "unit-a", "planned_date": soon, "lead_auditor_user_id": "a"}, 1),
        ("POST", "/notices", {"title": "X", "owner_unit_id": "unit-a"}, 1),
        ("POST", "/processing-activities", {"title": "X", "organizational_unit_id": "unit-a"}, 1),
        ("PUT", f"/entities/processing_activity/{foreign_proc}/tags", {"tag_ids": [tenant_a["tags"]["id"]]}, 1),
        ("POST", f"/entities/processing_activity/{foreign_proc}/comments", {"body": "intrusione", "visibility": "ALL_AUTHORIZED"}, 1),
        ("POST", "/dpo/opinions", {"entity_type": "processing_activity", "entity_id": foreign_proc, "subject": "x", "question": "y"}, 1),
    ]
    accepted = []
    for method, url, body, version in attempts:
        r = await c.request(method, f"{API}{url}", json=body, headers=ifm(version, b))
        if r.status_code < 400 or r.status_code >= 500:
            accepted.append((method, url.split("/")[1], r.status_code))
    assert accepted == []
    # nell'ente A non è cambiato nulla
    ann = (await c.get(f"{API}/entities/processing_activity/{foreign_proc}/annotations", headers=ADMIN)).json()
    assert ann["comments"] == [] and ann["tags"] == []


@pytest.mark.asyncio
async def test_catalog_entries_of_another_tenant_cannot_be_used(env):
    """Categorie di dati e interessati: quelle globali sì, quelle create da un altro ente no."""
    from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog

    async with env["maker"]() as db:
        db.add_all([
            DataCategoryCatalog(id="cat-globale", tenant_id=None, code="G", name="Globale"), DataCategoryCatalog(id="cat-di-a", tenant_id=TA, code="A", name="Solo di A"),
            DataSubjectCategoryCatalog(id="sub-di-a", tenant_id=TA, code="A", name="Solo di A"),
        ])
        await db.commit()
    await env["grant"](TB, "b-mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None)
    b = _h(TB, "b-mgr")
    c = env["client"]
    base = {"title": "T", "organizational_unit_id": "unit-x"}
    assert (await c.post(f"{API}/processing-activities", json={**base, "data_category_ids": ["cat-globale"]}, headers=b)).status_code == 201
    assert (await c.post(f"{API}/processing-activities", json={**base, "data_category_ids": ["cat-di-a"]}, headers=b)).status_code == 400
    assert (await c.post(f"{API}/processing-activities", json={**base, "data_subject_ids": ["sub-di-a"]}, headers=b)).status_code == 400
    await env["grant"](TA, "a-mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None)
    assert (await c.post(f"{API}/processing-activities", json={"title": "T", "organizational_unit_id": "unit-a", "data_category_ids": ["cat-di-a"]}, headers=_h(TA, "a-mgr"))).status_code == 201
