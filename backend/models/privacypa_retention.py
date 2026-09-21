"""
PrivacyPA — Conservazione dei dati (§21, §65).

Ogni regola descrive per quanto tempo, con quale criterio e con quale metodo di fine ciclo i dati di un
trattamento (o di una categoria di dati del trattamento) vengono conservati.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class RetentionUnit(str, enum.Enum):
    DAYS = "DAYS"
    MONTHS = "MONTHS"
    YEARS = "YEARS"
    EVENT_BASED = "EVENT_BASED"
    PERMANENT = "PERMANENT"


class DisposalMethod(str, enum.Enum):
    DELETION = "DELETION"
    ANONYMIZATION = "ANONYMIZATION"
    ARCHIVAL = "ARCHIVAL"
    TRANSFER_TO_ARCHIVE = "TRANSFER_TO_ARCHIVE"
    OTHER = "OTHER"


class RetentionRule(Base):
    __tablename__ = "privacypa_retention_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    data_category_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_data_category_catalog.id"), nullable=True
    )
    record_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retention_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_unit: Mapped[RetentionUnit] = mapped_column(SAEnum(RetentionUnit, native_enum=False, length=20), nullable=False)
    retention_criterion: Mapped[str] = mapped_column(Text, nullable=False)
    start_trigger: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Riferimento normativo in chiaro (es. "art. 2220 c.c."): il catalogo strutturato dei riferimenti (§13) non esiste ancora.
    legal_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Riferimento strutturato del catalogo dell'ente (§13); il testo libero resta per i casi non censiti.
    legal_reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    disposal_method: Mapped[DisposalMethod] = mapped_column(
        SAEnum(DisposalMethod, native_enum=False, length=30), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "processing_activity_id": self.processing_activity_id,
            "data_category_id": self.data_category_id,
            "record_class": self.record_class,
            "retention_value": self.retention_value,
            "retention_unit": self.retention_unit.value if self.retention_unit else None,
            "retention_criterion": self.retention_criterion,
            "start_trigger": self.start_trigger,
            "legal_reference": self.legal_reference,
            "legal_reference_id": self.legal_reference_id,
            "disposal_method": self.disposal_method.value if self.disposal_method else None,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
