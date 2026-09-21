"""
PrivacyPA — Registro dei trattamenti (§11-15 della specifica).

Nodo centrale del sistema: un trattamento collega struttura organizzativa,
finalità, basi giuridiche, interessati e categorie di dati. DPIA, breach,
fornitori e flussi arriveranno come relazioni successive, senza duplicare
questa anagrafica.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ProcessingStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class ConfidentialityLevel(str, enum.Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    # §134: solo per documenti, violazioni, DPIA e progetti (trattamenti e sistemi non lo accettano).
    HIGHLY_RESTRICTED = "HIGHLY_RESTRICTED"


ALLOWED_STATUS_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.DRAFT: {ProcessingStatus.IN_REVIEW},
    ProcessingStatus.IN_REVIEW: {ProcessingStatus.DRAFT, ProcessingStatus.APPROVED},
    ProcessingStatus.APPROVED: {ProcessingStatus.ACTIVE},
    ProcessingStatus.ACTIVE: {ProcessingStatus.IN_REVIEW, ProcessingStatus.SUSPENDED, ProcessingStatus.ARCHIVED},
    ProcessingStatus.SUSPENDED: {ProcessingStatus.ACTIVE, ProcessingStatus.ARCHIVED},
    ProcessingStatus.ARCHIVED: set(),
}


class ProcessingActivity(Base):
    """Attività di trattamento (§11.1 processing_activities)."""

    __tablename__ = "privacypa_processing_activities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProcessingStatus] = mapped_column(
        SAEnum(ProcessingStatus, native_enum=False, length=20),
        nullable=False,
        default=ProcessingStatus.DRAFT,
    )
    organizational_unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    privacy_referent_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    purpose_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_of_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    collection_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    mandatory_provision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    automated_decision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profiling: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20),
        nullable=False,
        default=ConfidentialityLevel.INTERNAL,
    )

    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Provenienza (§68, §178): MANUAL o LEGACY_IMPORT; `source_reference` punta a job e riga di origine.
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="MANUAL", server_default="MANUAL")
    source_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Dati del file legacy non modellati o non associati (responsabile, conservazione, valori non risolti).
    legacy_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    purposes: Mapped[list["ProcessingPurpose"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", order_by="ProcessingPurpose.order_no"
    )
    legal_bases: Mapped[list["ProcessingLegalBasis"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    data_subjects: Mapped[list["ProcessingDataSubject"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )
    data_categories: Mapped[list["ProcessingDataCategory"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uix_privacypa_processing_code"),
    )

    def to_dict(self, include_relations: bool = False) -> dict:
        payload = {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "status": self.status.value if self.status else None,
            "organizational_unit_id": self.organizational_unit_id,
            "owner_user_id": self.owner_user_id,
            "privacy_referent_user_id": self.privacy_referent_user_id,
            "purpose_summary": self.purpose_summary,
            "source_of_data": self.source_of_data,
            "collection_method": self.collection_method,
            "mandatory_provision": self.mandatory_provision,
            "automated_decision": self.automated_decision,
            "profiling": self.profiling,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "last_review_date": self.last_review_date.isoformat() if self.last_review_date else None,
            "next_review_date": self.next_review_date.isoformat() if self.next_review_date else None,
            "confidentiality_level": self.confidentiality_level.value if self.confidentiality_level else None,
            "version_no": self.version_no,
            "is_current": self.is_current,
            "source": self.source,
            "source_reference": self.source_reference,
            "legacy_notes": self.legacy_notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
        if include_relations:
            payload["purposes"] = [p.to_dict() for p in self.purposes]
            payload["legal_bases"] = [b.to_dict() for b in self.legal_bases]
            payload["data_subjects"] = [s.to_dict() for s in self.data_subjects]
            payload["data_categories"] = [c.to_dict() for c in self.data_categories]
        return payload


class ProcessingPurpose(Base):
    """Finalità di un trattamento (§12)."""

    __tablename__ = "privacypa_processing_purposes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    activity: Mapped["ProcessingActivity"] = relationship(back_populates="purposes")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "description": self.description,
            "order_no": self.order_no,
        }


class ProcessingLegalBasis(Base):
    """Basi giuridiche collegate a un trattamento (§13 processing_legal_bases)."""

    __tablename__ = "privacypa_processing_legal_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    legal_basis_code: Mapped[str] = mapped_column(
        String(30), ForeignKey("privacypa_legal_basis_catalog.code"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    activity: Mapped["ProcessingActivity"] = relationship(back_populates="legal_bases")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "legal_basis_code": self.legal_basis_code,
            "notes": self.notes,
        }


class ProcessingDataSubject(Base):
    """Categorie di interessati collegate a un trattamento (§14)."""

    __tablename__ = "privacypa_processing_data_subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    category_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_data_subject_category_catalog.id"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approx_volume_range: Mapped[str | None] = mapped_column(String(50), nullable=True)

    activity: Mapped["ProcessingActivity"] = relationship(back_populates="data_subjects")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "category_id": self.category_id,
            "notes": self.notes,
            "approx_volume_range": self.approx_volume_range,
        }


class ProcessingDataCategory(Base):
    """Categorie di dati collegate a un trattamento (§15)."""

    __tablename__ = "privacypa_processing_data_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    data_category_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_data_category_catalog.id"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    activity: Mapped["ProcessingActivity"] = relationship(back_populates="data_categories")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "data_category_id": self.data_category_id,
            "notes": self.notes,
        }


class ProcessingActivityVersion(Base):
    """Snapshot immutabile di un trattamento (§32: version table + audit log)."""

    __tablename__ = "privacypa_processing_activity_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self, include_snapshot: bool = True) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "processing_activity_id": self.processing_activity_id,
            "version_no": self.version_no,
            "reason": self.reason,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }
        if include_snapshot:
            data["snapshot"] = self.snapshot
        return data
