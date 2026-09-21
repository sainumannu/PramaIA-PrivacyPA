"""PrivacyPA — Cataloghi di base: basi giuridiche, categorie interessati, categorie dati (§13-15)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.db.database import get_db
from backend.models.privacypa_catalog import GdprDataClass
from backend.services.privacypa_catalog_service import CatalogService

router = APIRouter(
    prefix="/api/privacypa/catalogs",
    tags=["PrivacyPA - Cataloghi"],
    dependencies=[Depends(require_module_view("catalog"))],
)


class LegalBasisIn(BaseModel):
    code: str = Field(..., min_length=3, max_length=30)
    article: str
    paragraph: str | None = None
    letter: str | None = None
    description: str


class SubjectIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class DataCategoryIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    gdpr_class: GdprDataClass = GdprDataClass.ORDINARY
    parent_id: str | None = None
    description: str | None = None


class ImportRows(BaseModel):
    rows: list[dict]


@router.get("/legal-basis")
async def list_legal_basis(db: AsyncSession = Depends(get_db)):
    return [i.to_dict() for i in await CatalogService(db).list_legal_basis()]


@router.post("/legal-basis", status_code=status.HTTP_201_CREATED)
async def create_legal_basis(
    body: LegalBasisIn,
    user: TokenPayload = Depends(require_permission("catalog.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await CatalogService(db).create_legal_basis(user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/data-subject-categories")
async def list_data_subject_categories(
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [i.to_dict() for i in await CatalogService(db).list_data_subject_categories(tenant_id)]


@router.post("/data-subject-categories", status_code=status.HTTP_201_CREATED)
async def create_subject(
    body: SubjectIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await CatalogService(db).create_subject(tenant_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.patch("/data-subject-categories/{item_id}")
async def update_subject(
    item_id: str,
    body: SubjectIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await CatalogService(db).update_subject(tenant_id, item_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/data-subject-categories/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subject(
    item_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await CatalogService(db).delete_subject(tenant_id, item_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/data-subject-categories/import")
async def import_subjects(
    body: ImportRows,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.create")),
    db: AsyncSession = Depends(get_db),
):
    created = await CatalogService(db).import_subjects(tenant_id, user.sub or user.email, body.rows)
    return {"created": created}


@router.get("/data-categories")
async def list_data_categories(
    tenant_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return [i.to_dict() for i in await CatalogService(db).list_data_categories(tenant_id)]


@router.post("/data-categories", status_code=status.HTTP_201_CREATED)
async def create_data_category(
    body: DataCategoryIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await CatalogService(db).create_data_category(tenant_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.patch("/data-categories/{item_id}")
async def update_data_category(
    item_id: str,
    body: DataCategoryIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.edit")),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await CatalogService(db).update_data_category(tenant_id, item_id, user.sub or user.email, **body.model_dump())
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/data-categories/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_category(
    item_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.delete")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await CatalogService(db).delete_data_category(tenant_id, item_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/data-categories/import")
async def import_data_categories(
    body: ImportRows,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("catalog.create")),
    db: AsyncSession = Depends(get_db),
):
    created = await CatalogService(db).import_data_categories(tenant_id, user.sub or user.email, body.rows)
    return {"created": created}
