"""PrivacyPA — Sistemi e asset informativi (§17)."""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base
from backend.models.privacypa_processing import ConfidentialityLevel


def _uuid() -> str:
    return str(uuid.uuid4())


class AssetType(str, enum.Enum):
    APPLICATION = "APPLICATION"
    DATABASE = "DATABASE"
    FILE_SHARE = "FILE_SHARE"
    PAPER_ARCHIVE = "PAPER_ARCHIVE"
    SERVER = "SERVER"
    CLOUD_SERVICE = "CLOUD_SERVICE"
    DEVICE_CLASS = "DEVICE_CLASS"
    INTEGRATION = "INTEGRATION"
    OTHER = "OTHER"


class HostingType(str, enum.Enum):
    ON_PREM = "ON_PREM"
    CLOUD = "CLOUD"
    HYBRID = "HYBRID"
    SAAS = "SAAS"
    OTHER = "OTHER"


class AssetLifecycle(str, enum.Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    LEGACY = "LEGACY"
    DISMISSION = "DISMISSION"
    DISMISSED = "DISMISSED"


class AssetRole(str, enum.Enum):
    COLLECTION = "COLLECTION"
    STORAGE = "STORAGE"
    PROCESSING = "PROCESSING"
    EXCHANGE = "EXCHANGE"
    ARCHIVE = "ARCHIVE"
    PUBLICATION = "PUBLICATION"


class Asset(Base):
    __tablename__ = "privacypa_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(
        SAEnum(AssetType, native_enum=False, length=30), nullable=False, default=AssetType.APPLICATION
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_parties.id"), nullable=True
    )
    owner_unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=True
    )
    hosting_type: Mapped[HostingType] = mapped_column(
        SAEnum(HostingType, native_enum=False, length=20), nullable=False, default=HostingType.ON_PREM
    )
    cloud_service: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hosting_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    data_location_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    authentication_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mfa_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    logging_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    encryption_at_rest: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    encryption_in_transit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    backup_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lifecycle_status: Mapped[AssetLifecycle] = mapped_column(
        SAEnum(AssetLifecycle, native_enum=False, length=20),
        nullable=False,
        default=AssetLifecycle.ACTIVE,
    )
    go_live_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    retirement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidentiality_level: Mapped[ConfidentialityLevel] = mapped_column(
        SAEnum(ConfidentialityLevel, native_enum=False, length=20),
        nullable=False,
        default=ConfidentialityLevel.INTERNAL,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_asset_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "name": self.name,
            "asset_type": self.asset_type.value if self.asset_type else None,
            "description": self.description,
            "supplier_id": self.supplier_id,
            "owner_unit_id": self.owner_unit_id,
            "hosting_type": self.hosting_type.value if self.hosting_type else None,
            "cloud_service": self.cloud_service,
            "hosting_country": self.hosting_country,
            "data_location_notes": self.data_location_notes,
            "authentication_type": self.authentication_type,
            "mfa_supported": self.mfa_supported,
            "logging_supported": self.logging_supported,
            "encryption_at_rest": self.encryption_at_rest,
            "encryption_in_transit": self.encryption_in_transit,
            "backup_enabled": self.backup_enabled,
            "lifecycle_status": self.lifecycle_status.value if self.lifecycle_status else None,
            "go_live_date": self.go_live_date.isoformat() if self.go_live_date else None,
            "retirement_date": self.retirement_date.isoformat() if self.retirement_date else None,
            "confidentiality_level": self.confidentiality_level.value if self.confidentiality_level else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProcessingAsset(Base):
    __tablename__ = "privacypa_processing_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_assets.id"), nullable=False)
    role: Mapped[AssetRole] = mapped_column(
        SAEnum(AssetRole, native_enum=False, length=20), nullable=False, default=AssetRole.PROCESSING
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("processing_activity_id", "asset_id", "role", name="uix_privacypa_processing_asset_role"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "asset_id": self.asset_id,
            "role": self.role.value if self.role else None,
            "notes": self.notes,
        }
