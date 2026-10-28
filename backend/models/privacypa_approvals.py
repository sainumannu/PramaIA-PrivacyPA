"""
PrivacyPA — Approvazioni (§165) e policy di ente (§10.1, §166).

`Approval` registra ogni richiesta di approvazione con la versione del contenuto su cui è stata
chiesta e, quando arriva, la decisione. `TenantPolicy` contiene la configurazione per ente: matrice
dei permessi (override dei default) e regole di segregazione dei compiti.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ApprovalDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ApprovalState(str, enum.Enum):
    """Stato derivato (non memorizzato): in attesa, decisa, ritirata dal richiedente."""

    PENDING = "PENDING"
    DECIDED = "DECIDED"
    CANCELLED = "CANCELLED"


class Approval(Base):
    __tablename__ = "privacypa_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # row_version del record al momento della richiesta: se cambia, la decisione non è più applicabile.
    entity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Struttura del record (per il filtro di visibilità della coda approvazioni).
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    # Persona per conto della quale un account di servizio ha inviato la richiesta (es. operatore dell'HelpDesk).
    requested_on_behalf_of: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    request_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    approver_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision: Mapped[ApprovalDecision | None] = mapped_column(
        SAEnum(ApprovalDecision, native_enum=False, length=20), nullable=True
    )
    decision_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("ix_privacypa_approvals_entity", "tenant_id", "entity_type", "entity_id"),)

    @property
    def state(self) -> ApprovalState:
        if self.decision is not None:
            return ApprovalState.DECIDED
        if self.cancelled_at is not None:
            return ApprovalState.CANCELLED
        return ApprovalState.PENDING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_version": self.entity_version,
            "entity_code": self.entity_code,
            "entity_title": self.entity_title,
            "organizational_unit_id": self.organizational_unit_id,
            "state": self.state.value,
            "requested_by": self.requested_by,
            "requested_on_behalf_of": self.requested_on_behalf_of,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "request_notes": self.request_notes,
            "approver_id": self.approver_id,
            "decision": self.decision.value if self.decision else None,
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "notes": self.notes,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancelled_by": self.cancelled_by,
        }


class TenantPolicy(Base):
    """Configurazione per ente: una riga per tenant, creata al primo salvataggio."""

    __tablename__ = "privacypa_tenant_policies"

    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    # {"processing.approve": ["DPO", ...]}: sostituisce i ruoli di default del permesso.
    permission_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"approver_not_requester": bool, "approver_not_editor": bool}
    segregation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"dsr_base_months": 1, "dsr_extension_months": 2}: termini configurabili per ente (§156, §158).
    deadlines: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Regole per livello di riservatezza e step-up (§134-§136): vedi backend/auth/privacypa_confidentiality.py
    confidentiality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Parametri dei connettori verso sistemi esterni: {"retention_category": "IMMAGINI"} (vedi privacypa_integration_service.py).
    integrations: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
