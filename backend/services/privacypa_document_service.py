import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_documents import Document, DocumentLink, DocumentStatus, DocumentVersion
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_entity
from backend.services.privacypa_scanner import ScanStatus, scan_bytes

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ODT = "application/vnd.oasis.opendocument.text"

# Estensione -> tipo atteso. Il tipo dichiarato dal client non è mai usato.
EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".docx": _DOCX,
    ".odt": _ODT,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".csv": "text/csv",
}
ALLOWED_MIME = set(EXTENSION_MIME.values())
MAX_BYTES = 15 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
STORAGE_ROOT = Path("data/documents")

_EDITABLE = {"title", "document_type", "status", "confidentiality_level"}


def sanitize_filename(name: str | None) -> str:
    """Nome file sicuro da mostrare/scaricare: niente percorsi, controlli o nomi vuoti."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().strip(".")
    base = re.sub(r"\s+", " ", base)
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 200 - len(ext) - 1] + dot + ext) if dot else base[:200]
    return base or "upload.bin"


def _sniff(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"PK\x03\x04"):
        return _sniff_zip(content)
    if b"\x00" not in content:
        for encoding in ("utf-8", "cp1252"):
            try:
                content.decode(encoding)
                return "text/plain"
            except UnicodeDecodeError:
                continue
    return None


def _sniff_zip(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
                return None
            names = {i.filename for i in infos}
            if "[Content_Types].xml" in names and any(n.startswith("word/") for n in names):
                return _DOCX
            if "mimetype" in names and zf.read("mimetype").strip() == _ODT.encode():
                return _ODT
    except (zipfile.BadZipFile, RuntimeError):
        return None
    return None


def detect_mime(content: bytes, filename: str) -> str:
    """Tipo effettivo del file, verificato su estensione e contenuto (§55.5)."""
    ext = PurePosixPath(filename).suffix.lower()
    expected = EXTENSION_MIME.get(ext)
    if expected is None:
        raise ValueError(f"Estensione non consentita: {ext or '(nessuna)'}")
    detected = _sniff(content)
    if detected is None:
        raise ValueError("Contenuto del file non riconosciuto o non consentito")
    text_like = {"text/plain", "text/csv"}
    if detected != expected and not (detected in text_like and expected in text_like):
        raise ValueError("Il contenuto del file non corrisponde all'estensione")
    return expected


def _safe_path(key: str) -> Path:
    root = STORAGE_ROOT.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Percorso di storage non valido")
    return path


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Document).where(Document.tenant_id == tenant_id, Document.deleted_at.is_(None))

    async def list_documents(self, tenant_id: str, search: str | None = None) -> list[Document]:
        stmt = self._q(tenant_id).options(selectinload(Document.versions), selectinload(Document.links))
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(Document.title.ilike(like) | Document.code.ilike(like))
        result = await self.db.execute(stmt.order_by(Document.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, document_id: str) -> Document | None:
        result = await self.db.execute(
            self._q(tenant_id)
            .where(Document.id == document_id)
            .options(selectinload(Document.versions), selectinload(Document.links))
        )
        return result.scalar_one_or_none()

    async def create(self, tenant_id: str, actor: str, title: str, **fields) -> Document:
        links = fields.pop("links", None)
        for lnk in links or []:
            await ensure_entity(self.db, tenant_id, lnk.get("entity_type"), lnk.get("entity_id"))
        doc = Document(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, Document, tenant_id, "DOC"),
            title=title.strip(),
            created_by=actor,
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(doc)
        await self.db.flush()
        if links:
            for lnk in links:
                if lnk.get("entity_type") and lnk.get("entity_id"):
                    self.db.add(DocumentLink(
                        document_id=doc.id,
                        entity_type=lnk["entity_type"],
                        entity_id=lnk["entity_id"],
                        relation_type=lnk.get("relation_type") or "evidence",
                    ))
        await self.db.flush()
        await self.audit.log(tenant_id, "document", "create", entity_id=doc.id, actor_user_id=actor, new_values=doc.to_dict())
        return await self.get(tenant_id, doc.id)

    async def update(self, tenant_id: str, document_id: str, actor: str, **fields) -> Document:
        doc = await self.get(tenant_id, document_id)
        if not doc:
            raise ValueError("Documento non trovato")
        links = fields.pop("links", None)
        for lnk in links or []:
            await ensure_entity(self.db, tenant_id, lnk.get("entity_type"), lnk.get("entity_id"))
        old = doc.to_dict(include_relations=True)
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(doc, k, v)
        if links is not None:
            doc.links.clear()
            await self.db.flush()
            for lnk in links:
                if lnk.get("entity_type") and lnk.get("entity_id"):
                    doc.links.append(DocumentLink(
                        entity_type=lnk["entity_type"],
                        entity_id=lnk["entity_id"],
                        relation_type=lnk.get("relation_type") or "evidence",
                    ))
        await self.db.flush()
        await self.audit.log(tenant_id, "document", "update", entity_id=doc.id, actor_user_id=actor, old_values=old, new_values=doc.to_dict(include_relations=True))
        return await self.get(tenant_id, document_id)

    async def add_version(
        self,
        tenant_id: str,
        document_id: str,
        actor: str,
        file_name: str,
        content: bytes,
        effective_date: date | None = None,
        expiry_date: date | None = None,
        notes: str | None = None,
    ) -> DocumentVersion:
        """
        Registra una nuova versione. Il file viene salvato con chiave casuale (mai
        derivata dal nome del client), verificato su estensione e contenuto e
        sottoposto a scansione: resta scaricabile solo se CLEAN (§148).
        """
        safe_name = sanitize_filename(file_name)
        if not content:
            raise ValueError("File vuoto")
        if len(content) > MAX_BYTES:
            raise ValueError("File troppo grande (max 15 MB)")
        mime_type = detect_mime(content, safe_name)
        doc = await self.get(tenant_id, document_id)
        if not doc:
            raise ValueError("Documento non trovato")

        scan_status, scan_detail = await scan_bytes(content)
        version_no = max((v.version_no for v in doc.versions), default=0) + 1
        version = DocumentVersion(
            document_id=doc.id,
            version_no=version_no,
            file_name=safe_name,
            mime_type=mime_type,
            file_size=len(content),
            storage_key="pending",
            sha256=hashlib.sha256(content).hexdigest(),
            uploaded_by=actor,
            effective_date=effective_date,
            expiry_date=expiry_date,
            notes=notes,
            scan_status=scan_status.value,
            scan_detail=scan_detail,
            scanned_at=datetime.utcnow() if scan_status != ScanStatus.QUARANTINED else None,
        )
        self.db.add(version)
        await self.db.flush()

        key = f"{tenant_id}/{doc.id}/{version.id}"
        dest = _safe_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "xb") as fh:  # mai sovrascrivere un file esistente
            fh.write(content)
        version.storage_key = key

        if scan_status != ScanStatus.INFECTED:
            doc.current_version_id = version.id
            if expiry_date and expiry_date < date.today():
                doc.status = DocumentStatus.EXPIRED
            elif doc.status == DocumentStatus.DRAFT:
                doc.status = DocumentStatus.CURRENT
        await self.db.flush()
        await self.audit.log(
            tenant_id, "document", "upload_version", entity_id=doc.id, actor_user_id=actor,
            new_values=version.to_dict(),
        )
        await self.db.refresh(version)
        return version

    async def rescan_version(self, tenant_id: str, document_id: str, version_id: str, actor: str) -> DocumentVersion:
        doc = await self.get(tenant_id, document_id)
        version = next((v for v in (doc.versions if doc else []) if v.id == version_id), None)
        if version is None:
            raise ValueError("Versione non trovata")
        path = self.resolve_path(version)
        if not path.exists():
            raise ValueError("File non presente nello storage")
        old_status = version.scan_status
        status, detail = await scan_bytes(path.read_bytes())
        version.scan_status = status.value
        version.scan_detail = detail
        version.scanned_at = datetime.utcnow() if status != ScanStatus.QUARANTINED else None
        await self.db.flush()
        await self.audit.log(
            tenant_id, "document", "rescan_version", entity_id=doc.id, actor_user_id=actor,
            old_values={"scan_status": old_status}, new_values={"scan_status": status.value, "version_id": version.id},
        )
        return version

    def resolve_path(self, version: DocumentVersion) -> Path:
        return _safe_path(version.storage_key)

    async def expiring(self, tenant_id: str, within_days: int = 30) -> list[Document]:
        from datetime import timedelta
        limit = date.today() + timedelta(days=within_days)
        docs = await self.list_documents(tenant_id)
        out = []
        for d in docs:
            current = next((v for v in d.versions if v.id == d.current_version_id), None)
            if current and current.expiry_date and current.expiry_date <= limit:
                out.append(d)
        return out

    async def soft_delete(self, tenant_id: str, document_id: str, actor: str) -> None:
        doc = await self.get(tenant_id, document_id)
        if not doc:
            raise ValueError("Documento non trovato")
        doc.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "document", "delete", entity_id=doc.id, actor_user_id=actor)
