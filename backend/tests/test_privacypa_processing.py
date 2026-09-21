"""Test del registro trattamenti: codifica, transizioni di stato, soft delete."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.models import (  # noqa: F401
    privacypa_audit,
    privacypa_catalog,
    privacypa_identity,
    privacypa_organization,
    privacypa_processing,
)
from backend.models.privacypa_processing import (
    ALLOWED_STATUS_TRANSITIONS,
    ProcessingStatus,
)
from backend.models.privacypa_organization import Organization, OrganizationalUnit, OrganizationalUnitType
from backend.services.privacypa_processing_service import MissingRequiredFields, ProcessingService

COMPLETE = dict(
    description="Gestione degli interventi",
    organizational_unit_id="unit-1",
    legal_basis_codes=["GDPR_6_1_E"],
    data_subject_ids=["subject-1"],
    data_category_ids=["category-1"],
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        # la struttura indicata nei trattamenti deve esistere nell'ente (§141)
        db.add(Organization(id="org-1", tenant_id="t1", name="Ente"))
        db.add(OrganizationalUnit(id="unit-1", tenant_id="t1", organization_id="org-1", code="U1", name="Servizio", unit_type=OrganizationalUnitType.SERVIZIO))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_next_code_sequence(session):
    svc = ProcessingService(session)
    first = await svc.create_activity("tenant-a", "user-1", title="Anagrafe")
    second = await svc.create_activity("tenant-a", "user-1", title="Protocollo")
    other = await svc.create_activity("tenant-b", "user-1", title="Altro ente")
    assert first.code == "TR-000001"
    assert second.code == "TR-000002"
    assert other.code == "TR-000001"
    await session.commit()


@pytest.mark.asyncio
async def test_status_transition_and_reject_illegal(session):
    svc = ProcessingService(session)
    activity = await svc.create_activity("t1", "u1", title="Servizi sociali", **COMPLETE)
    assert activity.status == ProcessingStatus.DRAFT

    with pytest.raises(ValueError, match="Transizione non ammessa"):
        await svc.transition_status("t1", activity.id, "u1", ProcessingStatus.ACTIVE)

    reviewed = await svc.transition_status("t1", activity.id, "u1", ProcessingStatus.IN_REVIEW)
    assert reviewed.status == ProcessingStatus.IN_REVIEW
    approved = await svc.transition_status("t1", activity.id, "u1", ProcessingStatus.APPROVED)
    active = await svc.transition_status("t1", activity.id, "u1", ProcessingStatus.ACTIVE)
    assert active.status == ProcessingStatus.ACTIVE
    assert active.start_date is not None


@pytest.mark.asyncio
async def test_soft_delete_only_draft(session):
    svc = ProcessingService(session)
    draft = await svc.create_activity("t1", "u1", title="Bozza")
    await svc.soft_delete("t1", draft.id, "u1")
    assert await svc.get_activity("t1", draft.id) is None

    live = await svc.create_activity("t1", "u1", title="Da attivare", **COMPLETE)
    await svc.transition_status("t1", live.id, "u1", ProcessingStatus.IN_REVIEW)
    with pytest.raises(ValueError, match="solo in bozza"):
        await svc.soft_delete("t1", live.id, "u1")


def test_allowed_transitions_match_spec():
    assert ProcessingStatus.DRAFT in ALLOWED_STATUS_TRANSITIONS
    assert ALLOWED_STATUS_TRANSITIONS[ProcessingStatus.DRAFT] == {ProcessingStatus.IN_REVIEW}
    assert ProcessingStatus.ARCHIVED in ALLOWED_STATUS_TRANSITIONS[ProcessingStatus.ACTIVE]
    assert ALLOWED_STATUS_TRANSITIONS[ProcessingStatus.ARCHIVED] == set()


@pytest.mark.asyncio
async def test_review_requires_minimum_data(session):
    """AC-REG-002: senza dati minimi l'invio in revisione è rifiutato e indica cosa manca."""
    svc = ProcessingService(session)
    empty = await svc.create_activity("t1", "u1", title="Vuoto")
    with pytest.raises(MissingRequiredFields) as exc:
        await svc.transition_status("t1", empty.id, "u1", ProcessingStatus.IN_REVIEW)
    assert exc.value.missing == [
        "struttura",
        "descrizione/finalità",
        "almeno una base giuridica",
        "almeno una categoria di interessati",
        "almeno una categoria di dati",
    ]
    assert (await svc.get_activity("t1", empty.id)).status == ProcessingStatus.DRAFT

    # finalità in forma di elenco al posto della descrizione
    partial = await svc.create_activity(
        "t1", "u1", title="Parziale", organizational_unit_id="unit-1", purposes=["Istruttoria"],
        legal_basis_codes=["GDPR_6_1_E"], data_subject_ids=["s"],
    )
    with pytest.raises(MissingRequiredFields) as exc:
        await svc.transition_status("t1", partial.id, "u1", ProcessingStatus.IN_REVIEW)
    assert exc.value.missing == ["almeno una categoria di dati"]


@pytest.mark.asyncio
async def test_review_validation_applies_when_reopening_active_activity(session):
    svc = ProcessingService(session)
    a = await svc.create_activity("t1", "u1", title="Da riaprire", **COMPLETE)
    for target in (ProcessingStatus.IN_REVIEW, ProcessingStatus.APPROVED, ProcessingStatus.ACTIVE):
        await svc.transition_status("t1", a.id, "u1", target)
    await svc.update_activity("t1", a.id, "u1", title="Da riaprire", legal_basis_codes=[])
    with pytest.raises(MissingRequiredFields):
        await svc.transition_status("t1", a.id, "u1", ProcessingStatus.IN_REVIEW)
