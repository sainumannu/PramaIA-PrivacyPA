from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_confidentiality import assert_step_up, load_level_access
from backend.auth.privacypa_permissions import require_module_view, require_permission, roles_for
from backend.auth.privacypa_scope import PROCESSING_LINK_TYPES, VisibilityScope, build_scope
from backend.db.database import get_db
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_documents import DocumentStatus, DocumentType
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_document_service import MAX_BYTES, DocumentService
from backend.services.privacypa_watermark import WatermarkUnsupported, apply_watermark, watermark_text
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/documents",
    tags=["PrivacyPA - Documenti"],
    dependencies=[Depends(require_module_view("document"))],
)


class DocumentLinkIn(BaseModel):
    entity_type: str
    entity_id: str
    relation_type: str = "evidence"


class DocumentUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    document_type: DocumentType = DocumentType.OTHER
    status: DocumentStatus = DocumentStatus.DRAFT
    # Assente = invariato (in creazione INTERNAL): una modifica che lo omette non abbassa la riservatezza.
    confidentiality_level: ConfidentialityLevel | None = None
    links: list[DocumentLinkIn] | None = None


def _document_data(body: DocumentUpsert) -> dict:
    data = body.model_dump()
    if data.get("confidentiality_level") is None:
        data.pop("confidentiality_level", None)
    return data


def _assert_link_scope(scope: VisibilityScope, links: list[DocumentLinkIn] | None) -> None:
    """Un utente con scope limitato non collega documenti a trattamenti fuori dal suo scope."""
    for lnk in links or []:
        if lnk.entity_type in PROCESSING_LINK_TYPES:
            scope.assert_processing(lnk.entity_id)


async def _scope_for(db: AsyncSession, tenant_id: str, user: TokenPayload, code: str | None) -> VisibilityScope:
    """Scope di lettura, o con `code` quello dei soli ruoli che hanno quel permesso (il ruolo vale sul proprio scope)."""
    if code is None:
        return await build_scope(db, tenant_id, user)
    return await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, code))


async def _get_visible_document(
    db: AsyncSession, tenant_id: str, user: TokenPayload, document_id: str, version: int | None = None,
    write_code: str | None = None,
):
    """Carica il documento (404) verificando la visibilità per scope (403, AC-DOC-003); per le scritture, lo scope del permesso."""
    doc = await DocumentService(db).get(tenant_id, document_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento non trovato")
    scope = await _scope_for(db, tenant_id, user, write_code)
    if not scope.document_visible(doc):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    if version is not None:
        assert_version(doc, version)
    return doc


@router.get("")
async def list_documents(
    search: str | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    docs = scope.filter_documents(await DocumentService(db).list_documents(tenant_id, search=search))
    return [d.to_dict() for d in docs]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("document.create")),
    db: AsyncSession = Depends(get_db),
):
    scope = await _scope_for(db, tenant_id, user, "document.create")
    _assert_link_scope(scope, body.links)
    scope.assert_level_change(None, body.confidentiality_level)
    data = _document_data(body)
    if data.get("links"):
        data["links"] = [lnk.model_dump() for lnk in body.links or []]
    doc = await DocumentService(db).create(tenant_id, user.sub or user.email, **data)
    return doc.to_dict(include_relations=True)


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    response: Response,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_visible_document(db, tenant_id, user, document_id)
    response.headers["ETag"] = etag_of(doc)
    return doc.to_dict(include_relations=True)


@router.patch("/{document_id}")
async def update_document(
    document_id: str,
    body: DocumentUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("document.edit")),
    db: AsyncSession = Depends(get_db),
):
    current = await _get_visible_document(db, tenant_id, user, document_id, version, "document.edit")
    scope = await _scope_for(db, tenant_id, user, "document.edit")
    _assert_link_scope(scope, body.links)
    if body.confidentiality_level is not None:
        scope.assert_level_change(current.confidentiality_level, body.confidentiality_level, current.created_by)
    try:
        data = _document_data(body)
        if data.get("links") is not None:
            data["links"] = [lnk.model_dump() for lnk in body.links or []]
        doc = await DocumentService(db).update(tenant_id, document_id, user.sub or user.email, **data)
        return doc.to_dict(include_relations=True)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/{document_id}/versions")
