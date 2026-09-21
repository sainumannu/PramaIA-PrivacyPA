"""Reset e inventario dei dati di esempio per sezione."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.db.init_db import init_database  # noqa: F401 — registra i modelli
from backend.models import (  # noqa: F401
    privacypa_annotations,
    privacypa_approvals,
    privacypa_assessments,
    privacypa_assets,
    privacypa_audit,
    privacypa_catalog,
    privacypa_compliance,
    privacypa_documents,
    privacypa_identity,
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
    privacypa_webhooks,
)
from backend.models.privacypa_assets import Asset, AssetType
from backend.models.privacypa_documents import DocumentType
from backend.models.privacypa_organization import Organization, OrganizationalUnit, OrganizationalUnitType
from backend.models.privacypa_processing import ProcessingActivity
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_demo_data_service import DemoDataError, DemoDataService
from backend.services.privacypa_document_service import DocumentService
from backend.services.privacypa_processing_service import ProcessingService


TENANT = "tenant-demo"


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    from backend.services import privacypa_document_service

    monkeypatch.setattr(privacypa_document_service, "STORAGE_ROOT", tmp_path / "docs")
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "disabled")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id="org-1", tenant_id=TENANT, name="Ente demo"))
        db.add(OrganizationalUnit(
            id="unit-1", tenant_id=TENANT, organization_id="org-1",
            code="U1", name="Servizio", unit_type=OrganizationalUnitType.SERVIZIO,
        ))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_and_reset_processing_and_documents(session):
    proc = ProcessingService(session)
    activity = await proc.create_activity(TENANT, "user-1", title="Anagrafe", organizational_unit_id="unit-1")
    assets = AssetService(session)
    await assets.create(TENANT, "user-1", name="ANPR", asset_type=AssetType.APPLICATION, owner_unit_id="unit-1")
    docs = DocumentService(session)
    doc = await docs.create(
        TENANT, "user-1", title="Estratto", document_type=DocumentType.REGISTER_EXTRACT,
        links=[{"entity_type": "processing_activity", "entity_id": activity.id, "relation_type": "evidence"}],
    )
    await docs.add_version(TENANT, doc.id, "user-1", file_name="estratto.txt", content=b"test")
    await session.commit()

    svc = DemoDataService(session)
    inventory = {row["code"]: row for row in await svc.inventory(TENANT)}
    assert inventory["processing"]["records"] >= 1
    assert inventory["processing"]["documents"] >= 1
    assert inventory["assets"]["records"] >= 1
    assert inventory["documents"]["has_documents"] is True

    files = await svc.reset(TENANT, "admin", "processing", "documents")
    assert files["files"] >= 1
    inventory = {row["code"]: row for row in await svc.inventory(TENANT)}
    assert inventory["processing"]["documents"] == 0
    assert inventory["processing"]["records"] >= 1

    data = await svc.reset(TENANT, "admin", "processing", "data")
    assert data["records"] >= 1
    inventory = {row["code"]: row for row in await svc.inventory(TENANT)}
    assert inventory["processing"]["records"] == 0
    leftover = (await session.get(ProcessingActivity, activity.id))
    assert leftover is None


@pytest.mark.asyncio
async def test_unknown_section_and_documents_on_catalogs(session):
    svc = DemoDataService(session)
    with pytest.raises(DemoDataError):
        await svc.reset(TENANT, "admin", "nope", "data")
    with pytest.raises(DemoDataError):
        await svc.reset(TENANT, "admin", "catalogs", "documents")
