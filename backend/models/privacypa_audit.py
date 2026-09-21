"""
PrivacyPA — Audit trail di dominio (§33 della specifica funzionale).

Distinto da AppAuditLog (backend/models/permissions_models.py), che
traccia solo le modifiche ai permessi app-admin. Questo audit_log
traccia le azioni di business sulle entità del dominio PrivacyPA
(organizzazione, ruoli, e in futuro registro, DPIA, ecc.).

Append-only: nessun metodo di update/delete è esposto dal service.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ActorType(str, enum.Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    SERVICE = "SERVICE"


class AuditLog(Base):
    """Audit trail append-only per le entità di dominio PrivacyPA (§33.1)."""

    __tablename__ = "privacypa_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    actor_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(
        SAEnum(ActorType, native_enum=False, length=10), nullable=False, default=ActorType.USER
    )
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)

    old_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Colonna DB "metadata": nome Python diverso per non collidere con
    # l'attributo riservato Base.metadata di SQLAlchemy.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "actor_user_id": self.actor_user_id,
            "actor_type": self.actor_type.value if self.actor_type else None,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "event_type": self.event_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "metadata": self.event_metadata,
        }
