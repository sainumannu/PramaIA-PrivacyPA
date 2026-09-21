"""
PrivacyPA — Portabilità dei dati del tenant (§173, §174): pacchetto ZIP con dati, documenti e checksum.

Struttura del pacchetto:

    manifest.json     formato, versione, tabelle (righe e colonne), documenti inclusi ed esclusi
    checksums.txt     sha256 di ogni file del pacchetto (formato `sha256sum`)
    data/<tabella>.json   una lista JSON per tabella, solo righe dell'ente (audit compreso)
    documents/<codice>/v<n>_<nome>   i file dei documenti

Le tabelle senza `tenant_id` si esportano tramite la catena di chiavi esterne verso una tabella dell'ente. I file
con esito antimalware INFECTED non vengono mai esportati. I cataloghi globali (comuni a tutti gli enti) non
fanno parte dell'export; ne fanno parte le voci create dall'ente.
"""
import enum
import hashlib
import json
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.privacypa_confidentiality import effective_config
from backend.auth.privacypa_permissions import get_policy
from backend.db.database import Base
from backend.models.privacypa_documents import Document, DocumentVersion
from backend.services.privacypa_document_service import DocumentService, sanitize_filename

FORMAT = "privacypa-tenant-export"
FORMAT_VERSION = "1.0"
# Non esportate: contengono percorsi interni o stato tecnico senza valore per l'ente.
# I webhook portano segreti di firma cifrati e stato di consegna: mai nell'export.
EXCLUDED_TABLES = {"privacypa_import_jobs", "privacypa_webhook_subscriptions", "privacypa_webhook_deliveries"}
MAX_CHAIN_DEPTH = 4
# Elementi con livello di riservatezza (§134) e ciò che ne dipende: se il livello blocca l'export massivo (§135),
# il pacchetto conserva solo la riga strutturale, senza contenuto.
BLOCKABLE = {"privacypa_documents": "document", "privacypa_data_breaches": "data_breach", "privacypa_dpias": "dpia", "privacypa_projects": "project"}
CHILDREN = {
    "privacypa_document_versions": ("document_id", "document"), "privacypa_document_links": ("document_id", "document"),
    "privacypa_dpia_screening_answers": ("dpia_id", "dpia"), "privacypa_data_breach_links": ("data_breach_id", "data_breach"),
    "privacypa_project_reviews": ("project_id", "project"), "privacypa_project_artifacts": ("project_id", "project"),
}
POLYMORPHIC = {"privacypa_comments": "entity_type", "privacypa_custom_field_values": "entity_type", "privacypa_audit_log": "entity_type"}
STRUCTURAL = {
    "id", "tenant_id", "code", "row_version", "confidentiality_level", "deleted_at", "document_id", "dpia_id", "data_breach_id", "project_id",
    "entity_type", "entity_id", "event_type", "event_time", "action", "created_at", "version_no",
}


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _tenant_filter(table: Table, tenant_id: str, depth: int = 0):
    """Condizione SQL che limita la tabella alle righe dell'ente, oppure None se non è riconducibile."""
    if "tenant_id" in table.c:
        return table.c.tenant_id == tenant_id
    if depth >= MAX_CHAIN_DEPTH:
        return None
    candidates = []
    for fk in table.foreign_keys:
        parent = fk.column.table
        if parent is table:
            continue
        condition = _tenant_filter(parent, tenant_id, depth + 1)
        if condition is None:
            continue
        # Meglio i genitori con tenant_id obbligatorio (catalogo globale = tenant_id NULL, non riconducibile).
        strict = "tenant_id" in parent.c and not parent.c.tenant_id.nullable
        candidates.append((0 if strict else 1, fk, parent, condition))
    if not candidates:
        return None
    _, fk, parent, condition = sorted(candidates, key=lambda c: c[0])[0]
    return table.c[fk.parent.name].in_(select(fk.column).where(condition))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def build_tenant_export(db: AsyncSession, tenant_id: str, extracted_by: str) -> tuple[Path, dict]:
    """Crea il pacchetto in un file temporaneo (da eliminare dopo l'invio) e restituisce anche il manifest."""
    tables: dict[str, dict] = {}
    excluded: list[dict] = []
    checksums: dict[str, str] = {}
    policy = await get_policy(db, tenant_id)
    blocked_levels = {level for level, rule in effective_config(policy.confidentiality if policy else None)["levels"].items() if rule["block_bulk_export"]}
    blocked: dict[str, set[str]] = {kind: set() for kind in BLOCKABLE.values()}
    if blocked_levels:
        for table_name, kind in BLOCKABLE.items():
            table = Base.metadata.tables[table_name]
            found = await db.execute(select(table.c.id).where(table.c.tenant_id == tenant_id, table.c.confidentiality_level.in_(blocked_levels)))
            blocked[kind] = {row[0] for row in found.all()}
    redacted: dict[str, int] = {}

    def redact(table_name: str, row: dict) -> dict:
        """Riga senza contenuto se appartiene (o dipende da) un elemento il cui livello blocca l'export massivo."""
        kind = BLOCKABLE.get(table_name)
        target = row.get("id") if kind else None
        if table_name in CHILDREN:
            column, kind = CHILDREN[table_name]
            target = row.get(column)
        elif table_name in POLYMORPHIC:
            kind, target = row.get(POLYMORPHIC[table_name]), row.get("entity_id")
        if kind is None or target not in blocked.get(kind, ()):
            return row
        redacted[table_name] = redacted.get(table_name, 0) + 1
        return {k: (v if k in STRUCTURAL else None) for k, v in row.items()}

    handle = tempfile.NamedTemporaryFile(prefix="privacypa-export-", suffix=".zip", delete=False)
    handle.close()
    path = Path(handle.name)

    def add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
        zf.writestr(name, data)
        checksums[name] = hashlib.sha256(data).hexdigest()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in Base.metadata.sorted_tables:
            if not table.name.startswith("privacypa_"):
                continue
            if table.name in EXCLUDED_TABLES:
                excluded.append({"table": table.name, "reason": "stato tecnico interno"})
                continue
            condition = _tenant_filter(table, tenant_id)
            if condition is None:
                excluded.append({"table": table.name, "reason": "dati globali comuni a tutti gli enti"})
                continue
            rows = (await db.execute(select(table).where(condition))).mappings().all()
            name = f"data/{table.name.removeprefix('privacypa_')}.json"
            add(zf, name, json.dumps(
                [redact(table.name, {k: _jsonable(v) for k, v in row.items()}) for row in rows], ensure_ascii=False, indent=1, sort_keys=True,
            ).encode("utf-8"))
            tables[table.name] = {"file": name, "rows": len(rows), "columns": [c.name for c in table.columns]}

        included, skipped = 0, []
        svc = DocumentService(db)
        versions = (await db.execute(
            select(DocumentVersion, Document.code, Document.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.tenant_id == tenant_id)
            .order_by(Document.code, DocumentVersion.version_no)
        )).all()
        for version, code, document_id in versions:
            label = f"{code} v{version.version_no}"
            if document_id in blocked["document"]:
                skipped.append({"document": label, "reason": "livello di riservatezza: export massivo bloccato"})
                continue
            if version.scan_status == "INFECTED":
                skipped.append({"document": label, "reason": "minaccia rilevata dalla scansione antimalware"})
                continue
            try:
                source = svc.resolve_path(version)
            except ValueError:
                skipped.append({"document": label, "reason": "percorso di archiviazione non valido"})
                continue
            if not source.is_file():
                skipped.append({"document": label, "reason": "file non presente nello storage"})
                continue
            name = f"documents/{code}/v{version.version_no}_{sanitize_filename(version.file_name)}"
            zf.write(source, name)
            checksums[name] = _sha256_file(source)
            included += 1

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "tenant_id": tenant_id,
            "extracted_at": datetime.utcnow().isoformat(),
            "extracted_by": extracted_by,
            "tables": tables,
            "excluded_tables": excluded,
            "documents": {"included": included, "skipped": skipped},
            "redacted": {"levels": sorted(blocked_levels), "rows": redacted},
            "notes": "Le date sono in UTC. I file INFECTED non vengono esportati; gli elementi il cui livello di riservatezza blocca l'export massivo hanno solo la riga strutturale.",
        }
        add(zf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
        zf.writestr("checksums.txt", "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())))
    return path, manifest
