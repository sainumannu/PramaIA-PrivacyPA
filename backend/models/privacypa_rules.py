"""
PrivacyPA — Motore di regole: regole di conformità e finding persistenti (§38, §155).

Le regole hanno un'implementazione (registro in `privacypa_findings_service`) e una configurazione per ente
(attiva/disattiva, gravità, parametri) memorizzata qui, con una `version` che cresce a ogni modifica.
Ogni finding conserva versione e condizione della regola al momento del rilevamento (§155): se la regola
cambia, i finding storici restano leggibili con la definizione originale.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Severity(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FindingStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"  # presa in carico
    ACCEPTED = "ACCEPTED"          # accettata con motivazione (rischio accettato / falso positivo)
    RESOLVED = "RESOLVED"          # la condizione non sussiste più (solo automatico)


ACTIVE_STATUSES = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED, FindingStatus.ACCEPTED)


class ComplianceRule(Base):
    __tablename__ = "privacypa_compliance_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # `version` è anche la versione di concorrenza ottimistica (§54): ogni modifica la incrementa.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": version}
    tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, native_enum=False, length=10), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # {"condition": "testo con {parametri}", "params": {"horizon_days": 60}}
    rule_definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uix_privacypa_rule_code"),)

    @property
    def row_version(self) -> int:
        return self.version

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "enabled": self.enabled,
            "rule_definition": self.rule_definition,
            "version": self.version,
            "row_version": self.version,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ComplianceFinding(Base):
    __tablename__ = "privacypa_compliance_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": row_version}
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("privacypa_compliance_rules.id"), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # Versione, gravità e condizione della regola al momento del rilevamento (§155).
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, native_enum=False, length=10), nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Trattamento a cui il finding si riferisce: determina la visibilità per scope; None = anagrafica condivisa.
    processing_activity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Struttura dell'entità (violazioni, richieste, audit): limita la visibilità come per i trattamenti.
    organizational_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    # Uguale al fingerprint finché il finding è attivo, NULL quando è risolto: un indice univoco su
    # (tenant, active_key) impedisce duplicati anche con valutazioni concorrenti.
    active_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[FindingStatus] = mapped_column(
        SAEnum(FindingStatus, native_enum=False, length=20), nullable=False, default=FindingStatus.OPEN
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("uix_privacypa_finding_active", "tenant_id", "active_key", unique=True),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "row_version": self.row_version,
            "tenant_id": self.tenant_id,
            "rule_id": self.rule_id,
            # "code" resta il nome storico usato dall'interfaccia: coincide con il codice della regola.
            "code": self.rule_code,
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "condition": self.condition,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_code": self.entity_code,
            "processing_activity_id": self.processing_activity_id,
            "organizational_unit_id": self.organizational_unit_id,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "status": self.status.value,
            "message": self.message,
            "params": self.params or {},
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status_changed_by": self.status_changed_by,
            "status_changed_at": self.status_changed_at.isoformat() if self.status_changed_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_notes": self.resolution_notes,
        }
