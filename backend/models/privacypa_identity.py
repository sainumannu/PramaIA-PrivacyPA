"""
PrivacyPA — Identity e ruoli privacy (§9 della specifica funzionale).

Catalogo dei ruoli privacy applicativi (distinto dai ruoli tecnici
app-admin già gestiti da backend/models/permissions_models.py) e loro
assegnazione agli utenti con scope organizzativo (§9.3, §10).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PrivacyRoleCode(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    PRIVACY_MANAGER = "PRIVACY_MANAGER"
    DPO = "DPO"
    MANAGER = "MANAGER"
    PRIVACY_REFERENT = "PRIVACY_REFERENT"
    ICT = "ICT"
    RTD = "RTD"
    OPERATOR = "OPERATOR"
    AUDITOR = "AUDITOR"
    READ_ONLY = "READ_ONLY"
    # Account di servizio di un sistema esterno (es. VideoPA): vede solo `integration.*`, mai gli altri moduli.
    INTEGRATION = "INTEGRATION"
    # Account di servizio dell'HelpDesk (richieste di accesso): vede solo `access.*` e il registro degli autorizzati.
    INTEGRATION_HELPDESK = "INTEGRATION_HELPDESK"


class ScopeType(str, enum.Enum):
    TENANT = "TENANT"       # intero ente
    UNIT = "UNIT"           # una struttura
    SUBTREE = "SUBTREE"     # un sottoalbero organizzativo
    RECORD = "RECORD"       # singoli record


class PrivacyRoleCatalog(Base):
    """Catalogo dei ruoli privacy disponibili (§9.2)."""

    __tablename__ = "privacypa_role_catalog"

    code: Mapped[PrivacyRoleCode] = mapped_column(
        SAEnum(PrivacyRoleCode, native_enum=False, length=30), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def to_dict(self) -> dict:
        return {
            "code": self.code.value if self.code else None,
            "name": self.name,
            "description": self.description,
        }


class UserRoleAssignment(Base):
    """
    Assegnazione di un ruolo privacy a un utente con scope (§9.3).

    Lo scope può essere l'intero ente (TENANT), una struttura (UNIT),
    un sottoalbero organizzativo (SUBTREE) o un singolo record (RECORD).
    Per UNIT/SUBTREE, organizational_unit_id indica la radice dello scope.
    """

    __tablename__ = "privacypa_user_role_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    role_code: Mapped[PrivacyRoleCode] = mapped_column(
        SAEnum(PrivacyRoleCode, native_enum=False, length=30),
        ForeignKey("privacypa_role_catalog.code"),
        nullable=False,
    )
    organizational_unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("privacypa_organizational_units.id"), nullable=True
    )
    scope_type: Mapped[ScopeType] = mapped_column(
        SAEnum(ScopeType, native_enum=False, length=20), nullable=False, default=ScopeType.TENANT
    )

    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    granted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role: Mapped["PrivacyRoleCatalog"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "role_code", "organizational_unit_id",
            name="uix_privacypa_user_role_scope",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role_code": self.role_code.value if self.role_code else None,
            "organizational_unit_id": self.organizational_unit_id,
            "scope_type": self.scope_type.value if self.scope_type else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
        }
