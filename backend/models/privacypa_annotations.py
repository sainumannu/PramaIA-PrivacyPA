"""
PrivacyPA — Annotazioni trasversali: tag (§78), campi custom (§77), note e commenti (§79).

Tutte si agganciano a un elemento con la coppia `entity_type` + `entity_id` (senza chiave esterna: valgono per tipi
diversi). L'esistenza e la visibilità dell'elemento si verificano nel servizio, non nel database. I campi custom non
sostituiscono il modello core: sono pochi, tipizzati e solo su alcuni tipi di elemento.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _iso(value):
    return value.isoformat() if value else None


class Tag(Base):
    __tablename__ = "privacypa_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # Confronto senza maiuscole/minuscole: "PNRR" e "pnrr" sono lo stesso tag.
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "name_key", name="uix_privacypa_tag_name"),)

    def to_dict(self, usage: int | None = None) -> dict:
        data = {
            "id": self.id, "row_version": self.row_version, "name": self.name, "color": self.color, "description": self.description,
            "active": self.active, "created_by": self.created_by, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }
        if usage is not None:
            data["usage"] = usage
        return data


class EntityTag(Base):
    __tablename__ = "privacypa_entity_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tag_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_tags.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tag_id", "entity_type", "entity_id", name="uix_privacypa_entity_tag"),)


class CustomFieldType(str, enum.Enum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    SELECT = "SELECT"


class CustomFieldDefinition(Base):
    __tablename__ = "privacypa_custom_field_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    field_type: Mapped[CustomFieldType] = mapped_column(SAEnum(CustomFieldType, native_enum=False, length=20), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "entity_type", "key", name="uix_privacypa_custom_field_key"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "entity_type": self.entity_type, "key": self.key, "label": self.label,
            "field_type": self.field_type.value, "required": self.required, "options": self.options or [], "active": self.active,
            "position": self.position, "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class CustomFieldValue(Base):
    __tablename__ = "privacypa_custom_field_values"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    definition_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_custom_field_definitions.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("definition_id", "entity_id", name="uix_privacypa_custom_field_value"),)


class CommentVisibility(str, enum.Enum):
    TEAM = "TEAM"                        # chi lavora sull'elemento: ha il permesso di modificarlo sulla sua struttura
    DPO_ONLY = "DPO_ONLY"                # l'autore e i DPO assegnati alla struttura dell'elemento
    PRIVATE = "PRIVATE"                  # solo l'autore
    ALL_AUTHORIZED = "ALL_AUTHORIZED"    # chiunque possa vedere l'elemento


class Comment(Base):
    __tablename__ = "privacypa_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    author_id: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[CommentVisibility] = mapped_column(
        SAEnum(CommentVisibility, native_enum=False, length=20), nullable=False, default=CommentVisibility.TEAM
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "row_version": self.row_version, "entity_type": self.entity_type, "entity_id": self.entity_id,
            "author_id": self.author_id, "body": self.body, "visibility": self.visibility.value,
            "created_at": _iso(self.created_at), "edited_at": _iso(self.edited_at),
        }
