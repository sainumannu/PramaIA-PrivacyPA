"""
PrivacyPA — Informative (§25) e flussi dei dati (§20).

Le informative sono versionate (§32): una versione approvata è immutabile e ogni modifica di contenuto crea una
nuova versione. Il collegamento con i trattamenti permette di segnalare quando un trattamento cambia dopo
l'approvazione dell'informativa (NOTICE_REVIEW_REQUIRED, §25.1).
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class NoticeStatus(str, enum.Enum):
    DRAFT = "DRAFT"          # versione in lavorazione
    IN_REVIEW = "IN_REVIEW"  # inviata in approvazione
    APPROVED = "APPROVED"    # ultima versione approvata, in vigore
    RETIRED = "RETIRED"      # non più in uso


NOTICE_TRANSITIONS: dict[NoticeStatus, set[NoticeStatus]] = {
    NoticeStatus.DRAFT: {NoticeStatus.IN_REVIEW},
    NoticeStatus.IN_REVIEW: {NoticeStatus.DRAFT, NoticeStatus.APPROVED},
    NoticeStatus.APPROVED: {NoticeStatus.RETIRED},
    NoticeStatus.RETIRED: set(),
}


class PrivacyNotice(Base):
    __tablename__ = "privacypa_privacy_notices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    audience: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[NoticeStatus] = mapped_column(
        SAEnum(NoticeStatus, native_enum=False, length=20), nullable=False, default=NoticeStatus.DRAFT
    )
    owner_unit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=False, index=True
    )
    # Versione approvata in vigore (può differire da quella in lavorazione).
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Verifica di coerenza confermata senza nuova versione (chiude NOTICE_REVIEW_REQUIRED).
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    versions: Mapped[list["PrivacyNoticeVersion"]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", order_by="PrivacyNoticeVersion.version_no"
    )
    links: Mapped[list["NoticeProcessingLink"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_notice_code"),)

    @property
    def working_version(self) -> "PrivacyNoticeVersion | None":
        return next((v for v in self.versions if v.approved_at is None), None)

    @property
    def current_version(self) -> "PrivacyNoticeVersion | None":
        return next((v for v in self.versions if v.id == self.current_version_id), None)

    @property
    def reference_date(self) -> datetime | None:
        """Da quando il contenuto è considerato coerente: ultima approvazione o ultima verifica confermata."""
        current = self.current_version
        dates = [d for d in (current.approved_at if current else None, self.last_reviewed_at) if d]
        return max(dates) if dates else None

    def to_dict(self, include_versions: bool = False) -> dict:
        current, working = self.current_version, self.working_version
        data = {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "audience": self.audience,
            "status": self.status.value,
            "owner_unit_id": self.owner_unit_id,
            "current_version_id": self.current_version_id,
            "current_version": current.to_dict(include_content=False) if current else None,
            "working_version_id": working.id if working else None,
            "processing_activity_ids": sorted(l.processing_activity_id for l in self.links),
            "last_reviewed_at": self.last_reviewed_at.isoformat() if self.last_reviewed_at else None,
            "last_reviewed_by": self.last_reviewed_by,
            "review_notes": self.review_notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_versions:
            data["versions"] = [v.to_dict(include_content=True) for v in self.versions]
        return data


class PrivacyNoticeVersion(Base):
    __tablename__ = "privacypa_privacy_notice_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    privacy_notice_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_privacy_notices.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    notice: Mapped["PrivacyNotice"] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("privacy_notice_id", "version_no", name="uix_privacypa_notice_version_no"),)

    def to_dict(self, include_content: bool = True) -> dict:
        data = {
            "id": self.id,
            "privacy_notice_id": self.privacy_notice_id,
            "version_no": self.version_no,
            "version_label": self.version_label,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "published_url": self.published_url,
            "document_id": self.document_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "is_approved": self.approved_at is not None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_content:
            data["content"] = self.content
        return data


class NoticeProcessingLink(Base):
    __tablename__ = "privacypa_notice_processing_links"

    privacy_notice_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_privacy_notices.id"), primary_key=True
    )
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), primary_key=True
    )


# ---------------------------------------------------------------- flussi dei dati (§20)

class FlowEndpointType(str, enum.Enum):
    DATA_SUBJECT = "DATA_SUBJECT"                  # interessati (etichetta libera)
    ASSET = "ASSET"                                # sistema
    PARTY = "PARTY"                                # fornitore, responsabile o destinatario
    ORGANIZATIONAL_UNIT = "ORGANIZATIONAL_UNIT"    # struttura dell'ente
    EXTERNAL = "EXTERNAL"                          # soggetto esterno non censito (etichetta libera)


# Tipi di estremo che devono puntare a un'anagrafica dell'ente.
REFERENCED_ENDPOINTS = {FlowEndpointType.ASSET, FlowEndpointType.PARTY, FlowEndpointType.ORGANIZATIONAL_UNIT}


class FlowFrequency(str, enum.Enum):
    REALTIME = "REALTIME"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"
    ON_DEMAND = "ON_DEMAND"
    ONE_OFF = "ONE_OFF"


class FlowChannel(str, enum.Enum):
    API = "API"
    FILE_TRANSFER = "FILE_TRANSFER"
    EMAIL = "EMAIL"
    PEC = "PEC"
    WEB_FORM = "WEB_FORM"
    PORTAL = "PORTAL"
    DATABASE = "DATABASE"
    PAPER = "PAPER"
    MANUAL = "MANUAL"
    OTHER = "OTHER"


class DataFlow(Base):
    __tablename__ = "privacypa_data_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    source_type: Mapped[FlowEndpointType] = mapped_column(SAEnum(FlowEndpointType, native_enum=False, length=30), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[FlowEndpointType] = mapped_column(SAEnum(FlowEndpointType, native_enum=False, length=30), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    frequency: Mapped[FlowFrequency] = mapped_column(SAEnum(FlowFrequency, native_enum=False, length=20), nullable=False)
    channel: Mapped[FlowChannel] = mapped_column(SAEnum(FlowChannel, native_enum=False, length=20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    security_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    categories: Mapped[list["DataFlowCategory"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "processing_activity_id": self.processing_activity_id,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "target_label": self.target_label,
            "frequency": self.frequency.value,
            "channel": self.channel.value,
            "description": self.description,
            "security_notes": self.security_notes,
            "data_category_ids": sorted(c.data_category_id for c in self.categories),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DataFlowCategory(Base):
    __tablename__ = "privacypa_data_flow_categories"

    data_flow_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_data_flows.id"), primary_key=True)
    data_category_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_data_category_catalog.id"), primary_key=True
    )
