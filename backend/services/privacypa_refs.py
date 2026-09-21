"""
PrivacyPA — Riferimenti tra elementi dello stesso ente (§141, isolamento tra tenant).

Un identificativo di un altro ente deve comportarsi come uno inesistente: mai accettato in un collegamento, mai una risposta
diversa da «non trovato» (nessun metadato dell'altro ente). I servizi più recenti lo verificano da sé (`_exists`); questi
helper servono ai servizi che validano un riferimento senza avere una classe base comune.

I cataloghi (categorie di dati, interessati, misure di sicurezza) possono essere globali (`tenant_id` nullo) o dell'ente: un
elemento di catalogo di un *altro* ente non è utilizzabile.
"""
from typing import Iterable

from sqlalchemy import select


class InvalidReference(ValueError):
    """Il riferimento non esiste nell'ente (400)."""


def _kinds() -> dict:
    # Import differiti: i modelli dei moduli funzionali si importano tra loro.
    from backend.models.privacypa_assessments import Dpia, InternationalTransfer
    from backend.models.privacypa_assets import Asset
    from backend.models.privacypa_compliance import CorrectiveAction, DataBreach, DataSubjectRequest, PrivacyAudit
    from backend.models.privacypa_documents import Document
    from backend.models.privacypa_notices import PrivacyNotice
    from backend.models.privacypa_organization import OrganizationalUnit
    from backend.models.privacypa_parties import Contract, Party, ProcessorAgreement, Subprocessor
    from backend.models.privacypa_processing import ProcessingActivity
    from backend.models.privacypa_projects import Project
    from backend.models.privacypa_risk import Risk, SecurityMeasure
    from backend.models.privacypa_tasks import PrivacyTask

    return {
        "unit": (OrganizationalUnit, "Struttura organizzativa non trovata", False), "party": (Party, "Soggetto non trovato", False),
        "processing": (ProcessingActivity, "Trattamento non trovato", False), "asset": (Asset, "Sistema non trovato", False),
        "document": (Document, "Documento non trovato", False), "dpia": (Dpia, "DPIA non trovata", False), "contract": (Contract, "Contratto non trovato", False),
        "task": (PrivacyTask, "Task non trovato", False), "notice": (PrivacyNotice, "Informativa non trovata", False), "breach": (DataBreach, "Violazione non trovata", False),
        "request": (DataSubjectRequest, "Richiesta non trovata", False), "action": (CorrectiveAction, "Azione correttiva non trovata", False),
        "audit": (PrivacyAudit, "Audit non trovato", False), "project": (Project, "Progetto non trovato", False), "transfer": (InternationalTransfer, "Trasferimento non trovato", False),
        "agreement": (ProcessorAgreement, "Atto art. 28 non trovato", False), "subprocessor": (Subprocessor, "Sub-responsabile non trovato", False),
        "risk": (Risk, "Rischio non trovato", True), "measure": (SecurityMeasure, "Misura di sicurezza non trovata", True),
    }


# tipo di elemento come compare nei collegamenti polimorfici → tipo verificabile
ENTITY_KINDS = {
    "processing_activity": "processing", "processing": "processing", "dpia": "dpia", "asset": "asset", "party": "party", "contract": "contract",
    "document": "document", "privacy_notice": "notice", "notice": "notice", "data_breach": "breach", "task": "task",
    "corrective_action": "action", "audit": "audit", "project": "project", "transfer": "transfer", "international_transfer": "transfer",
    "processor_agreement": "agreement", "subprocessor": "subprocessor", "data_subject_request": "request", "request": "request",
    "risk": "risk", "security_measure": "measure", "organizational_unit": "unit", "unit": "unit",
}
# tipo di origine di un task → tipo verificabile (MANUAL non ha un elemento)
TASK_SOURCES = {"PROCESSING": "processing", "ASSET": "asset", "PARTY": "party", "DOCUMENT": "document", "REQUEST": "request", "PROJECT": "project"}


async def ensure_exists(db, tenant_id: str, kind: str, ref_id: str | None) -> None:
    """Il riferimento (se indicato) esiste nell'ente e non è cancellato; altrimenti `InvalidReference`."""
    if not ref_id:
        return
    model, label, global_allowed = _kinds()[kind]
    stmt = select(model.id).where(model.id == ref_id)
    if hasattr(model, "tenant_id"):
        stmt = stmt.where((model.tenant_id == tenant_id) | model.tenant_id.is_(None)) if global_allowed else stmt.where(model.tenant_id == tenant_id)
    if hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))
    if (await db.execute(stmt.limit(1))).first() is None:
        raise InvalidReference(label)


async def ensure_entity(db, tenant_id: str, entity_type: str | None, entity_id: str | None) -> None:
    """Collegamento polimorfico `(tipo, id)`: se il tipo è noto l'elemento deve essere dell'ente; i tipi non riconosciuti non risolvono dati."""
    kind = ENTITY_KINDS.get((entity_type or "").strip().lower())
    if kind is not None:
        await ensure_exists(db, tenant_id, kind, entity_id)


async def ensure_task_source(db, tenant_id: str, source_type, source_id: str | None) -> None:
    kind = TASK_SOURCES.get(getattr(source_type, "value", source_type) or "")
    if kind is not None:
        await ensure_exists(db, tenant_id, kind, source_id)


async def ensure_catalog_usable(db, tenant_id: str, model, ids: Iterable[str] | None) -> None:
    """Voci di catalogo indicate: quelle che esistono devono essere globali o dell'ente (le inesistenti non si controllano: seguono le regole del chiamante)."""
    wanted = [i for i in (ids or []) if i]
    if not wanted:
        return
    foreign = await db.execute(select(model.id).where(model.id.in_(wanted), model.tenant_id.is_not(None), model.tenant_id != tenant_id).limit(1))
    if foreign.first() is not None:
        raise InvalidReference("Voce di catalogo non trovata")
