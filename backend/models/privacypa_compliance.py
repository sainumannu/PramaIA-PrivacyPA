"""
PrivacyPA — Riferimenti normativi (§13), azioni correttive (§29), data breach (§26), richieste degli
interessati (§27) e audit strutturati (§28).

Le entità con dati sensibili (violazioni, richieste) hanno sempre una struttura di riferimento, che ne
determina la visibilità per scope. I riferimenti a persone (richiedente) sono opachi e non finiscono in audit
né nell'outbox.
"""
import enum
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base
from backend.models.privacypa_processing import ConfidentialityLevel


def _uuid() -> str:
    return str(uuid.uuid4())


def _enum(cls, length=30):
    return SAEnum(cls, native_enum=False, length=length)


def _iso(value):
    return value.isoformat() if value else None


# ---------------------------------------------------------------- riferimenti normativi (§13)

class LegalSourceType(str, enum.Enum):
    LAW = "LAW"
    LEGISLATIVE_DECREE = "LEGISLATIVE_DECREE"
    REGULATION = "REGULATION"
    DIRECTIVE = "DIRECTIVE"
    GUIDELINE = "GUIDELINE"
    MUNICIPAL_ACT = "MUNICIPAL_ACT"
    OTHER = "OTHER"


class LegalReference(Base):
    __tablename__ = "privacypa_legal_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_type: Mapped[LegalSourceType] = mapped_column(_enum(LegalSourceType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    article: Mapped[str | None] = mapped_column(String(100), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def citation(self) -> str:
        """Citazione leggibile, es. "D.Lgs. 196/2003, art. 2-ter" (titolo, numero/anno, articolo)."""
        ref = self.title
        if self.number or self.year:
            ref += f" {self.number or ''}{'/' if self.number and self.year else ''}{self.year or ''}".rstrip()
        if self.article:
            ref += f", {self.article}"
        return ref

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id,
            "source_type": self.source_type.value, "title": self.title, "number": self.number, "year": self.year,
            "article": self.article, "url": self.url, "notes": self.notes, "citation": self.citation,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class ProcessingLegalReference(Base):
    __tablename__ = "privacypa_processing_legal_references"

    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), primary_key=True
    )
    legal_reference_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_legal_references.id"), primary_key=True
    )

    def to_dict(self) -> dict:
        return {"processing_activity_id": self.processing_activity_id, "legal_reference_id": self.legal_reference_id}


# ---------------------------------------------------------------- azioni correttive (§29)

class CorrectiveSource(str, enum.Enum):
    AUDIT = "AUDIT"
    DPIA = "DPIA"
    BREACH = "BREACH"
    PROCESSING_REVIEW = "PROCESSING_REVIEW"
    PROCESSOR_ASSESSMENT = "PROCESSOR_ASSESSMENT"
    SECURITY_ASSESSMENT = "SECURITY_ASSESSMENT"
    DPO_RECOMMENDATION = "DPO_RECOMMENDATION"
    MANUAL = "MANUAL"


class ActionPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CorrectiveAction(Base):
    __tablename__ = "privacypa_corrective_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[CorrectiveSource] = mapped_column(_enum(CorrectiveSource), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[ActionPriority] = mapped_column(_enum(ActionPriority, 20), nullable=False, default=ActionPriority.MEDIUM)
    owner_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus, 20), nullable=False, default=ActionStatus.OPEN)
    closure_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidence_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_action_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "source_type": self.source_type.value, "source_id": self.source_id, "title": self.title,
            "description": self.description, "priority": self.priority.value, "owner_user_id": self.owner_user_id,
            "organizational_unit_id": self.organizational_unit_id, "due_date": _iso(self.due_date),
            "status": self.status.value, "closure_notes": self.closure_notes, "closed_at": _iso(self.closed_at),
            "evidence_document_id": self.evidence_document_id, "created_by": self.created_by,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


# ---------------------------------------------------------------- data breach (§26)

class BreachStatus(str, enum.Enum):
    OPEN = "OPEN"
    ASSESSING = "ASSESSING"
    CONTAINED = "CONTAINED"
    CLOSED = "CLOSED"


