"""PrivacyPA — Task e scadenze (§35). OVERDUE è calcolato, non persistito."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class TaskSourceType(str, enum.Enum):
    PROCESSING = "PROCESSING"
    ASSET = "ASSET"
    PARTY = "PARTY"
    DOCUMENT = "DOCUMENT"
    REQUEST = "REQUEST"
    PROJECT = "PROJECT"
    MANUAL = "MANUAL"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PrivacyTask(Base):
    __tablename__ = "privacypa_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[TaskSourceType] = mapped_column(
        SAEnum(TaskSourceType, native_enum=False, length=20),
        nullable=False,
        default=TaskSourceType.MANUAL,
    )
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    assigned_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority, native_enum=False, length=20),
        nullable=False,
        default=TaskPriority.MEDIUM,
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False, length=20),
        nullable=False,
        default=TaskStatus.OPEN,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_task_code"),)

    def computed_status(self) -> str:
        if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return self.status.value
        if self.due_at and self.due_at < datetime.utcnow():
            return "OVERDUE"
        return self.status.value

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "source_type": self.source_type.value if self.source_type else None,
            "source_id": self.source_id,
            "assigned_user_id": self.assigned_user_id,
            "assigned_unit_id": self.assigned_unit_id,
            "priority": self.priority.value if self.priority else None,
            "status": self.status.value if self.status else None,
            "computed_status": self.computed_status(),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
