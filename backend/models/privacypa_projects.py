"""
PrivacyPA — Privacy by design: nuovi progetti (§30).

Un progetto attraversa un workflow di revisioni (privacy, ICT/RTD, DPO se previsto), una DPIA se necessaria e
l'approvazione, poi entra in esercizio e viene rivisto dopo l'implementazione. Ogni giro di revisioni resta nello
storico (`cycle`): se una revisione chiede modifiche il progetto torna in proposta e riparte.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base
from backend.models.privacypa_processing import ConfidentialityLevel


def _uuid() -> str:
    return str(uuid.uuid4())


def _enum(cls, length=20):
    return SAEnum(cls, native_enum=False, length=length)


def _iso(value):
    return value.isoformat() if value else None


class ProjectStatus(str, enum.Enum):
    PROPOSAL = "PROPOSAL"
    PRIVACY_REVIEW = "PRIVACY_REVIEW"
    ICT_REVIEW = "ICT_REVIEW"
    DPO_REVIEW = "DPO_REVIEW"
    DPIA = "DPIA"
    APPROVAL = "APPROVAL"
    GO_LIVE = "GO_LIVE"
    POST_REVIEW = "POST_REVIEW"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = (ProjectStatus.CLOSED, ProjectStatus.REJECTED, ProjectStatus.CANCELLED)
# Stati in cui una revisione decide il passo successivo.
REVIEW_STATUSES = (
    ProjectStatus.PRIVACY_REVIEW, ProjectStatus.ICT_REVIEW, ProjectStatus.DPO_REVIEW, ProjectStatus.APPROVAL,
    ProjectStatus.POST_REVIEW,
)
# Da qui in poi gli elementi del progetto (trattamento, sistema, DPIA...) si possono generare.
GENERATION_STATUSES = (
    ProjectStatus.DPIA, ProjectStatus.APPROVAL, ProjectStatus.GO_LIVE, ProjectStatus.POST_REVIEW, ProjectStatus.CLOSED,
)

# Caratteristiche che aumentano il rischio: ne basta una perché serva lo screening della DPIA (§24, §30).
RISK_FLAGS = (
    "uses_ai", "profiles_people", "automated_decisions", "special_data", "criminal_data", "minors",
    "systematic_monitoring", "large_scale",
)
FLAGS = ("uses_cloud",) + RISK_FLAGS


class ReviewStage(str, enum.Enum):
    PRIVACY = "PRIVACY"
    ICT = "ICT"
    DPO = "DPO"
    APPROVAL = "APPROVAL"
    POST = "POST"


class ReviewOutcome(str, enum.Enum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"


class Project(Base):
    __tablename__ = "privacypa_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposing_unit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=False, index=True
    )
    project_owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(_enum(ProjectStatus), nullable=False, default=ProjectStatus.PROPOSAL)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20), nullable=False, default=ConfidentialityLevel.INTERNAL,
        server_default="INTERNAL",
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)

    uses_cloud: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uses_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    profiles_people: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    automated_decisions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    special_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    criminal_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minors: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    systematic_monitoring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    large_scale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Calcolati dai flag a ogni salvataggio (`assess`): non si impostano a mano.
    privacy_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dpia_screening_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    dpia_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_dpias.id"), nullable=True)
    go_live_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    post_review_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_project_code"),)

    def assess(self) -> dict:
        """Cosa richiedono le caratteristiche dichiarate: screening DPIA e revisione del DPO (§30)."""
        criteria = [f for f in RISK_FLAGS if getattr(self, f)]
        screening = bool(criteria)
        return {"criteria": criteria, "dpia_screening_required": screening, "dpo_review_required": screening,
                "privacy_review_required": True}

    def apply_assessment(self) -> None:
        result = self.assess()
        self.dpia_screening_required = result["dpia_screening_required"]
        self.privacy_review_required = result["privacy_review_required"]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "title": self.title, "description": self.description, "proposing_unit_id": self.proposing_unit_id,
            "project_owner_id": self.project_owner_id, "supplier_id": self.supplier_id, "status": self.status.value,
            "status_changed_at": _iso(self.status_changed_at), "cycle": self.cycle,
            "confidentiality_level": self.confidentiality_level.value,
            **{f: getattr(self, f) for f in FLAGS},
            "privacy_review_required": self.privacy_review_required, "dpia_screening_required": self.dpia_screening_required,
            "assessment": self.assess(), "dpia_id": self.dpia_id, "go_live_date": _iso(self.go_live_date),
            "post_review_due_date": _iso(self.post_review_due_date), "closed_at": _iso(self.closed_at),
            "created_by": self.created_by, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class ProjectReview(Base):
    __tablename__ = "privacypa_project_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_projects.id"), nullable=False, index=True)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage: Mapped[ReviewStage] = mapped_column(_enum(ReviewStage), nullable=False)
    outcome: Mapped[ReviewOutcome] = mapped_column(_enum(ReviewOutcome), nullable=False)
    reviewer_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "cycle": self.cycle, "stage": self.stage.value,
            "outcome": self.outcome.value, "reviewer_user_id": self.reviewer_user_id, "notes": self.notes,
            "decided_at": _iso(self.decided_at),
        }


class ProjectArtifact(Base):
    """Elemento creato dal progetto (trattamento, sistema, DPIA, task...): evita duplicati se si genera di nuovo."""

    __tablename__ = "privacypa_project_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_projects.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("project_id", "entity_type", "entity_id", name="uix_privacypa_project_artifact"),)

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id, "created_at": _iso(self.created_at)}
