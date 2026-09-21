from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_assessments import TiaOutcome, TransferMechanism, TransferStatus
from backend.services.privacypa_transfer_service import TransferService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/transfers",
    tags=["PrivacyPA - Trasferimenti / TIA"],
    dependencies=[Depends(require_module_view("transfer"))],
)


class TransferLinkIn(BaseModel):
    entity_type: str
    entity_id: str


class TransferUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    country_code: str = Field(..., min_length=2, max_length=2)
    exporter_party_id: str | None = None
    importer_party_id: str | None = None
    description: str | None = None
    mechanism: TransferMechanism = TransferMechanism.SCC
    adequacy_reference: str | None = None
    scc_module: str | None = None
    tia_required: bool | None = None
    tia_outcome: TiaOutcome = TiaOutcome.PENDING
    tia_rationale: str | None = None
    law_access_assessment: str | None = None
    supplementary_measures: str | None = None
    tia_document_id: str | None = None
    last_review_date: date | None = None
    next_review_date: date | None = None
    status: TransferStatus = TransferStatus.DRAFT
    links: list[TransferLinkIn] | None = None


@router.get("")
async def list_transfers(tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    return [t.to_dict() for t in await TransferService(db).list_transfers(tenant_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_transfer(
    body: TransferUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("transfer.create")),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    links = data.pop("links", None)
    if data.get("tia_required") is None:
        data.pop("tia_required")
    try:
        await TransferService(db).check_links(tenant_id, links)
        item = await TransferService(db).create(tenant_id, user.sub or user.email, **data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if links:
        await TransferService(db).set_links(item.id, links)
    stored_links = await TransferService(db).list_links(item.id)
    return {**item.to_dict(), "links": [l.to_dict() for l in stored_links]}


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    svc = TransferService(db)
    item = await svc.get(tenant_id, transfer_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trasferimento non trovato")
    response.headers["ETag"] = etag_of(item)
    links = await svc.list_links(transfer_id)
    return {**item.to_dict(), "links": [l.to_dict() for l in links]}


@router.patch("/{transfer_id}")
async def update_transfer(
    transfer_id: str,
    body: TransferUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("transfer.edit")),
    db: AsyncSession = Depends(get_db),
):
    current = await TransferService(db).get(tenant_id, transfer_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trasferimento non trovato")
    assert_version(current, version)
    try:
        data = body.model_dump()
        links = data.pop("links", None)
        await TransferService(db).check_links(tenant_id, links)
        item = await TransferService(db).update(tenant_id, transfer_id, user.sub or user.email, **data)
        if links is not None:
            await TransferService(db).set_links(item.id, links)
        stored_links = await TransferService(db).list_links(item.id)
        return {**item.to_dict(), "links": [l.to_dict() for l in stored_links]}
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
