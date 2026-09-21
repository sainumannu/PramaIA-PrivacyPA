"""
PrivacyPA — Cataloghi di base (§13, §14, §15 della specifica funzionale).

Cataloghi riutilizzabili dal registro dei trattamenti (dominio centrale,
§2.2) e da futuri moduli. tenant_id nullable = voce del catalogo
standard/template, condivisa; valorizzato = estensione specifica del
tenant.
"""
import enum
import uuid

from sqlalchemy import String, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class GdprDataClass(str, enum.Enum):
    ORDINARY = "ORDINARY"
    SPECIAL_ART9 = "SPECIAL_ART9"
    CRIMINAL_ART10 = "CRIMINAL_ART10"


class LegalBasisCatalog(Base):
    """Basi giuridiche GDPR (§13). Catalogo globale, non tenant-aware."""

    __tablename__ = "privacypa_legal_basis_catalog"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)  # es. GDPR_6_1_C
    article: Mapped[str] = mapped_column(String(10), nullable=False)
    paragraph: Mapped[str | None] = mapped_column(String(10), nullable=True)
    letter: Mapped[str | None] = mapped_column(String(5), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "article": self.article,
            "paragraph": self.paragraph,
            "letter": self.letter,
            "description": self.description,
        }


class DataSubjectCategoryCatalog(Base):
    """Categorie di interessati (§14)."""

    __tablename__ = "privacypa_data_subject_category_catalog"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
        }


class DataCategoryCatalog(Base):
    """Categorie di dati, struttura gerarchica (§15)."""

    __tablename__ = "privacypa_data_category_catalog"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_data_category_catalog.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gdpr_class: Mapped[GdprDataClass] = mapped_column(
        SAEnum(GdprDataClass, native_enum=False, length=20), nullable=False, default=GdprDataClass.ORDINARY
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    parent: Mapped["DataCategoryCatalog | None"] = relationship(remote_side=[id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "parent_id": self.parent_id,
            "code": self.code,
            "name": self.name,
            "gdpr_class": self.gdpr_class.value if self.gdpr_class else None,
            "description": self.description,
        }
