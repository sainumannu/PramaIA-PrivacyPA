"""
PrivacyPA — Import del Registro da Excel/CSV (§67, §68, MoSCoW MUST).

Wizard: 1) upload → 2) scelta foglio e mapping colonne → 3) dry-run con anomalie e anteprima →
4) conferma. L'import crea solo trattamenti in bozza (`source = LEGACY_IMPORT`).
"""
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import (
    get_accessible_unit_ids,
    is_portal_global_or_tenant_admin,
    require_tenant,
)
from backend.auth.privacypa_permissions import require_permission, roles_for
from backend.db.database import get_db
from backend.services.privacypa_import_parser import MAX_IMPORT_BYTES, ImportFileError, read_tabular
from backend.services.privacypa_import_service import (
    ImportFailed,
    ImportFlowError,
    ImportService,
)

router = APIRouter(prefix="/api/privacypa/imports/processing-activities", tags=["PrivacyPA - Import"])


class DryRunIn(BaseModel):
    sheet: str
    mapping: dict[str, str] = Field(..., description="campo di destinazione -> intestazione di colonna")
    on_duplicate: Literal["skip", "import"] = "skip"
    skip_invalid_rows: bool = False


class CommitIn(BaseModel):
    confirm: bool = False


async def import_access(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("import.create")),
    db: AsyncSession = Depends(get_db),
) -> TokenPayload:
    # Import massivo: richiede il permesso `import.create` con visibilità sull'intero ente.
    scope = await get_accessible_unit_ids(
        db,
        tenant_id,
        user.sub or user.email,
        is_portal_global_or_tenant_admin(user),
        roles=await roles_for(db, tenant_id, "import.create"),
    )
    if scope is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "L'import richiede visibilità sull'intero ente")
    return user


async def _job(db: AsyncSession, tenant_id: str, job_id: str):
    job = await ImportService(db).get(tenant_id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import non trovato")
    return job


def _flow(exc: ImportFlowError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        job, sheets = await ImportService(db).create_job(
            tenant_id, user.sub or user.email, file.filename or "import", content
        )
    except ImportFileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"job": job.to_dict(), "sheets": ImportService.describe_sheets(sheets)}


@router.get("")
async def list_jobs(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    return [j.to_dict() for j in await ImportService(db).list_jobs(tenant_id)]


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    return (await _job(db, tenant_id, job_id)).to_dict()


@router.get("/{job_id}/sheets")
async def get_sheets(
    job_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    svc = ImportService(db)
    job = await _job(db, tenant_id, job_id)
    try:
        sheets = read_tabular(svc._read_file(job), job.filename)
    except ImportFlowError as e:
        raise _flow(e)
    except ImportFileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return ImportService.describe_sheets(sheets)


@router.post("/{job_id}/dry-run")
async def dry_run(
    job_id: str,
    body: DryRunIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    job = await _job(db, tenant_id, job_id)
    try:
        report = await ImportService(db).dry_run(
            job, user.sub or user.email, body.sheet, body.mapping,
            {"on_duplicate": body.on_duplicate, "skip_invalid_rows": body.skip_invalid_rows},
        )
    except ImportFlowError as e:
        raise _flow(e)
    except ImportFileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"job_id": job.id, "status": job.status.value, **report}


@router.post("/{job_id}/commit")
async def commit(
    job_id: str,
    body: CommitIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    if not body.confirm:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Confermare esplicitamente l'import (confirm: true)")
    job = await _job(db, tenant_id, job_id)
    try:
        result = await ImportService(db).commit(job, user.sub or user.email)
    except ImportFlowError as e:
        raise _flow(e)
    except ImportFileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except ImportFailed:
        await db.commit()  # conserva lo stato FAILED e l'audit; i dati importati sono già stati annullati
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Import annullato per un errore imprevisto: nessun trattamento è stato creato",
        )
    return {"job_id": job.id, "status": job.status.value, **result}


@router.delete("/{job_id}")
async def discard(
    job_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(import_access),
    db: AsyncSession = Depends(get_db),
):
    job = await _job(db, tenant_id, job_id)
    try:
        await ImportService(db).discard(job, user.sub or user.email)
    except ImportFlowError as e:
        raise _flow(e)
    return job.to_dict()
