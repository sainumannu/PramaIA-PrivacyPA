"""PrivacyPA — Fornitori, responsabili art. 28 e destinatari (§16, §18)."""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PartyType(str, enum.Enum):
    PROCESSOR = "PROCESSOR"
    JOINT_CONTROLLER = "JOINT_CONTROLLER"
    RECIPIENT = "RECIPIENT"
    SUPPLIER = "SUPPLIER"
    PUBLIC_BODY = "PUBLIC_BODY"
    OTHER = "OTHER"


class ProcessorStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TO_REVIEW = "TO_REVIEW"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class AgreementStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SIGNED = "SIGNED"
    TO_REVIEW = "TO_REVIEW"
    EXPIRED = "EXPIRED"


class ContractStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"


class SubprocessorAuthorization(str, enum.Enum):
    """Autorizzazione del titolare al ricorso a sub-responsabili (art. 28, par. 2 GDPR)."""

    SPECIFIC = "SPECIFIC"
    GENERAL = "GENERAL"


class SubprocessorStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TO_REVIEW = "TO_REVIEW"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


class CommunicationType(str, enum.Enum):
    COMMUNICATION = "COMMUNICATION"
    DISCLOSURE = "DISCLOSURE"
    PUBLICATION = "PUBLICATION"
    INTERNAL_ACCESS = "INTERNAL_ACCESS"


class Party(Base):
    __tablename__ = "privacypa_parties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    party_type: Mapped[PartyType] = mapped_column(
        SAEnum(PartyType, native_enum=False, length=30), nullable=False, default=PartyType.SUPPLIER
    )
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    processor_profile: Mapped["ProcessorProfile | None"] = relationship(
        back_populates="party", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_party_code"),)

    def to_dict(self, include_profile: bool = False) -> dict:
        data = {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "party_type": self.party_type.value if self.party_type else None,
            "legal_name": self.legal_name,
            "tax_id": self.tax_id,
            "vat_id": self.vat_id,
            "pec": self.pec,
            "email": self.email,
            "country_code": self.country_code,
            "address": self.address,
            "website": self.website,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_profile and self.processor_profile:
            data["processor_profile"] = self.processor_profile.to_dict()
        return data


class ProcessorProfile(Base):
    __tablename__ = "privacypa_processor_profiles"

    party_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_parties.id"), primary_key=True
    )
    dpo_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProcessorStatus] = mapped_column(
        SAEnum(ProcessorStatus, native_enum=False, length=20),
        nullable=False,
        default=ProcessorStatus.TO_REVIEW,
    )
    last_assessment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_assessment_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    party: Mapped["Party"] = relationship(back_populates="processor_profile")

    def to_dict(self) -> dict:
        return {
            "party_id": self.party_id,
            "dpo_contact": self.dpo_contact,
            "security_contact": self.security_contact,
            "privacy_contact": self.privacy_contact,
            "status": self.status.value if self.status else None,
            "last_assessment_date": self.last_assessment_date.isoformat() if self.last_assessment_date else None,
            "next_assessment_date": self.next_assessment_date.isoformat() if self.next_assessment_date else None,
        }


class ProcessorAgreement(Base):
    """Atto art. 28: checklist documentale, non dichiarazione di conformità (§18.4)."""

    __tablename__ = "privacypa_processor_agreements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    party_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=False, index=True)
    signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[AgreementStatus] = mapped_column(
        SAEnum(AgreementStatus, native_enum=False, length=20),
        nullable=False,
        default=AgreementStatus.DRAFT,
    )
    instructions_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidentiality_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    security_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subprocessor_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rights_assistance_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    breach_assistance_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dpia_assistance_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deletion_return_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audit_clause: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    assessment_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "party_id": self.party_id,
            "contract_id": self.contract_id,
            "signed_date": self.signed_date.isoformat() if self.signed_date else None,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "document_id": self.document_id,
            "status": self.status.value if self.status else None,
            "instructions_present": self.instructions_present,
            "confidentiality_clause": self.confidentiality_clause,
            "security_clause": self.security_clause,
            "subprocessor_clause": self.subprocessor_clause,
            "rights_assistance_clause": self.rights_assistance_clause,
            "breach_assistance_clause": self.breach_assistance_clause,
            "dpia_assistance_clause": self.dpia_assistance_clause,
            "deletion_return_clause": self.deletion_return_clause,
            "audit_clause": self.audit_clause,
            "assessment_notes": self.assessment_notes,
        }


class ProcessingProcessor(Base):
    __tablename__ = "privacypa_processing_processors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    party_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=False)
    processor_agreement_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("processing_activity_id", "party_id", name="uix_privacypa_processing_processor"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "party_id": self.party_id,
            "processor_agreement_id": self.processor_agreement_id,
            "contract_id": self.contract_id,
            "notes": self.notes,
        }


class Contract(Base):
    """Contratto con un fornitore (§18.3)."""

    __tablename__ = "privacypa_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Concorrenza ottimistica (§54): incrementato dal DB a ogni UPDATE, esposto come ETag.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    party_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cig: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cup: Mapped[str | None] = mapped_column(String(15), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    organizational_unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=True
    )
    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus, native_enum=False, length=20), nullable=False, default=ContractStatus.DRAFT
    )
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_contract_code"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "party_id": self.party_id,
            "code": self.code,
            "title": self.title,
            "cig": self.cig,
            "cup": self.cup,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "renewal_date": self.renewal_date.isoformat() if self.renewal_date else None,
            "organizational_unit_id": self.organizational_unit_id,
            "status": self.status.value if self.status else None,
            "document_id": self.document_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Subprocessor(Base):
    """Sub-responsabile di un responsabile del trattamento (§19)."""

    __tablename__ = "privacypa_subprocessors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processor_party_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_parties.id"), nullable=False, index=True
    )
    subprocessor_party_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_parties.id"), nullable=False, index=True
    )
    service_description: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_type: Mapped[SubprocessorAuthorization] = mapped_column(
        SAEnum(SubprocessorAuthorization, native_enum=False, length=20), nullable=False
    )
    authorization_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    countries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[SubprocessorStatus] = mapped_column(
        SAEnum(SubprocessorStatus, native_enum=False, length=20), nullable=False, default=SubprocessorStatus.TO_REVIEW
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "processor_party_id": self.processor_party_id,
            "subprocessor_party_id": self.subprocessor_party_id,
            "service_description": self.service_description,
            "authorization_type": self.authorization_type.value if self.authorization_type else None,
            "authorization_date": self.authorization_date.isoformat() if self.authorization_date else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "countries": self.countries or [],
            "status": self.status.value if self.status else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProcessingRecipient(Base):
    __tablename__ = "privacypa_processing_recipients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    processing_activity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("privacypa_processing_activities.id"), nullable=False, index=True
    )
    party_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_parties.id"), nullable=False)
    communication_type: Mapped[CommunicationType] = mapped_column(
        SAEnum(CommunicationType, native_enum=False, length=30),
        nullable=False,
        default=CommunicationType.COMMUNICATION,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_activity_id": self.processing_activity_id,
            "party_id": self.party_id,
            "communication_type": self.communication_type.value if self.communication_type else None,
            "notes": self.notes,
        }