class BreachRisk(str, enum.Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


BREACH_LINK_TYPES = (
    "processing_activity", "asset", "party", "data_category", "data_subject_category", "security_measure",
    "document", "corrective_action",
)


class DataBreach(Base):
    __tablename__ = "privacypa_data_breaches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    organizational_unit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    aware_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Correzione motivata del momento di conoscenza (§157): l'originale resta visibile.
    aware_at_original: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    aware_at_correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    aware_at_corrected_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    aware_at_corrected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reported_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20), nullable=False, default=ConfidentialityLevel.INTERNAL,
        server_default="INTERNAL",
    )
    status: Mapped[BreachStatus] = mapped_column(_enum(BreachStatus, 20), nullable=False, default=BreachStatus.OPEN)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidentiality_affected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    integrity_affected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    availability_affected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approx_data_subjects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approx_records: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[BreachRisk | None] = mapped_column(_enum(BreachRisk, 20), nullable=True)
    risk_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_authority: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notify_authority_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notify_subjects: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notify_subjects_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    subjects_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    links: Mapped[list["DataBreachLink"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_breach_code"),)

    def to_dict(self, now: datetime | None = None) -> dict:
        now = now or datetime.utcnow()
        elapsed = (self.closed_at or now) - self.aware_at
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "title": self.title, "organizational_unit_id": self.organizational_unit_id,
            "detected_at": _iso(self.detected_at), "aware_at": _iso(self.aware_at),
            "aware_at_original": _iso(self.aware_at_original),
            "aware_at_correction_reason": self.aware_at_correction_reason,
            "aware_at_corrected_by": self.aware_at_corrected_by, "aware_at_corrected_at": _iso(self.aware_at_corrected_at),
            "reported_by": self.reported_by, "status": self.status.value, "description": self.description,
            "confidentiality_level": self.confidentiality_level.value,
            "confidentiality_affected": self.confidentiality_affected, "integrity_affected": self.integrity_affected,
            "availability_affected": self.availability_affected, "approx_data_subjects": self.approx_data_subjects,
            "approx_records": self.approx_records, "risk_level": self.risk_level.value if self.risk_level else None,
            "risk_rationale": self.risk_rationale, "notify_authority": self.notify_authority,
            "notify_authority_rationale": self.notify_authority_rationale,
            "authority_notified_at": _iso(self.authority_notified_at), "notify_subjects": self.notify_subjects,
            "notify_subjects_rationale": self.notify_subjects_rationale,
            "subjects_notified_at": _iso(self.subjects_notified_at), "closed_at": _iso(self.closed_at),
            # Tempo trascorso dalla conoscenza (§26.2, §157): informativo, non stabilisce l'obbligo di notifica.
            "hours_since_aware": round(elapsed.total_seconds() / 3600, 1),
            "reference_72h_at": _iso(self.aware_at.replace(microsecond=0) + timedelta(hours=72)),
            "links": [l.to_dict() for l in self.links],
            "created_by": self.created_by, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class DataBreachLink(Base):
    __tablename__ = "privacypa_data_breach_links"

    data_breach_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_data_breaches.id"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id}


# ---------------------------------------------------------------- richieste degli interessati (§27)

class RequestType(str, enum.Enum):
    ACCESS = "ACCESS"
    RECTIFICATION = "RECTIFICATION"
    ERASURE = "ERASURE"
    RESTRICTION = "RESTRICTION"
    OBJECTION = "OBJECTION"
    PORTABILITY = "PORTABILITY"
    OTHER = "OTHER"


class RequestChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    PEC = "PEC"
    POSTAL = "POSTAL"
    WEB_FORM = "WEB_FORM"
    PHONE = "PHONE"
    IN_PERSON = "IN_PERSON"
    OTHER = "OTHER"


class RequestStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    IN_PROGRESS = "IN_PROGRESS"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class DataSubjectRequest(Base):
    __tablename__ = "privacypa_data_subject_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    organizational_unit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    request_type: Mapped[RequestType] = mapped_column(_enum(RequestType, 20), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    channel: Mapped[RequestChannel] = mapped_column(_enum(RequestChannel, 20), nullable=False)
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    identity_verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RequestStatus] = mapped_column(_enum(RequestStatus, 20), nullable=False, default=RequestStatus.RECEIVED)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    extension_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extension_communicated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_request_code"),)

    def to_dict(self, personal: bool = True) -> dict:
        data = {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "organizational_unit_id": self.organizational_unit_id, "request_type": self.request_type.value,
            "received_at": _iso(self.received_at), "channel": self.channel.value,
            "identity_verified": self.identity_verified, "status": self.status.value, "due_at": _iso(self.due_at),
            "extension_applied": self.extension_applied, "extension_reason": self.extension_reason,
            "extension_communicated_at": _iso(self.extension_communicated_at),
            "response_sent_at": _iso(self.response_sent_at), "outcome": self.outcome,
            "created_by": self.created_by, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }
        if personal:  # riferimento del richiedente e note di verifica: solo nell'API, mai in audit o outbox
            data["requester_reference"] = self.requester_reference
            data["identity_verification_notes"] = self.identity_verification_notes
        return data


# ---------------------------------------------------------------- audit strutturati (§28)

class AuditType(str, enum.Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    PROCESSOR = "PROCESSOR"
    SECURITY = "SECURITY"
    DPO_REVIEW = "DPO_REVIEW"


class AuditStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AuditFindingStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACTION_PLANNED = "ACTION_PLANNED"
    CLOSED = "CLOSED"


class PrivacyAudit(Base):
    __tablename__ = "privacypa_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    audit_type: Mapped[AuditType] = mapped_column(_enum(AuditType, 20), nullable=False)
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    performed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lead_auditor_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AuditStatus] = mapped_column(_enum(AuditStatus, 20), nullable=False, default=AuditStatus.PLANNED)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    findings: Mapped[list["AuditFinding"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AuditFinding.created_at"
    )

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_audit_code"),)

    def to_dict(self, include_findings: bool = False) -> dict:
        data = {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "title": self.title, "audit_type": self.audit_type.value,
            "organizational_unit_id": self.organizational_unit_id, "planned_date": _iso(self.planned_date),
            "performed_date": _iso(self.performed_date), "lead_auditor_user_id": self.lead_auditor_user_id,
            "status": self.status.value, "summary": self.summary, "findings_count": len(self.findings),
            "open_findings": sum(1 for f in self.findings if f.status != AuditFindingStatus.CLOSED),
            "created_by": self.created_by, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }
        if include_findings:
            data["findings"] = [f.to_dict() for f in self.findings]
        return data


class AuditFinding(Base):
    __tablename__ = "privacypa_audit_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    audit_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_audits.id"), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AuditFindingStatus] = mapped_column(_enum(AuditFindingStatus, 20), nullable=False, default=AuditFindingStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "audit_id": self.audit_id, "severity": self.severity, "title": self.title,
            "description": self.description, "evidence": self.evidence, "status": self.status.value,
            "created_at": _iso(self.created_at),
        }