async def upload_version(
    document_id: str,
    file: UploadFile = File(...),
    notes: str | None = Form(None),
    effective_date: date | None = Form(None),
    expiry_date: date | None = Form(None),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("document.create")),
    db: AsyncSession = Depends(get_db),
):
    await _get_visible_document(db, tenant_id, user, document_id, write_code="document.create")
    content = await file.read(MAX_BYTES + 1)
    try:
        version = await DocumentService(db).add_version(
            tenant_id,
            document_id,
            user.sub or user.email,
            file_name=file.filename or "upload.bin",
            content=content,
            effective_date=effective_date,
            expiry_date=expiry_date,
            notes=notes,
        )
        return version.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/{document_id}/file")
async def download_current(
    document_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = DocumentService(db)
    doc = await _get_visible_document(db, tenant_id, user, document_id)
    if not doc.current_version_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nessun file caricato")
    version = next((v for v in doc.versions if v.id == doc.current_version_id), None)
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versione non trovata")
    if version.scan_status != "CLEAN":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File non scaricabile: scansione antimalware in stato {version.scan_status}",
        )
    try:
        path = svc.resolve_path(version)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File non presente nello storage")
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File non presente nello storage")
    actor = user.sub or user.email
    # Livelli riservati (§135): step-up, tetto orario, filigrana; ogni download è comunque registrato con il livello.
    access = await load_level_access(db, tenant_id, user)
    rule = access.rule(doc.confidentiality_level)
    event_type, metadata, marked = None, {"version_id": version.id, "sha256": version.sha256}, None
    if rule:
        level = doc.confidentiality_level.value
        event_type = f"document.download.{level.lower()}"
        if rule["step_up"]:
            assert_step_up(user, access.config["step_up"]["max_age_seconds"])
        limit = rule["max_downloads_per_hour"]
        if limit:
            since = datetime.utcnow() - timedelta(hours=1)
            done = (await db.execute(select(func.count(AuditLog.id)).where(
                AuditLog.tenant_id == tenant_id, AuditLog.event_type == event_type, AuditLog.actor_user_id == actor,
                AuditLog.event_time >= since))).scalar_one()
            if done >= limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Raggiunto il tetto di {limit} download all'ora per i documenti {level}")
        metadata.update(level=level, watermark_mode=rule["watermark"], step_up=bool(rule["step_up"]))
        if rule["watermark"] != "off":
            try:
                marked = apply_watermark(path.read_bytes(), version.mime_type, version.file_name, watermark_text(level, actor, doc.code))
            except WatermarkUnsupported as e:
                if rule["watermark"] == "required":
                    raise HTTPException(status.HTTP_409_CONFLICT, "Il formato del file non consente la filigrana richiesta dal livello di riservatezza: download non ammesso") from e
            metadata["watermarked"] = marked is not None
    await AuditService(db).log(
        tenant_id, "document", "download", event_type=event_type, entity_id=doc.id, actor_user_id=actor, metadata=metadata,
    )
    if marked is not None:
        return RawResponse(marked, media_type=version.mime_type,
                           headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.file_name)}"})
    return FileResponse(path, media_type=version.mime_type, filename=version.file_name)


@router.post("/{document_id}/versions/{version_id}/rescan")
async def rescan_version(
    document_id: str,
    version_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("document.administer")),
    db: AsyncSession = Depends(get_db),
):
    """Ripete la scansione (es. dopo la configurazione dello scanner o su SCAN_FAILED)."""
    await _get_visible_document(db, tenant_id, user, document_id, write_code="document.administer")
    try:
        version = await DocumentService(db).rescan_version(
            tenant_id, document_id, version_id, user.sub or user.email
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return version.to_dict()


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("document.delete")),
    db: AsyncSession = Depends(get_db),
):
    await _get_visible_document(db, tenant_id, user, document_id, write_code="document.delete")
    try:
        await DocumentService(db).soft_delete(tenant_id, document_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
