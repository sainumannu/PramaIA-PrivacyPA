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


class DocumentInUse(ValueError):
    """Il documento è ancora collegato ad altri elementi (409): `references` li elenca."""

    def __init__(self, references: list[dict]):
        super().__init__(f"Il documento è collegato a {len(references)} elementi: rimuovere prima i collegamenti")
        self.references = references


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
        if status == ScanStatus.INFECTED and doc.current_version_id == version.id:
            # una versione infetta non resta corrente: torna la più recente non infetta, se c'è
            others = sorted((v for v in doc.versions if v.id != version.id and v.scan_status != ScanStatus.INFECTED.value),
                            key=lambda v: v.version_no)
            doc.current_version_id = others[-1].id if others else None
        await self.db.flush()
        await self.audit.log(
            tenant_id, "document", "rescan_version", entity_id=doc.id, actor_user_id=actor,
            old_values={"scan_status": old_status}, new_values={"scan_status": status.value, "version_id": version.id},
        )
        return version

    async def rescan_quarantined(self, actor: str = "system") -> dict[str, int]:
        """Rianalizza, in tutti gli enti, le versioni rimaste in quarantena perché mancava lo scanner."""
        rows = (await self.db.execute(
            select(Document.tenant_id, Document.id, DocumentVersion.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.scan_status == ScanStatus.QUARANTINED.value)
        )).all()
        outcome: dict[str, int] = {}
        for tenant_id, document_id, version_id in rows:
            try:
                version = await self.rescan_version(tenant_id, document_id, version_id, actor)
                outcome[version.scan_status] = outcome.get(version.scan_status, 0) + 1
            except ValueError:
                outcome["MISSING"] = outcome.get("MISSING", 0) + 1
        return outcome

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

    async def _referencing_rows(self, tenant_id: str, document_id: str) -> list[tuple[str, object, str]]:
        """(tipo, record, campo) degli elementi che indicano il documento come allegato, evidenza o origine."""
        # Import differiti: i modelli dei moduli funzionali si importano tra loro.
        from backend.models.privacypa_assessments import InternationalTransfer
        from backend.models.privacypa_compliance import CorrectiveAction
        from backend.models.privacypa_notices import PrivacyNotice, PrivacyNoticeVersion
        from backend.models.privacypa_parties import Contract, ProcessorAgreement
        from backend.models.privacypa_people import TrainingCompletion
        from backend.models.privacypa_risk import MeasureImplementation
        from backend.models.privacypa_tasks import PrivacyTask, TaskSourceType

        specs = [
            ("transfer", InternationalTransfer, "tia_document_id"),
            ("corrective_action", CorrectiveAction, "evidence_document_id"),
            ("processor_agreement", ProcessorAgreement, "document_id"),
            ("contract", Contract, "document_id"),
            ("training_completion", TrainingCompletion, "certificate_document_id"),
            ("measure_implementation", MeasureImplementation, "evidence_document_id"),
            ("task", PrivacyTask, "source_id"),
        ]
        rows: list[tuple[str, object, str]] = []
        for kind, model, field in specs:
            stmt = select(model).where(model.tenant_id == tenant_id, getattr(model, field) == document_id)
            if hasattr(model, "deleted_at"):
                stmt = stmt.where(model.deleted_at.is_(None))
            if model is PrivacyTask:
                stmt = stmt.where(PrivacyTask.source_type == TaskSourceType.DOCUMENT)
            rows += [(kind, r, field) for r in (await self.db.execute(stmt)).scalars()]
        versions = await self.db.execute(
            select(PrivacyNoticeVersion).join(PrivacyNotice, PrivacyNotice.id == PrivacyNoticeVersion.privacy_notice_id)
            .where(PrivacyNotice.tenant_id == tenant_id, PrivacyNotice.deleted_at.is_(None),
                   PrivacyNoticeVersion.document_id == document_id)
        )
        rows += [("notice_version", r, "document_id") for r in versions.scalars()]
        return rows

    async def _reference_label(self, kind: str, row) -> str:
        from backend.models.privacypa_notices import PrivacyNotice
        from backend.models.privacypa_parties import Party
        from backend.models.privacypa_people import TrainingCourse
        from backend.models.privacypa_risk import SecurityMeasure

        if kind in ("transfer", "corrective_action", "contract"):
            return f"{row.code} · {row.title}"
        if kind == "notice_version":
            notice = await self.db.get(PrivacyNotice, row.privacy_notice_id)
            return f"{notice.code} · {notice.title} (v{row.version_no})" if notice else f"v{row.version_no}"
        if kind == "processor_agreement":
            party = await self.db.get(Party, row.party_id)
            return party.legal_name if party else row.id
        if kind == "training_completion":
            course = await self.db.get(TrainingCourse, row.course_id)
            return f"{course.title if course else row.course_id} · {row.user_id}"
        if kind == "measure_implementation":
            measure = await self.db.get(SecurityMeasure, row.measure_id)
            return measure.name if measure else row.measure_id
        return row.title

    async def _evidence_label(self, tenant_id: str, link: DocumentLink) -> str:
        """Elemento di cui il documento è evidenza (collegamento del documento stesso)."""
        from backend.services.privacypa_refs import ENTITY_KINDS, _kinds

        kind = ENTITY_KINDS.get((link.entity_type or "").lower())
        if kind is not None:
            model = _kinds()[kind][0]
            row = await self.db.get(model, link.entity_id)
            if row is not None and getattr(row, "tenant_id", tenant_id) in (tenant_id, None):
                name = next((getattr(row, a) for a in ("title", "name", "legal_name") if getattr(row, a, None)), None)
                code = getattr(row, "code", None)
                return " · ".join(x for x in (code, name) if x) or link.entity_id
        return link.entity_id

    async def references(self, tenant_id: str, document_id: str) -> list[dict]:
        """Collegamenti del documento: da eliminarlo, questi elementi resterebbero senza allegato o evidenza."""
        doc = await self.get(tenant_id, document_id)
        if not doc:
            raise ValueError("Documento non trovato")
        out = [{"kind": kind, "id": row.id, "label": await self._reference_label(kind, row)}
               for kind, row, _ in await self._referencing_rows(tenant_id, document_id)]
        out += [{"kind": "evidence", "entity_type": link.entity_type, "id": link.entity_id,
                 "label": await self._evidence_label(tenant_id, link)} for link in doc.links]
        return out

    async def soft_delete(self, tenant_id: str, document_id: str, actor: str, unlink: bool = False) -> None:
        """Elimina il documento; se è ancora collegato serve `unlink`, che prima toglie i collegamenti (solo amministratori)."""
        doc = await self.get(tenant_id, document_id)
        if not doc:
            raise ValueError("Documento non trovato")
        refs = await self.references(tenant_id, document_id)
        if refs and not unlink:
            raise DocumentInUse(refs)
        if refs:
            from backend.models.privacypa_tasks import TaskSourceType

            for kind, row, field in await self._referencing_rows(tenant_id, document_id):
                setattr(row, field, None)
                if kind == "task":
                    row.source_type = TaskSourceType.MANUAL
            doc.links.clear()
            await self.audit.log(tenant_id, "document", "unlink", entity_id=doc.id, actor_user_id=actor,
                                 old_values={"references": refs})
        doc.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "document", "delete", entity_id=doc.id, actor_user_id=actor)
