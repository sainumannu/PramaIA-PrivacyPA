"""
Test di autorizzazione e isolamento tenant sulle API PrivacyPA (§10, §133, §141, §142).

Le API vengono esercitate end-to-end (router + service + DB SQLite in memoria); l'identità
è iniettata con l'header `X-Test-User` al posto del JWT Portal.
"""
import json
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.db.database import Base, get_db
from backend.models import (  # noqa: F401
    privacypa_assessments,
    privacypa_assets,
    privacypa_audit,
    privacypa_catalog,
    privacypa_documents,
    privacypa_identity,
    privacypa_organization,
    privacypa_parties,
    privacypa_processing,
    privacypa_risk,
    privacypa_tasks,
)
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_documents import Document, DocumentLink
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType, UserRoleAssignment
from backend.models.privacypa_organization import Organization, OrganizationalUnit, OrganizationalUnitType
from backend.models.privacypa_processing import ProcessingStatus
from backend.routers import (
    privacypa_document_router,
    privacypa_dpia_router,
    privacypa_ops_router,
    privacypa_processing_router,
    privacypa_task_router,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_processing_service import ProcessingService

from backend.tests.conftest import put_links, TA, TB, _h, ifm, version_of  # noqa: E402


# ---------------------------------------------------------------- audit (§33.2)

@pytest.mark.asyncio
async def test_audit_readable_only_by_tenant_wide_control_roles(env):
    c, grant = env["client"], env["grant"]
    await grant(TA, "operator", R.OPERATOR)
    await grant(TA, "dpo-unit", R.DPO, ScopeType.UNIT, "unit-a")
    await grant(TA, "dpo-tenant", R.DPO)
    await grant(TA, "auditor", R.AUDITOR)

    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "nobody"))).status_code == 403
    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "operator"))).status_code == 403
    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "dpo-unit"))).status_code == 403
    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "dpo-tenant"))).status_code == 200
    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "auditor"))).status_code == 200
    assert (await c.get("/api/privacypa/audit", headers=_h(TA, "admin", "admin"))).status_code == 200


@pytest.mark.asyncio
async def test_audit_does_not_leak_other_tenant(env):
    await env["grant"](TA, "auditor", R.AUDITOR)
    rows = (await env["client"].get("/api/privacypa/audit", headers=_h(TA, "auditor"))).json()
    assert rows and all(r["tenant_id"] == TA for r in rows)


# ---------------------------------------------------------------- isolamento tenant (§141)

