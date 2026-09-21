"""
PrivacyPA — Formazione (§44), autorizzati al trattamento (§45), pareri del DPO (§43) e relazione annuale (§42).

Le persone sono identificate dall'identificativo opaco dell'utente (`sub` del Portal), come nel resto
dell'applicazione: nessun dato anagrafico viene copiato qui.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _enum(cls, length=20):
    return SAEnum(cls, native_enum=False, length=length)


def _iso(value):
    return value.isoformat() if value else None


# ---------------------------------------------------------------- formazione (§44)

class AssignmentStatus(str, enum.Enum):
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    WAIVED = "WAIVED"      # esonero motivato


class TrainingCourse(Base):
    __tablename__ = "privacypa_training_courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Destinatari: ruoli privacy e/o strutture; vuoti = nessuna assegnazione automatica.
    target_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    target_unit_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Mesi di validità dopo il completamento (None = senza scadenza): determina la prossima scadenza.
    validity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_course_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "code": self.code,
            "title": self.title, "version": self.version, "description": self.description, "mandatory": self.mandatory,
            "target_roles": self.target_roles or [], "target_unit_ids": self.target_unit_ids or [],
            "validity_months": self.validity_months, "active": self.active, "created_by": self.created_by,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class TrainingSession(Base):
    __tablename__ = "privacypa_training_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_training_courses.id"), nullable=False, index=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    trainer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "course_id": self.course_id, "session_date": _iso(self.session_date), "trainer": self.trainer,
            "location": self.location, "notes": self.notes, "created_by": self.created_by, "created_at": _iso(self.created_at),
        }


class TrainingAssignment(Base):
    __tablename__ = "privacypa_training_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_training_courses.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(_enum(AssignmentStatus), nullable=False, default=AssignmentStatus.ASSIGNED)
    waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "course_id": self.course_id,
            "user_id": self.user_id, "due_date": _iso(self.due_date), "status": self.status.value,
            "waiver_reason": self.waiver_reason, "assigned_by": self.assigned_by, "assigned_at": _iso(self.assigned_at),
        }


class TrainingCompletion(Base):
    __tablename__ = "privacypa_training_completions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    assignment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_training_assignments.id"), nullable=True)
    course_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_training_courses.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    completed_at: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    certificate_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "assignment_id": self.assignment_id, "course_id": self.course_id, "user_id": self.user_id,
            "session_id": self.session_id, "completed_at": _iso(self.completed_at), "score": self.score,
            "certificate_document_id": self.certificate_document_id, "next_due_at": _iso(self.next_due_at),
            "recorded_by": self.recorded_by, "recorded_at": _iso(self.recorded_at),
        }


# ---------------------------------------------------------------- autorizzati (§45)

class AuthorizationProfile(Base):
    __tablename__ = "privacypa_authorization_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uix_privacypa_auth_profile_name"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "name": self.name,
            "description": self.description, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class Authorization(Base):
    __tablename__ = "privacypa_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processing_activity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=True, index=True
    )
    asset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("privacypa_assets.id"), nullable=True, index=True)
    authorization_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_authorization_profiles.id"), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Verifica confermata dopo un cambio di struttura del trattamento (§45).
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def is_active(self, today: date) -> bool:
        return self.revoked_at is None and self.valid_from <= today and (self.valid_to is None or self.valid_to >= today)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "user_id": self.user_id,
            "processing_activity_id": self.processing_activity_id, "asset_id": self.asset_id,
            "authorization_profile_id": self.authorization_profile_id, "valid_from": _iso(self.valid_from),
            "valid_to": _iso(self.valid_to), "granted_by": self.granted_by, "granted_at": _iso(self.granted_at),
            "revoked_at": _iso(self.revoked_at), "revoked_by": self.revoked_by, "revoked_reason": self.revoked_reason,
            "last_reviewed_at": _iso(self.last_reviewed_at), "last_reviewed_by": self.last_reviewed_by,
            "active": self.is_active(date.today()),
        }


# ---------------------------------------------------------------- pareri del DPO (§43)

class OpinionStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    ISSUED = "ISSUED"
    WITHDRAWN = "WITHDRAWN"


OPINION_ENTITY_TYPES = ("processing_activity", "dpia", "privacy_notice", "data_breach")


class DpoOpinion(Base):
    __tablename__ = "privacypa_dpo_opinions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    entity_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    request_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Il parere è distinto dalla decisione del titolare (§43): resta un record a sé, immutabile una volta emesso.
    opinion_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dpo_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[OpinionStatus] = mapped_column(_enum(OpinionStatus), nullable=False, default=OpinionStatus.REQUESTED)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "tenant_id": self.tenant_id, "entity_type": self.entity_type,
            "entity_id": self.entity_id, "entity_code": self.entity_code, "entity_title": self.entity_title,
            "organizational_unit_id": self.organizational_unit_id, "requested_at": _iso(self.requested_at),
            "requested_by": self.requested_by, "request_notes": self.request_notes, "opinion_text": self.opinion_text,
            "issued_at": _iso(self.issued_at), "dpo_user_id": self.dpo_user_id, "status": self.status.value,
        }


# ---------------------------------------------------------------- relazione annuale (§42)

class AnnualReportStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    FINAL = "FINAL"


class AnnualReport(Base):
    """Commenti e conclusioni del soggetto competente; con FINAL congela anche i dati calcolati."""

    __tablename__ = "privacypa_annual_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    comments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[AnnualReportStatus] = mapped_column(_enum(AnnualReportStatus), nullable=False, default=AnnualReportStatus.DRAFT)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finalized_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "year", name="uix_privacypa_annual_report_year"),)
