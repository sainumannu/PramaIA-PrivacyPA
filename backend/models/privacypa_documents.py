"""PrivacyPA — Documenti ed evidenze (§31). Il blob è separato dai metadati."""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base
from backend.models.privacypa_processing import ConfidentialityLevel


def _uuid() -> str:
    return str(uuid.uuid4())


class DocumentType(str, enum.Enum):
    POLICY = "POLICY"
    AGREEMENT_ART28 = "AGREEMENT_ART28"
    NOTICE = "NOTICE"
    DPIA = "DPIA"
    EVIDENCE = "EVIDENCE"
    REGISTER_EXTRACT = "REGISTER_EXTRACT"
    OTHER = "OTHER"


class DocumentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CURRENT = "CURRENT"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class Document(Base):
    __tablename__ = "privacypa_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        SAEnum(DocumentType, native_enum=False, length=30),
        nullable=False,
        default=DocumentType.OTHER,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, native_enum=False, length=20),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20),
        nullable=False,
        default=ConfidentialityLevel.INTERNAL,
    )
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    links: Mapped[list["DocumentLink"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_document_code"),)

    def to_dict(self, include_relations: bool = False) -> dict:
        data = {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "document_type": self.document_type.value if self.document_type else None,
            "status": self.status.value if self.status else None,
            "confidentiality_level": self.confidentiality_level.value if self.confidentiality_level else None,
            "current_version_id": self.current_version_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        loaded = sa_inspect(self).dict
        if include_relations:
            if "versions" in loaded:
                data["versions"] = [v.to_dict() for v in self.versions]
            if "links" in loaded:
                data["links"] = [lnk.to_dict() for lnk in self.links]
        elif "versions" in loaded:
            current = next((v for v in self.versions if v.id == self.current_version_id), None)
            if current:
                data["current_version"] = current.to_dict()
        return data


class DocumentVersion(Base):
    __tablename__ = "privacypa_document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_documents.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Esito della scansione antimalware (§148): scaricabile solo se CLEAN.
    scan_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="QUARANTINED", server_default="QUARANTINED"
    )
    scan_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="versions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "version_no": self.version_no,
            "file_name": self.file_name,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "notes": self.notes,
            "scan_status": self.scan_status,
            "scan_detail": self.scan_detail,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
        }


class DocumentLink(Base):
    __tablename__ = "privacypa_document_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_documents.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(50), nullable=False, default="evidence")

    document: Mapped["Document"] = relationship(back_populates="links")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "relation_type": self.relation_type,
        }
