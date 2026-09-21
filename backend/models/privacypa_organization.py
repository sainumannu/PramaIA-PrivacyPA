"""
PrivacyPA — Organizzazione (§8 della specifica funzionale).

Entità fondamentali: organizations (l'ente) e organizational_units
(struttura gerarchica arbitraria: aree, settori, servizi, uffici...).
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import String, Boolean, DateTime, Date, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class OrganizationalUnitType(str, enum.Enum):
    AREA = "AREA"
    SETTORE = "SETTORE"
    SERVIZIO = "SERVIZIO"
    UFFICIO = "UFFICIO"
    UNITA = "UNITA"
    COMANDO = "COMANDO"
    ISTITUZIONE = "ISTITUZIONE"


class Organization(Base):
    """L'ente pubblico (§8.1 organizations)."""

    __tablename__ = "privacypa_organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True, unique=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ipa_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dpo_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dpo_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    units: Mapped[list["OrganizationalUnit"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "tax_code": self.tax_code,
            "ipa_code": self.ipa_code,
            "pec": self.pec,
            "address": self.address,
            "dpo_name": self.dpo_name,
            "dpo_contact": self.dpo_contact,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OrganizationalUnit(Base):
    """Struttura organizzativa gerarchica (§8.1 organizational_units)."""

    __tablename__ = "privacypa_organizational_units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_organizations.id"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=True, index=True
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_type: Mapped[OrganizationalUnitType] = mapped_column(
        SAEnum(OrganizationalUnitType, native_enum=False, length=20), nullable=False
    )
    manager_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, default=date.today)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="units")
    parent: Mapped["OrganizationalUnit | None"] = relationship(remote_side=[id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "parent_id": self.parent_id,
            "code": self.code,
            "name": self.name,
            "unit_type": self.unit_type.value if self.unit_type else None,
            "manager_user_id": self.manager_user_id,
            "active": self.active,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }
