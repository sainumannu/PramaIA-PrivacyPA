"""
PrivacyPA — Export completo dei dati dell'ente (§173): solo amministratori, con audit obbligatorio.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_confidentiality import require_admin_step_up
from backend.auth.privacypa_permissions import require_permission
from backend.db.database import get_db
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_export_service import build_tenant_export
from backend.services.privacypa_sbom import FORMATS, build

router = APIRouter(prefix="/api/privacypa/export", tags=["PrivacyPA - Export"])


@router.post("/tenant")
async def export_tenant(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("export.administer")),
    db: AsyncSession = Depends(get_db),
):
    """Pacchetto ZIP con dati (JSON), documenti, manifest e checksum. Ogni richiesta è registrata in audit."""
    actor = user.sub or user.email
    await require_admin_step_up(db, tenant_id, user)                 # §136: export dei dati e dell'audit
    path, manifest = await build_tenant_export(db, tenant_id, actor)
    await AuditService(db).log(
        tenant_id, "tenant", "export", event_type="tenant.export", entity_id=None, actor_user_id=actor,
        metadata={
            "tables": len(manifest["tables"]),
            "rows": sum(t["rows"] for t in manifest["tables"].values()),
            "documents": manifest["documents"]["included"],
            "documents_skipped": len(manifest["documents"]["skipped"]),
            "redacted_rows": sum(manifest["redacted"]["rows"].values()),
        },
    )
    filename = f"privacypa-export-{tenant_id}-{manifest['extracted_at'][:10]}.zip"
    return FileResponse(
        path, media_type="application/zip", filename=filename, background=BackgroundTask(os.unlink, path)
    )


@router.get("/sbom")
async def export_sbom(
    format: str = Query("cyclonedx", description="cyclonedx (1.5) o spdx (2.3)"),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("export.view")),
    db: AsyncSession = Depends(get_db),
):
    """Software Bill of Materials (§137) dell'istanza in esecuzione: pacchetti Python (versioni installate) e, se presente, il lockfile del frontend."""
    if format not in FORMATS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Formato non supportato: {format} (ammessi: {', '.join(FORMATS)})")
    document = await run_in_threadpool(build, format)
    count = len(document["components"]) if format == "cyclonedx" else len(document["packages"]) - 1
    await AuditService(db).log(
        tenant_id, "system", "export_sbom", event_type="system.sbom.export", actor_user_id=user.sub or user.email,
        metadata={"format": format, "components": count},
    )
    version = document["metadata"]["component"]["version"] if format == "cyclonedx" else document["name"].split("-", 1)[1]
    suffix = "cdx" if format == "cyclonedx" else "spdx"
    return JSONResponse(document, headers={"Content-Disposition": f'attachment; filename="privacypa-{version}.{suffix}.json"'})
