"""Fixture condivise: app FastAPI minimale con DB SQLite in memoria e identità iniettata."""
import json
from datetime import datetime

import httpx
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.db.database import Base, get_db
from backend.models import (  # noqa: F401
    privacypa_assessments,
    privacypa_annotations,
    privacypa_approvals,
    privacypa_assets,
    privacypa_audit,
    privacypa_catalog,
    privacypa_compliance,
    privacypa_documents,
    privacypa_identity,
    privacypa_import,
    privacypa_notices,
    privacypa_organization,
    privacypa_outbox,
    privacypa_parties,
    privacypa_people,
    privacypa_processing,
    privacypa_projects,
    privacypa_retention,
    privacypa_risk,
    privacypa_rules,
    privacypa_tasks,
)
from backend.models.privacypa_identity import ScopeType, UserRoleAssignment
from backend.models.privacypa_organization import Organization, OrganizationalUnit, OrganizationalUnitType
from backend.routers import (
    privacypa_annotation_router,
    privacypa_query_router,
    privacypa_webhook_router,
    privacypa_catalog_router,
    privacypa_risk_router,
    privacypa_approval_router,
    privacypa_asset_router,
    privacypa_dataflow_router,
    privacypa_document_router,
    privacypa_dpia_router,
    privacypa_dpo_router,
    privacypa_export_router,
    privacypa_identity_router,
    privacypa_findings_router,
    privacypa_import_router,
    privacypa_notice_router,
    privacypa_ops_router,
    privacypa_organization_router,
    privacypa_outbox_router,
    privacypa_party_router,
    privacypa_people_router,
    privacypa_policy_router,
    privacypa_processing_router,
    privacypa_project_router,
    privacypa_retention_router,
    privacypa_workflow_router,
    privacypa_supply_router,
    privacypa_task_router,
    privacypa_transfer_router,
)
from backend.api_errors import register_error_handlers
from backend.services.privacypa_processing_service import ProcessingService

TA, TB = "tenant-a", "tenant-b"


def _identity(tenant: str, sub: str, tenant_role: str | None = None) -> str:
    return json.dumps({"tenant_id": tenant, "sub": sub, "email": f"{sub}@example.org", "tenant_role": tenant_role})


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    from backend.services import privacypa_document_service

    monkeypatch.setattr(privacypa_document_service, "STORAGE_ROOT", tmp_path / "docs")
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "disabled")
    from backend.services import privacypa_import_service

    monkeypatch.setattr(privacypa_import_service, "IMPORT_ROOT", tmp_path / "imports")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    for module in (
        privacypa_processing_router,
        privacypa_ops_router,
        privacypa_document_router,
        privacypa_dpia_router,
        privacypa_task_router,
        privacypa_asset_router,
        privacypa_party_router,
        privacypa_transfer_router,
        privacypa_organization_router,
        privacypa_import_router,
        privacypa_approval_router,
        privacypa_policy_router,
        privacypa_retention_router,
        privacypa_supply_router,
        privacypa_findings_router,
        privacypa_outbox_router,
        privacypa_export_router,
        privacypa_notice_router,
        privacypa_dataflow_router,
        privacypa_workflow_router,
        privacypa_people_router,
        privacypa_dpo_router,
        privacypa_project_router,
        privacypa_annotation_router,
        privacypa_query_router,
        privacypa_webhook_router,
        privacypa_catalog_router,
        privacypa_risk_router,
        privacypa_identity_router,
    ):
        app.include_router(module.router)
    register_error_handlers(app)

    async def override_db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def override_user(request: Request) -> TokenPayload:
        return TokenPayload(json.loads(request.headers["X-Test-User"]))

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    # Dati: due strutture sorelle (A, B) nel tenant A, una struttura nel tenant B.
    async with maker() as db:
        for tenant in (TA, TB):
            db.add(Organization(id=f"org-{tenant}", tenant_id=tenant, name=tenant))
        for uid, tenant in (("unit-a", TA), ("unit-b", TA), ("unit-x", TB)):
            db.add(OrganizationalUnit(
                id=uid, tenant_id=tenant, organization_id=f"org-{tenant}",
                code=uid, name=uid, unit_type=OrganizationalUnitType.SERVIZIO,
            ))
        await db.commit()

        svc = ProcessingService(db)
        complete = dict(
            description="Descrizione",
            legal_basis_codes=["GDPR_6_1_E"],
            data_subject_ids=["subject-1"],
            data_category_ids=["category-1"],
        )
        ids = {
            "proc_a": (await svc.create_activity(TA, "seed", "Trattamento A", organizational_unit_id="unit-a", **complete)).id,
            "proc_b": (await svc.create_activity(TA, "seed", "Trattamento B", organizational_unit_id="unit-b", **complete)).id,
            "proc_x": (await svc.create_activity(TB, "seed", "Trattamento X", organizational_unit_id="unit-x", **complete)).id,
        }
        await db.commit()

    async def grant(tenant, user, role, scope=ScopeType.TENANT, unit=None):
        async with maker() as db:
            db.add(UserRoleAssignment(
                tenant_id=tenant, user_id=user, role_code=role, scope_type=scope,
                organizational_unit_id=unit, valid_from=datetime(2020, 1, 1),
            ))
            await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "maker": maker, "ids": ids, "grant": grant, "tmp_docs": tmp_path / "docs"}
    await engine.dispose()


def _h(tenant, sub, tenant_role=None):
    return {"X-Test-User": _identity(tenant, sub, tenant_role)}


def ifm(version, headers=None):
    """Header If-Match (§54) da unire agli header di identità."""
    return {**(headers or {}), "If-Match": f'"{version}"'}


async def put_links(client, url, json, headers):
    """PUT dei collegamenti di un trattamento con la versione corrente nell'If-Match (§54)."""
    version = await version_of(client, url.rsplit("/", 1)[0])
    return await client.put(url, json=json, headers=ifm(version, headers))


async def version_of(client, url, tenant=TA):
    """Versione corrente di un record, letta come amministratore del tenant."""
    r = await client.get(url, headers=_h(tenant, "root", "admin"))
    assert r.status_code == 200, r.text
    return r.json()["row_version"]
