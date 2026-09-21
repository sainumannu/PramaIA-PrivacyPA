"""
PrivacyPA — DPIA (§24) e trasferimenti internazionali / TIA (§46).

La DPIA è agganciata a un trattamento esistente (single source of truth).
Il motore di screening *suggerisce* se procedere; la decisione è sempre umana.
La TIA vive sul trasferimento: non è un secondo registro.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base
from backend.models.privacypa_processing import ConfidentialityLevel


def _uuid() -> str:
    return str(uuid.uuid4())


SCREENING_CRITERIA = [
    ("SCORING", "Valutazione o scoring degli interessati"),
    ("AUTOMATED_DECISION", "Decisioni automatizzate con effetti giuridici o analoghi"),
    ("SYSTEMATIC_MONITORING", "Monitoraggio sistematico"),
    ("SPECIAL_DATA", "Dati particolari art. 9 o giudiziari art. 10"),
    ("LARGE_SCALE", "Trattamento su larga scala"),
    ("DATASET_COMBINATION", "Combinazione o raffronto di dataset"),
    ("VULNERABLE", "Interessati vulnerabili"),
    ("INNOVATIVE_TECH", "Uso innovativo di tecnologia"),
    ("RIGHTS_OBSTACLE", "Impedimento all'esercizio di diritti o all'accesso a un servizio"),
]


class DpiaStatus(str, enum.Enum):
    SCREENING = "SCREENING"
    NOT_REQUIRED = "NOT_REQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    DPO_REVIEW = "DPO_REVIEW"
    AWAITING_DECISION = "AWAITING_DECISION"
    APPROVED = "APPROVED"
    TO_REVIEW = "TO_REVIEW"
    ARCHIVED = "ARCHIVED"


class ScreeningOutcome(str, enum.Enum):
    PROCEED = "PROCEED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNCERTAIN = "UNCERTAIN"


DPIA_TRANSITIONS: dict[DpiaStatus, set[DpiaStatus]] = {
    DpiaStatus.SCREENING: {DpiaStatus.NOT_REQUIRED, DpiaStatus.IN_PROGRESS},
    DpiaStatus.NOT_REQUIRED: {DpiaStatus.SCREENING, DpiaStatus.ARCHIVED},
    DpiaStatus.IN_PROGRESS: {DpiaStatus.DPO_REVIEW, DpiaStatus.SCREENING},
    DpiaStatus.DPO_REVIEW: {DpiaStatus.AWAITING_DECISION, DpiaStatus.IN_PROGRESS},
    DpiaStatus.AWAITING_DECISION: {DpiaStatus.APPROVED, DpiaStatus.IN_PROGRESS},
    DpiaStatus.APPROVED: {DpiaStatus.TO_REVIEW, DpiaStatus.ARCHIVED},
    DpiaStatus.TO_REVIEW: {DpiaStatus.IN_PROGRESS, DpiaStatus.APPROVED},
    DpiaStatus.ARCHIVED: set(),
}


class TransferMechanism(str, enum.Enum):
    ADEQUACY = "ADEQUACY"
    SCC = "SCC"
    BCR = "BCR"
    DEROGATION_49 = "DEROGATION_49"
    OTHER = "OTHER"


class TransferStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    TO_REVIEW = "TO_REVIEW"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


class TiaOutcome(str, enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    ACCEPTABLE = "ACCEPTABLE"
    ACCEPTABLE_WITH_MEASURES = "ACCEPTABLE_WITH_MEASURES"
    NOT_ACCEPTABLE = "NOT_ACCEPTABLE"
    PENDING = "PENDING"


class Dpia(Base):
    __tablename__ = "privacypa_dpias"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    processing_activity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[DpiaStatus] = mapped_column(
        SAEnum(DpiaStatus, native_enum=False, length=30), nullable=False, default=DpiaStatus.SCREENING
    )
    screening_outcome: Mapped[ScreeningOutcome | None] = mapped_column(
        SAEnum(ScreeningOutcome, native_enum=False, length=20), nullable=True
    )
    screening_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine_suggestion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    necessity_proportionality: Mapped[str | None] = mapped_column(Text, nullable=True)
    risks_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    measures_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    residual_risk_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dpo_opinion: Mapped[str | None] = mapped_column(Text, nullable=True)
    controller_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20), nullable=False, default=ConfidentialityLevel.INTERNAL,
        server_default="INTERNAL",
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_dpia_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "processing_activity_id": self.processing_activity_id,
            "title": self.title,
            "status": self.status.value if self.status else None,
            "screening_outcome": self.screening_outcome.value if self.screening_outcome else None,
            "screening_rationale": self.screening_rationale,
            "engine_suggestion": self.engine_suggestion,
            "description": self.description,
            "necessity_proportionality": self.necessity_proportionality,
            "risks_text": self.risks_text,
            "measures_text": self.measures_text,
            "residual_risk_text": self.residual_risk_text,
            "dpo_opinion": self.dpo_opinion,
            "controller_decision": self.controller_decision,
            "action_plan": self.action_plan,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "next_review_date": self.next_review_date.isoformat() if self.next_review_date else None,
            "version_no": self.version_no,
            "confidentiality_level": self.confidentiality_level.value if self.confidentiality_level else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DpiaScreeningAnswer(Base):
    __tablename__ = "privacypa_dpia_screening_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dpia_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_dpias.id"), nullable=False, index=True)
    criterion_code: Mapped[str] = mapped_column(String(40), nullable=False)
    answer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("dpia_id", "criterion_code", name="uix_privacypa_dpia_criterion"),)

    def to_dict(self) -> dict:
        label = next((l for c, l in SCREENING_CRITERIA if c == self.criterion_code), self.criterion_code)
        return {
            "id": self.id,
            "dpia_id": self.dpia_id,
            "criterion_code": self.criterion_code,
            "label": label,
            "answer": self.answer,
            "notes": self.notes,
        }


class InternationalTransfer(Base):
    """Trasferimento extra-SEE + TIA sul medesimo record (§46)."""

    __tablename__ = "privacypa_international_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    exporter_party_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=True)
    importer_party_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mechanism: Mapped[TransferMechanism] = mapped_column(
        SAEnum(TransferMechanism, native_enum=False, length=30),
        nullable=False,
        default=TransferMechanism.SCC,
    )
    adequacy_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    scc_module: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tia_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tia_outcome: Mapped[TiaOutcome] = mapped_column(
        SAEnum(TiaOutcome, native_enum=False, length=30),
        nullable=False,
        default=TiaOutcome.PENDING,
    )
    tia_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    law_access_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplementary_measures: Mapped[str | None] = mapped_column(Text, nullable=True)
    tia_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[TransferStatus] = mapped_column(
        SAEnum(TransferStatus, native_enum=False, length=20),
        nullable=False,
        default=TransferStatus.DRAFT,
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_transfer_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "exporter_party_id": self.exporter_party_id,
            "importer_party_id": self.importer_party_id,
            "country_code": self.country_code,
            "description": self.description,
            "mechanism": self.mechanism.value if self.mechanism else None,
            "adequacy_reference": self.adequacy_reference,
            "scc_module": self.scc_module,
            "tia_required": self.tia_required,
            "tia_outcome": self.tia_outcome.value if self.tia_outcome else None,
            "tia_rationale": self.tia_rationale,
            "law_access_assessment": self.law_access_assessment,
            "supplementary_measures": self.supplementary_measures,
            "tia_document_id": self.tia_document_id,
            "last_review_date": self.last_review_date.isoformat() if self.last_review_date else None,
            "next_review_date": self.next_review_date.isoformat() if self.next_review_date else None,
            "status": self.status.value if self.status else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TransferLink(Base):
    __tablename__ = "privacypa_transfer_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transfer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_international_transfers.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "transfer_id": self.transfer_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
        }
