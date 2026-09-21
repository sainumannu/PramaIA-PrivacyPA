"""
PrivacyPA — Job di import (§67, §68): ogni importazione è un record tracciato, con esito del
dry-run, stato e riferimento al file caricato (rimosso a import concluso o scartato).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ImportStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    DRY_RUN = "DRY_RUN"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


class ImportJob(Base):
    __tablename__ = "privacypa_import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity: Mapped[str] = mapped_column(String(40), nullable=False, default="processing_activities")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ImportStatus] = mapped_column(
        SAEnum(ImportStatus, native_enum=False, length=20), nullable=False, default=ImportStatus.UPLOADED
    )
    sheet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "entity": self.entity,
            "filename": self.filename,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "status": self.status.value if self.status else None,
            "sheet": self.sheet,
            "mapping": self.mapping,
            "options": self.options,
            "report": self.report,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "committed_at": self.committed_at.isoformat() if self.committed_at else None,
        }
