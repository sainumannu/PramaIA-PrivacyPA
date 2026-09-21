"""
PrivacyPA — Flussi dei dati (§20): elenco, modifica e grafo per trattamento.

Lettura e scrittura seguono lo scope della struttura del trattamento; le modifiche richiedono `If-Match` (§54).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_notices import FlowChannel, FlowEndpointType, FlowFrequency
from backend.services.privacypa_dataflow_service import DataFlowService, FlowError
from backend.services.privacypa_processing_service import ProcessingService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa",
    tags=["PrivacyPA - Flussi dei dati"],
    dependencies=[Depends(require_module_view("dataflow"))],
)


class DataFlowIn(BaseModel):
    source_type: FlowEndpointType
    source_id: str | None = None
    source_label: str | None = Field(None, max_length=255)
    target_type: FlowEndpointType
    target_id: str | None = None
    target_label: str | None = Field(None, max_length=255)
    frequency: FlowFrequency
    channel: FlowChannel
    description: str = Field(..., min_length=1)
    security_notes: str | None = None
    data_category_ids: list[str] = Field(default_factory=list, max_length=200)


def _bad(e: FlowError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


async def _scope(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str | None):
    roles = await roles_for(db, tenant_id, code) if code else None
    return await build_scope(db, tenant_id, user, roles=roles)


async def _processing(db: AsyncSession, tenant_id: str, user: TokenPayload, processing_id: str, code: str | None):
    """Trattamento (404) con permesso e scope, di lettura o del permesso `code` (403)."""
    if await ProcessingService(db).get_activity(tenant_id, processing_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trattamento non trovato")
    if code:
        await assert_permission(user, db, code)
    (await _scope(db, tenant_id, user, code)).assert_processing(processing_id)


async def _flow(db: AsyncSession, tenant_id: str, user: TokenPayload, flow_id: str, code: str | None, version: int | None = None):
    flow = await DataFlowService(db).get(tenant_id, flow_id)
    if flow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flusso non trovato")
    await _processing(db, tenant_id, user, flow.processing_activity_id, code)
    if version is not None:
        assert_version(flow, version)
    return flow


@router.get("/processing-activities/{processing_id}/data-flows")
async def list_flows(
    processing_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _processing(db, tenant_id, user, processing_id, None)
    return [f.to_dict() for f in await DataFlowService(db).list_for_processing(tenant_id, processing_id)]


@router.get("/processing-activities/{processing_id}/data-flows/graph")
async def flow_graph(
    processing_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _processing(db, tenant_id, user, processing_id, None)
    return await DataFlowService(db).graph(tenant_id, processing_id)


@router.post("/processing-activities/{processing_id}/data-flows", status_code=status.HTTP_201_CREATED)
async def create_flow(
    processing_id: str,
    body: DataFlowIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dataflow.create")),
    db: AsyncSession = Depends(get_db),
):
    await _processing(db, tenant_id, user, processing_id, "dataflow.create")
    data = body.model_dump()
    try:
        flow = await DataFlowService(db).create(
            tenant_id, user.sub or user.email, processing_id, data.pop("data_category_ids"), **data
        )
    except FlowError as e:
        raise _bad(e)
    response.headers["ETag"] = etag_of(flow)
    return flow.to_dict()


@router.get("/data-flows/{flow_id}")
async def get_flow(
    flow_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    flow = await _flow(db, tenant_id, user, flow_id, None)
    response.headers["ETag"] = etag_of(flow)
    return flow.to_dict()


@router.patch("/data-flows/{flow_id}")
async def update_flow(
    flow_id: str,
    body: DataFlowIn,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("dataflow.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _flow(db, tenant_id, user, flow_id, "dataflow.edit", version)
    data = body.model_dump()
    try:
        flow = await DataFlowService(db).update(
            tenant_id, flow_id, user.sub or user.email, data.pop("data_category_ids"), **data
        )
    except FlowError as e:
        raise _bad(e)
    response.headers["ETag"] = etag_of(flow)
    return flow.to_dict()


@router.delete("/data-flows/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(
    flow_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("dataflow.delete")),
    db: AsyncSession = Depends(get_db),
):
    await _flow(db, tenant_id, user, flow_id, "dataflow.delete")
    try:
        await DataFlowService(db).soft_delete(tenant_id, flow_id, user.sub or user.email)
    except FlowError as e:
        raise _bad(e)