@pytest.mark.asyncio
async def test_cross_tenant_processing_is_not_found(env):
    c, ids = env["client"], env["ids"]
    await env["grant"](TB, "b-manager", R.PRIVACY_MANAGER)
    who = _h(TB, "b-manager")
    for path in (
        f"/api/privacypa/processing-activities/{ids['proc_a']}",
        f"/api/privacypa/processing-activities/{ids['proc_a']}/versions",
    ):
        assert (await c.get(path, headers=who)).status_code == 404
    r = await c.patch(
        f"/api/privacypa/processing-activities/{ids['proc_a']}",
        json={"title": "hack"}, headers=ifm(1, who),
    )
    assert r.status_code == 404
    r = await c.post(
        f"/api/privacypa/processing-activities/{ids['proc_a']}/status",
        json={"status": "IN_REVIEW"}, headers=ifm(1, who),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_links_reject_other_tenant_targets(env):
    c, ids, maker = env["client"], env["ids"], env["maker"]
    await env["grant"](TA, "a-manager", R.PRIVACY_MANAGER)
    async with maker() as db:
        foreign = Asset(tenant_id=TB, code="AS-000001", name="Sistema di B")
        db.add(foreign)
        await db.commit()
        foreign_id = foreign.id
    r = await put_links(c, f"/api/privacypa/processing-activities/{ids['proc_a']}/assets",
        json=[{"asset_id": foreign_id, "role": "PROCESSING"}],
        headers=_h(TA, "a-manager"),
    )
    assert r.status_code == 400
    r = await put_links(c, f"/api/privacypa/processing-activities/{ids['proc_a']}/recipients",
        json=[{"party_id": "does-not-exist"}], headers=_h(TA, "a-manager"),
    )
    assert r.status_code == 400
    r = await put_links(c, f"/api/privacypa/processing-activities/{ids['proc_a']}/assets",
        json=[{"asset_id": foreign_id, "role": "NOT_A_ROLE"}], headers=_h(TA, "a-manager"),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- scope (§10, AC-REG-005)

@pytest.mark.asyncio
async def test_scope_applies_to_reads_versions_and_links(env):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    who = _h(TA, "ref-a")
    base = "/api/privacypa/processing-activities"

    assert (await c.get(f"{base}/{ids['proc_a']}", headers=who)).status_code == 200
    assert (await c.get(f"{base}/{ids['proc_a']}/versions", headers=who)).status_code == 200
    assert (await c.get(f"{base}/{ids['proc_b']}", headers=who)).status_code == 403
    assert (await c.get(f"{base}/{ids['proc_b']}/versions", headers=who)).status_code == 403
    assert (await put_links(c, f"{base}/{ids['proc_b']}/assets", json=[], headers=who)).status_code == 403
    assert (await put_links(c, f"{base}/{ids['proc_b']}/processors", json=[], headers=who)).status_code == 403
    assert (await put_links(c, f"{base}/{ids['proc_b']}/recipients", json=[], headers=who)).status_code == 403
    assert (await put_links(c, f"{base}/{ids['proc_a']}/assets", json=[], headers=who)).status_code == 200

    listed = (await c.get(base, headers=who)).json()
    assert [p["id"] for p in listed] == [ids["proc_a"]]


@pytest.mark.asyncio
async def test_user_without_assignments_sees_nothing(env):
    c, ids = env["client"], env["ids"]
    who = _h(TA, "stranger")
    # Senza alcun ruolo assegnato manca il permesso di lettura del modulo (§10.1).
    assert (await c.get("/api/privacypa/processing-activities", headers=who)).status_code == 403
    assert (await c.get(f"/api/privacypa/processing-activities/{ids['proc_a']}", headers=who)).status_code == 403


# ---------------------------------------------------------------- ruolo legato allo scope (§9.3)

@pytest.mark.asyncio
async def test_approval_role_must_cover_the_record_unit(env):
    c, ids, grant, maker = env["client"], env["ids"], env["grant"], env["maker"]
    # OPERATOR sull'ufficio A, DPO sull'ufficio B: non può approvare sull'ufficio A.
    await grant(TA, "mixed", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await grant(TA, "mixed", R.DPO, ScopeType.UNIT, "unit-b")
    async with maker() as db:
        svc = ProcessingService(db)
        for pid in (ids["proc_a"], ids["proc_b"]):
            await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await db.commit()

    base = "/api/privacypa/processing-activities"
    who = _h(TA, "mixed")
    va, vb = await version_of(c, f"{base}/{ids['proc_a']}"), await version_of(c, f"{base}/{ids['proc_b']}")
    denied = await c.post(f"{base}/{ids['proc_a']}/status", json={"status": "APPROVED"}, headers=ifm(va, who))
    assert denied.status_code == 403
    allowed = await c.post(f"{base}/{ids['proc_b']}/status", json={"status": "APPROVED"}, headers=ifm(vb, who))
    assert allowed.status_code == 200 and allowed.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_read_only_scope_does_not_grant_write_elsewhere(env):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    # READ_ONLY su A + OPERATOR su B: vede A ma non può modificarlo.
    await grant(TA, "reader", R.READ_ONLY, ScopeType.UNIT, "unit-a")
    await grant(TA, "reader", R.OPERATOR, ScopeType.UNIT, "unit-b")
    who = _h(TA, "reader")
    base = "/api/privacypa/processing-activities"

    assert (await c.get(f"{base}/{ids['proc_a']}", headers=who)).status_code == 200
    va, vb = await version_of(c, f"{base}/{ids['proc_a']}"), await version_of(c, f"{base}/{ids['proc_b']}")
    r = await c.patch(f"{base}/{ids['proc_a']}", json={"title": "cambiato", "organizational_unit_id": "unit-a"}, headers=ifm(va, who))
    assert r.status_code == 403
    r = await c.patch(f"{base}/{ids['proc_b']}", json={"title": "ok", "organizational_unit_id": "unit-b"}, headers=ifm(vb, who))
    assert r.status_code == 200
    r = await c.post(base, json={"title": "Nuovo", "organizational_unit_id": "unit-a"}, headers=who)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_subtree_scope_covers_children(env):
    c, ids, grant, maker = env["client"], env["ids"], env["grant"], env["maker"]
    async with maker() as db:
        db.add(OrganizationalUnit(
            id="unit-a-child", tenant_id=TA, organization_id=f"org-{TA}", parent_id="unit-a",
            code="a1", name="a1", unit_type=OrganizationalUnitType.UFFICIO,
        ))
        child = await ProcessingService(db).create_activity(TA, "seed", "Figlio", organizational_unit_id="unit-a-child")
        await db.commit()
    await grant(TA, "dirigente", R.MANAGER, ScopeType.SUBTREE, "unit-a")
    who = _h(TA, "dirigente")
    base = "/api/privacypa/processing-activities"
    assert (await c.get(f"{base}/{child.id}", headers=who)).status_code == 200
    assert (await c.get(f"{base}/{ids['proc_b']}", headers=who)).status_code == 403


# ---------------------------------------------------------------- documenti (AC-DOC-003)

@pytest.mark.asyncio
async def test_documents_follow_processing_scope_and_download_is_audited(env):
    c, ids, grant, maker = env["client"], env["ids"], env["grant"], env["maker"]
    await grant(TA, "ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    await grant(TA, "manager", R.PRIVACY_MANAGER)
    async with maker() as db:
        doc_b = Document(tenant_id=TA, code="DOC-000001", title="Contratto B", created_by="manager")
        doc_a = Document(tenant_id=TA, code="DOC-000002", title="Informativa A", created_by="manager")
        db.add_all([doc_a, doc_b])
        await db.flush()
        db.add(DocumentLink(document_id=doc_b.id, entity_type="processing_activity", entity_id=ids["proc_b"]))
        db.add(DocumentLink(document_id=doc_a.id, entity_type="processing_activity", entity_id=ids["proc_a"]))
        await db.commit()
        a_id, b_id = doc_a.id, doc_b.id

    who = _h(TA, "ref-a")
    assert [d["id"] for d in (await c.get("/api/privacypa/documents", headers=who)).json()] == [a_id]
    assert (await c.get(f"/api/privacypa/documents/{b_id}", headers=who)).status_code == 403
    assert (await c.get(f"/api/privacypa/documents/{b_id}/file", headers=who)).status_code == 403
    upload = await c.post(
        f"/api/privacypa/documents/{b_id}/versions",
        files={"file": ("x.txt", b"ciao", "text/plain")}, headers=who,
    )
    assert upload.status_code == 403
    # Un utente con scope limitato non può collegare un documento a un trattamento fuori scope.
    r = await c.post(
        "/api/privacypa/documents",
        json={"title": "Nuovo", "links": [{"entity_type": "processing_activity", "entity_id": ids["proc_b"]}]},
        headers=who,
    )
    assert r.status_code == 403

    # Il download nello scope funziona ed è tracciato nell'audit.
    manager = _h(TA, "manager")
    up = await c.post(
        f"/api/privacypa/documents/{a_id}/versions",
        files={"file": ("x.txt", b"ciao", "text/plain")}, headers=manager,
    )
    assert up.status_code == 200
    dl = await c.get(f"/api/privacypa/documents/{a_id}/file", headers=who)
    assert dl.status_code == 200 and dl.content == b"ciao"
    async with maker() as db:
        events = await AuditService(db).list_for_entity(TA, "document", a_id)
    assert "download" in {e.action for e in events}


@pytest.mark.asyncio
async def test_search_and_dashboard_do_not_expose_out_of_scope_records(env):
    c, ids, grant, maker = env["client"], env["ids"], env["grant"], env["maker"]
    await grant(TA, "ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    async with maker() as db:
        db.add(Document(tenant_id=TA, code="DOC-000009", title="Riservato Beta", created_by="manager"))
        doc = (await db.execute(
            Document.__table__.select().where(Document.code == "DOC-000009")
        )).first()
        await db.flush()
        db.add(DocumentLink(document_id=doc.id, entity_type="processing_activity", entity_id=ids["proc_b"]))
        dpia = await DpiaService(db).create(TA, "manager", "DPIA Beta", processing_activity_id=ids["proc_b"])
        await db.commit()

    who = _h(TA, "ref-a")
    found = (await c.get("/api/privacypa/search", params={"q": "Beta"}, headers=who)).json()
    assert found["processing_activities"] == []
    assert found["documents"] == []
    assert found["dpias"] == []

    assert (await c.get(f"/api/privacypa/dpias/{dpia.id}", headers=who)).status_code == 403
    assert (await c.get("/api/privacypa/dpias", headers=who)).json() == []
    board = (await c.get("/api/privacypa/dashboard", headers=who)).json()
    assert board["processing"]["total"] == 1


@pytest.mark.asyncio
async def test_dpia_approval_requires_role_on_processing_unit(env):
    c, ids, grant, maker = env["client"], env["ids"], env["grant"], env["maker"]
    await grant(TA, "mixed", R.OPERATOR, ScopeType.UNIT, "unit-a")
    await grant(TA, "mixed", R.DPO, ScopeType.UNIT, "unit-b")
    async with maker() as db:
        dpia = await DpiaService(db).create(
            TA, "seed", "DPIA A", processing_activity_id=ids["proc_a"],
            controller_decision="Procedere", screening_rationale="Necessaria",
        )
        await db.commit()
    who = _h(TA, "mixed")
    v = await version_of(c, f"/api/privacypa/dpias/{dpia.id}")
    r = await c.post(f"/api/privacypa/dpias/{dpia.id}/status", json={"status": "NOT_REQUIRED"}, headers=ifm(v, who))
    assert r.status_code == 403


# ---------------------------------------------------------------- task

@pytest.mark.asyncio
async def test_tasks_visible_only_within_scope(env):
    c, ids, grant = env["client"], env["ids"], env["grant"]
    await grant(TA, "ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a")
    await grant(TA, "manager", R.PRIVACY_MANAGER)
    manager, ref = _h(TA, "manager"), _h(TA, "ref-a")
    t_b = (await c.post("/api/privacypa/tasks", json={
        "title": "Task B", "source_type": "PROCESSING", "source_id": ids["proc_b"],
    }, headers=manager)).json()
    t_a = (await c.post("/api/privacypa/tasks", json={
        "title": "Task A", "source_type": "PROCESSING", "source_id": ids["proc_a"],
    }, headers=manager)).json()

    assert [t["id"] for t in (await c.get("/api/privacypa/tasks", headers=ref)).json()] == [t_a["id"]]
    r = await c.patch(f"/api/privacypa/tasks/{t_b['id']}", json={"title": "x"}, headers=ifm(t_b["row_version"], ref))
    assert r.status_code == 403
    r = await c.patch(f"/api/privacypa/tasks/{t_a['id']}", json={"title": "x", "source_type": "PROCESSING",
                                                                 "source_id": ids["proc_a"]},
                      headers=ifm(t_a["row_version"], ref))
    assert r.status_code == 200
