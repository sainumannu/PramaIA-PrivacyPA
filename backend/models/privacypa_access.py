"""
PrivacyPA — Valutazioni delle richieste di accesso (integrazione con l'HelpDesk).

Una valutazione è immutabile e legata a una versione della richiesta (`source_ref` + `request_version`): una nuova
versione ne crea un'altra. La persona interessata è conservata solo per la segregazione dei compiti e non esce mai
nelle risposte all'HelpDesk, nell'audit o nell'outbox.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base

STANDARD, REVIEW_REQUIRED, UNKNOWN = "STANDARD", "REVIEW_REQUIRED", "UNKNOWN"
OUTCOMES = (STANDARD, REVIEW_REQUIRED, UNKNOWN)
REQUEST_TYPES = ("GRANT", "CHANGE", "REVOKE")


def _uuid() -> str:
    return str(uuid.uuid4())


class AccessAssessment(Base):
    __tablename__ = "privacypa_access_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_module: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Indirizzo del ticket nel modulo di origine (solo http/https): collegamento nella coda approvazioni.
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_type: Mapped[str] = mapped_column(String(10), nullable=False)
    subject_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Riferimenti come ricevuti: un sistema non registrato resta qui per spiegare l'esito UNKNOWN.
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_authorization_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Operazioni, ambito e durata richiesti (senza dati della persona).
    request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    checks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Struttura responsabile del sistema: visibilità della coda approvazioni.
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_on_behalf_of: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "source_module", "source_ref", "request_version", name="uix_privacypa_access_assessment_source"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source": {"module": self.source_module, "ref": self.source_ref, "request_version": self.request_version,
                                      "url": self.source_url},
            "request_type": self.request_type, "asset_id": self.asset_id, "profile_id": self.profile_id,
            "profile_version": self.profile_version, "current_authorization_id": self.current_authorization_id,
            "request": self.request or {}, "outcome": self.outcome, "checks": self.checks or [],
            "organizational_unit_id": self.organizational_unit_id,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }
