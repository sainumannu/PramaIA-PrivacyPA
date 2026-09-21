"""
PrivacyPA — Webhook (§70): sottoscrizioni degli endpoint esterni e consegne.

Una consegna nasce nella stessa transazione dell'evento di dominio (vedi `privacypa_webhook_service.fan_out_event`), quindi
non si perde tra commit e invio. Il segreto di firma è cifrato a riposo e mostrato una sola volta, alla creazione.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _iso(value):
    return value.isoformat() if value else None


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"        # da inviare (o da ritentare)
    DELIVERED = "DELIVERED"    # risposta 2xx
    DEAD = "DEAD"              # tentativi esauriti o endpoint non più valido


class WebhookSubscription(Base):
    __tablename__ = "privacypa_webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    disabled_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "name": self.name, "url": self.url, "events": list(self.events or []),
            "active": self.active, "disabled_reason": self.disabled_reason, "failure_streak": self.failure_streak,
            "last_success_at": _iso(self.last_success_at), "last_failure_at": _iso(self.last_failure_at),
            "secret_rotated_at": _iso(self.secret_rotated_at), "created_by": self.created_by,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class WebhookDelivery(Base):
    __tablename__ = "privacypa_webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_webhook_subscriptions.id"), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(60), nullable=False)          # id dell'evento sull'outbox (o `ping-…`)
    event_name: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(SAEnum(DeliveryStatus, native_enum=False, length=20), nullable=False, default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("subscription_id", "event_id", "event_name", name="uix_privacypa_webhook_delivery"),
        Index("ix_privacypa_webhook_due", "status", "next_attempt_at"),
    )

    def to_dict(self, with_payload: bool = False) -> dict:
        data = {
            "id": self.id, "subscription_id": self.subscription_id, "event_id": self.event_id, "event_name": self.event_name,
            "status": self.status.value, "attempts": self.attempts, "next_attempt_at": _iso(self.next_attempt_at),
            "last_status_code": self.last_status_code, "last_error": self.last_error, "created_at": _iso(self.created_at),
            "last_attempt_at": _iso(self.last_attempt_at), "delivered_at": _iso(self.delivered_at),
        }
        if with_payload:
            data["payload"] = self.payload
        return data
