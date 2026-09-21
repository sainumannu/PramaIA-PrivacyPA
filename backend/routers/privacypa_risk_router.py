from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_risk import ImplementationStatus, MeasureCategory
from backend.services.privacypa_risk_service import RiskService

router = APIRouter(
    prefix="/api/privacypa",
    tags=["PrivacyPA - Rischi"],
    dependencies=[Depends(require_module_view("risk"))],
)


class MatrixIn(BaseModel):
    scale_min: int = 1
    scale_max: int = 5
    bands: list[dict]


class RiskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    threat_category: str | None = None
    affected_rights: list[str] | None = None


class AssessmentIn(BaseModel):
    entity_type: str
    entity_id: str
    risk_id: str
    likelihood: int = Field(..., ge=1, le=5)
    impact: int = Field(..., ge=1, le=5)
    assessment_rationale: str = Field(..., min_length=5)
    residual_likelihood: int | None = Field(None, ge=1, le=5)
    residual_impact: int | None = Field(None, ge=1, le=5)
    residual_rationale: str | None = None


class MeasureIn(BaseModel):
    name: str
    code: str | None = None
    category: MeasureCategory = MeasureCategory.ORGANIZATIONAL
    description: str | None = None


class ImplementationIn(BaseModel):
    measure_id: str
    entity_type: str
    entity_id: str
    implementation_status: ImplementationStatus = ImplementationStatus.PLANNED
    implementation_notes: str | None = None
    owner_user_id: str | None = None
    last_verified_at: date | None = None
    next_review_at: date | None = None
    evidence_document_id: str | None = None


@router.get("/risk-matrix")
async def get_matrix(tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    return await RiskService(db).get_matrix(tenant_id)


@router.put("/risk-matrix")
async def save_matrix(
    body: MatrixIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await RiskService(db).save_matrix(tenant_id, user.sub or user.email, body.model_dump())
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/risks")
async def list_risks(tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    return [r.to_dict() for r in await RiskService(db).list_risks(tenant_id)]


@router.post("/risks", status_code=status.HTTP_201_CREATED)
async def create_risk(
    body: RiskIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.create")),
    db: AsyncSession = Depends(get_db),
):
    risk = await RiskService(db).create_risk(tenant_id, user.sub or user.email, **body.model_dump())
    return risk.to_dict()


@router.patch("/risks/{risk_id}")
async def update_risk(
    risk_id: str,
    body: RiskIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        risk = await RiskService(db).update_risk(tenant_id, risk_id, user.sub or user.email, **body.model_dump())
        return risk.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/risk-assessments")
async def list_assessments(
    entity_type: str | None = None,
    entity_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    rows = await RiskService(db).list_assessments(tenant_id, entity_type=entity_type, entity_id=entity_id)
    return [r.to_dict() for r in rows]


@router.post("/risk-assessments", status_code=status.HTTP_201_CREATED)
async def create_assessment(
    body: AssessmentIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await RiskService(db).create_assessment(tenant_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.patch("/risk-assessments/{assessment_id}")
async def update_assessment(
    assessment_id: str,
    body: AssessmentIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await RiskService(db).update_assessment(tenant_id, assessment_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/security-measures")
async def list_measures(tenant_id: str = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    return [m.to_dict() for m in await RiskService(db).list_measures(tenant_id)]


@router.post("/security-measures", status_code=status.HTTP_201_CREATED)
async def create_measure(
    body: MeasureIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await RiskService(db).create_measure(tenant_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/measure-implementations")
async def list_implementations(
    entity_type: str | None = None,
    entity_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    rows = await RiskService(db).list_implementations(tenant_id, entity_type=entity_type, entity_id=entity_id)
    return [r.to_dict() for r in rows]


@router.post("/measure-implementations", status_code=status.HTTP_201_CREATED)
async def create_implementation(
    body: ImplementationIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("risk.create")),
    db: AsyncSession = Depends(get_db),
):
    item = await RiskService(db).upsert_implementation(tenant_id, user.sub or user.email, **body.model_dump())
    return item.to_dict()
