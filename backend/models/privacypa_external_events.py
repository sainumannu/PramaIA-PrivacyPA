"""
PrivacyPA — Eventi tecnici ricevuti da sistemi esterni (connettore VideoPA, W3).

Si conservano solo i campi ammessi dal contratto: tipo, riferimento della camera, riassunto breve e link. Niente coordinate, indirizzi IP,
credenziali o evidenze. L'evento non cambia nulla nel trattamento: alimenta una segnalazione per il DPO (regola
`EXTERNAL_CHANGE_REVIEW_PENDING`) che si chiude quando il trattamento viene rivisto o l'evento archiviato con una nota.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _iso(value):
    return value.isoformat() if value else None


class ExternalEventStatus(str, enum.Enum):
    PENDING = "PENDING"        # in attesa di revisione
    UNMATCHED = "UNMATCHED"    # il trattamento non esiste nell'ente: lo smista un amministratore
    ARCHIVED = "ARCHIVED"      # archiviato con nota («nessun impatto»)


class ExternalEvent(Base):
    __tablename__ = "privacypa_external_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    processing_activity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Riferimento indicato dal mittente, per lo smistamento di ciò che non trova il trattamento.
    claimed_processing_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ExternalEventStatus] = mapped_column(
        SAEnum(ExternalEventStatus, native_enum=False, length=20), nullable=False, default=ExternalEventStatus.PENDING
    )
    received_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    archived_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archive_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "event_id", name="uix_privacypa_external_event"),
        Index("ix_privacypa_external_event_pending", "tenant_id", "status", "processing_activity_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "source": self.source, "event_id": self.event_id,
            "type": self.event_type, "occurred_at": _iso(self.occurred_at), "processing_activity_id": self.processing_activity_id,
            "claimed_processing_id": self.claimed_processing_id, "subject_ref": self.subject_ref, "summary": self.summary,
            "link": self.link, "status": self.status.value, "received_at": _iso(self.received_at),
            "archived_by": self.archived_by, "archived_at": _iso(self.archived_at), "archive_note": self.archive_note,
        }
