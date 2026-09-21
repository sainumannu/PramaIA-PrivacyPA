"""
PrivacyPA — Chiavi di idempotenza (§131): risposta memorizzata per una `Idempotency-Key`, per rendere sicuri i retry.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


class IdempotencyRecord(Base):
    __tablename__ = "privacypa_idempotency_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    # Impronta di metodo, percorso, query e corpo: la stessa chiave con una richiesta diversa è un errore.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL finché la richiesta originale è in corso.
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (UniqueConstraint("tenant_id", "subject", "key", name="uix_privacypa_idempotency"),)
