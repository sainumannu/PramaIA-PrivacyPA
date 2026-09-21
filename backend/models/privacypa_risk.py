"""PrivacyPA — Analisi del rischio (§23) e misure di sicurezza (§22)."""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


DEFAULT_MATRIX = {
    "scale_min": 1,
    "scale_max": 5,
    "bands": [
        {"level": "LOW", "min_score": 1, "max_score": 4},
        {"level": "MEDIUM", "min_score": 5, "max_score": 9},
        {"level": "HIGH", "min_score": 10, "max_score": 16},
        {"level": "CRITICAL", "min_score": 17, "max_score": 25},
    ],
}


class MeasureCategory(str, enum.Enum):
    ACCESS_CONTROL = "ACCESS_CONTROL"
    AUTHENTICATION = "AUTHENTICATION"
    ENCRYPTION = "ENCRYPTION"
    LOGGING = "LOGGING"
    BACKUP = "BACKUP"
    CONTINUITY = "CONTINUITY"
    NETWORK = "NETWORK"
    ENDPOINT = "ENDPOINT"
    ORGANIZATIONAL = "ORGANIZATIONAL"
    TRAINING = "TRAINING"
    PHYSICAL = "PHYSICAL"
    DATA_MINIMIZATION = "DATA_MINIMIZATION"
    PSEUDONYMIZATION = "PSEUDONYMIZATION"
    INCIDENT_MANAGEMENT = "INCIDENT_MANAGEMENT"
    VENDOR_MANAGEMENT = "VENDOR_MANAGEMENT"


class ImplementationStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    PARTIAL = "PARTIAL"
    IMPLEMENTED = "IMPLEMENTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"


class RiskMatrixConfig(Base):
    """Matrice likelihood×impact configurabile per ente (§23)."""

    __tablename__ = "privacypa_risk_matrix"

    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "config": self.config or DEFAULT_MATRIX,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
        }


class Risk(Base):
    __tablename__ = "privacypa_risks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    threat_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    affected_rights: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_risk_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "threat_category": self.threat_category,
            "affected_rights": self.affected_rights or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RiskAssessment(Base):
    __tablename__ = "privacypa_risk_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    risk_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_risks.id"), nullable=False)
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=20), nullable=False
    )
    inherent_score: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    residual_likelihood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    residual_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    residual_level: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=20), nullable=True
    )
    residual_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    residual_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assessed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_risk_assessment_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "risk_id": self.risk_id,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "inherent_level": self.inherent_level.value if self.inherent_level else None,
            "inherent_score": self.inherent_score,
            "assessment_rationale": self.assessment_rationale,
            "residual_likelihood": self.residual_likelihood,
            "residual_impact": self.residual_impact,
            "residual_level": self.residual_level.value if self.residual_level else None,
            "residual_score": self.residual_score,
            "residual_rationale": self.residual_rationale,
            "assessed_by": self.assessed_by,
            "assessed_at": self.assessed_at.isoformat() if self.assessed_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "version_no": self.version_no,
        }


class SecurityMeasure(Base):
    __tablename__ = "privacypa_security_measures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[MeasureCategory] = mapped_column(
        SAEnum(MeasureCategory, native_enum=False, length=30),
        nullable=False,
        default=MeasureCategory.ORGANIZATIONAL,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "name": self.name,
            "category": self.category.value if self.category else None,
            "description": self.description,
            "is_global": self.tenant_id is None,
        }


class MeasureImplementation(Base):
    __tablename__ = "privacypa_measure_implementations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    measure_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_security_measures.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    implementation_status: Mapped[ImplementationStatus] = mapped_column(
        SAEnum(ImplementationStatus, native_enum=False, length=20),
        nullable=False,
        default=ImplementationStatus.PLANNED,
    )
    implementation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_verified_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_review_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "measure_id": self.measure_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "implementation_status": self.implementation_status.value if self.implementation_status else None,
            "implementation_notes": self.implementation_notes,
            "owner_user_id": self.owner_user_id,
            "last_verified_at": self.last_verified_at.isoformat() if self.last_verified_at else None,
            "next_review_at": self.next_review_at.isoformat() if self.next_review_at else None,
            "evidence_document_id": self.evidence_document_id,
        }
