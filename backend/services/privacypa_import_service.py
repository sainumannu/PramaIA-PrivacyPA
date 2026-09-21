"""
PrivacyPA — Import del Registro da XLSX/CSV (§67, §68).

Flusso: upload → dry-run (mapping, anomalie, anteprima; nessuna scrittura sul dominio) →
commit transazionale. Ogni trattamento importato nasce in DRAFT con
`source = LEGACY_IMPORT`. Non vengono create anagrafiche (fornitori, sistemi, cataloghi,
strutture): i valori non riconosciuti restano segnalati e conservati in `legacy_notes`.
"""
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assets import Asset
from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog, LegalBasisCatalog
from backend.models.privacypa_import import ImportJob, ImportStatus
from backend.models.privacypa_organization import OrganizationalUnit
from backend.models.privacypa_parties import Party
from backend.models.privacypa_processing import ProcessingActivity
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_document_service import sanitize_filename
from backend.services.privacypa_import_parser import (
    Anomaly,
    ImportFileError,
    NormalizedRow,
    Sheet,
    norm,
    parse_bool,
    parse_date,
    read_tabular,
    resolve_legal_basis,
    short,
    split_list,
    suggest_mapping,
    validate_mapping,
)
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_processing_service import ProcessingService

IMPORT_ROOT = Path("data/imports")
MAX_ANOMALIES = 500
PREVIEW_ROWS = 20
SOURCE_LEGACY = "LEGACY_IMPORT"

DEFAULT_OPTIONS = {"on_duplicate": "skip", "skip_invalid_rows": False}


class ImportFlowError(Exception):
    """Errore di flusso (stato non valido, job scaduto...): `status_code` guida la risposta HTTP."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ImportFailed(Exception):
    """Errore critico durante il commit: nessun dato è stato scritto."""


def _safe_path(key: str) -> Path:
    root = IMPORT_ROOT.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Percorso di storage non valido")
    return path


def _ttl() -> timedelta:
    return timedelta(hours=int(os.getenv("IMPORT_JOB_TTL_HOURS", "24")))


class Lookups:
    """Anagrafiche del tenant indicizzate per confronto tollerante (nome/codice normalizzati)."""

    def __init__(self):
        self.units: dict[str, list] = {}
        self.subjects: dict[str, list] = {}
        self.categories: dict[str, list] = {}
        self.parties: dict[str, list] = {}
        self.assets: dict[str, list] = {}
        self.legal: dict[str, str] = {}
        self.existing_keys: set[tuple] = set()

    @staticmethod
    def _index(items, *attrs) -> dict[str, list]:
        index: dict[str, list] = {}
        for item in items:
            for attr in attrs:
                key = norm(getattr(item, attr, None))
                if key and item not in index.setdefault(key, []):
                    index[key].append(item)
        return index


def _resolve_refs(tokens: list[str], index: dict[str, list]) -> tuple[list[str], list[str], list[str]]:
    """(id risolti, non trovati, ambigui)."""
    found, missing, ambiguous = [], [], []
    for token in tokens:
        candidates = index.get(norm(token), [])
        if len(candidates) == 1:
            if candidates[0].id not in found:
                found.append(candidates[0].id)
        elif candidates:
            ambiguous.append(token)
        else:
            missing.append(token)
    return found, missing, ambiguous


def dedup_key(title: str, unit_id: str | None, unit_text: str | None) -> tuple:
    return (norm(title), unit_id or f"text:{norm(unit_text)}")


def normalize_row(row_no: int, raw: dict, lk: Lookups) -> NormalizedRow:
    """Applica le regole di normalizzazione a una riga già ricondotta ai campi di destinazione."""
    out = NormalizedRow(row_no=row_no)
    add = lambda fld, code, sev, msg, val=None: out.anomalies.append(  # noqa: E731
        Anomaly(row_no, fld, code, sev, msg, short(val))
    )
    notes: list[str] = []

    title = str(raw.get("title") or "").strip()
    if not title:
        add("title", "TITLE_MISSING", "error", "Titolo del trattamento mancante")
    elif len(title) > 255:
        add("title", "TITLE_TOO_LONG", "error", "Titolo oltre 255 caratteri", title)
    out.data["title"] = title

    unit_text = str(raw.get("unit") or "").strip() or None
    unit_id = None
    if unit_text:
        candidates = lk.units.get(norm(unit_text), [])
        if len(candidates) == 1:
            unit_id = candidates[0].id
        elif candidates:
            add("unit", "UNIT_AMBIGUOUS", "warning", "Struttura ambigua: più strutture con lo stesso nome", unit_text)
            notes.append(f"Struttura indicata (ambigua): {unit_text}")
        else:
            add("unit", "UNIT_NOT_FOUND", "warning", "Struttura non presente in anagrafica", unit_text)
            notes.append(f"Struttura indicata (non associata): {unit_text}")
    else:
        add("unit", "UNIT_MISSING", "warning", "Struttura non indicata")
    out.data["organizational_unit_id"] = unit_id

    for fld in ("description", "source_of_data", "collection_method"):
        value = str(raw.get(fld) or "").strip() or None
        out.data[fld] = value

    purposes = split_list(raw.get("purposes"), commas=False)
    out.data["purposes"] = purposes

    # basi giuridiche
    bases, unresolved_bases = [], []
    for token in split_list(raw.get("legal_bases")):
        code = resolve_legal_basis(token, lk.legal)
        if code and code not in bases:
            bases.append(code)
        elif not code:
            unresolved_bases.append(token)
    for token in unresolved_bases:
        add("legal_bases", "LEGAL_BASIS_UNKNOWN", "warning", "Base giuridica non riconosciuta", token)
    if unresolved_bases:
        notes.append("Basi giuridiche non riconosciute: " + ", ".join(unresolved_bases))
    out.data["legal_basis_codes"] = bases

    # cataloghi, fornitori, sistemi: solo associazione con anagrafiche esistenti
    for fld, index, label, key in (
        ("data_subjects", lk.subjects, "Interessati", "data_subject_ids"),
        ("data_categories", lk.categories, "Categorie di dati", "data_category_ids"),
        ("recipients", lk.parties, "Destinatari", "recipient_ids"),
        ("processors", lk.parties, "Responsabili", "processor_ids"),
        ("assets", lk.assets, "Sistemi", "asset_ids"),
    ):
        tokens = split_list(raw.get(fld))
        found, missing, ambiguous = _resolve_refs(tokens, index)
        out.data[key] = found
        for token in missing:
            add(fld, "REFERENCE_NOT_FOUND", "warning", f"{label}: valore non presente in anagrafica", token)
        for token in ambiguous:
            add(fld, "REFERENCE_AMBIGUOUS", "warning", f"{label}: valore ambiguo", token)
        unresolved = missing + ambiguous
        if unresolved:
            notes.append(f"{label} non associati: " + ", ".join(unresolved))

    for fld in ("start_date", "last_review_date", "next_review_date"):
        cell = raw.get(fld)
        if cell in (None, ""):
            out.data[fld] = None
            continue
        parsed = parse_date(cell)
        if parsed is None:
            add(fld, "DATE_INVALID", "warning", "Data non riconosciuta (usare gg/mm/aaaa)", cell)
            notes.append(f"{fld} non valida: {short(cell, 40)}")
        out.data[fld] = parsed

    for fld in ("mandatory_provision", "automated_decision", "profiling"):
        cell = raw.get(fld)
        if cell in (None, ""):
            out.data[fld] = None if fld == "mandatory_provision" else False
            continue
        parsed = parse_bool(cell)
        if parsed is None:
            add(fld, "BOOLEAN_INVALID", "warning", "Valore non riconosciuto (usare sì/no)", cell)
            parsed = None if fld == "mandatory_provision" else False
        out.data[fld] = parsed

    for fld, label in (("owner_note", "Responsabile interno"), ("retention_note", "Conservazione"), ("notes", "Note")):
        value = str(raw.get(fld) or "").strip()
        if value:
            notes.append(f"{label}: {value}")
    out.data["legacy_notes"] = ("[Import legacy]\n" + "\n".join(notes)) if notes else None
    out.data["unit_text"] = unit_text

    out.outcome = "invalid" if any(a.severity == "error" for a in out.anomalies) else "ok"
    return out


class ImportService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    # ------------------------------------------------------------- job
    async def get(self, tenant_id: str, job_id: str) -> ImportJob | None:
        result = await self.db.execute(
            select(ImportJob).where(ImportJob.tenant_id == tenant_id, ImportJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_jobs(self, tenant_id: str) -> list[ImportJob]:
        result = await self.db.execute(
            select(ImportJob).where(ImportJob.tenant_id == tenant_id).order_by(ImportJob.created_at.desc()).limit(100)
        )
        return list(result.scalars().all())

    def _remove_file(self, job: ImportJob) -> None:
        if job.storage_key:
            try:
                _safe_path(job.storage_key).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            job.storage_key = None

    def _read_file(self, job: ImportJob) -> bytes:
        if not job.storage_key:
            raise ImportFlowError("Il file caricato non è più disponibile: ripetere l'upload", 410)
        if datetime.utcnow() - job.created_at > _ttl():
            self._remove_file(job)
            raise ImportFlowError("Import scaduto: ripetere l'upload", 410)
        try:
            return _safe_path(job.storage_key).read_bytes()
        except (OSError, ValueError):
            raise ImportFlowError("Il file caricato non è più disponibile: ripetere l'upload", 410)

    async def create_job(self, tenant_id: str, actor: str, filename: str, content: bytes) -> tuple[ImportJob, list[Sheet]]:
        sheets = read_tabular(content, filename)  # valida formato, limiti e leggibilità
        job = ImportJob(
            tenant_id=tenant_id,
            filename=sanitize_filename(filename)[:255],
            file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status=ImportStatus.UPLOADED,
            created_by=actor,
        )
        self.db.add(job)
        await self.db.flush()
        key = f"{tenant_id}/{job.id}"
        dest = _safe_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "xb") as fh:
            fh.write(content)
        job.storage_key = key
        await self.db.flush()
        await self.audit.log(
            tenant_id, "import_job", "upload", entity_id=job.id, actor_user_id=actor,
            metadata={"filename": job.filename, "sha256": job.sha256, "sheets": [s.name for s in sheets]},
        )
        return job, sheets

    async def discard(self, job: ImportJob, actor: str) -> None:
        if job.status in (ImportStatus.COMMITTED, ImportStatus.COMMITTING):
            raise ImportFlowError("Un import concluso non può essere scartato", 409)
        self._remove_file(job)
        job.status = ImportStatus.DISCARDED
        await self.db.flush()
        await self.audit.log(job.tenant_id, "import_job", "discard", entity_id=job.id, actor_user_id=actor)

    # ------------------------------------------------------------- lookups / normalizzazione
    async def _lookups(self, tenant_id: str) -> Lookups:
        lk = Lookups()

        async def rows(stmt):
            return list((await self.db.execute(stmt)).scalars().all())

        units = await rows(select(OrganizationalUnit).where(
            OrganizationalUnit.tenant_id == tenant_id, OrganizationalUnit.active.is_(True)))
        lk.units = Lookups._index(units, "name", "code")
        subjects = await rows(select(DataSubjectCategoryCatalog).where(
            (DataSubjectCategoryCatalog.tenant_id.is_(None)) | (DataSubjectCategoryCatalog.tenant_id == tenant_id)))
        lk.subjects = Lookups._index(subjects, "name", "code")
        categories = await rows(select(DataCategoryCatalog).where(
            (DataCategoryCatalog.tenant_id.is_(None)) | (DataCategoryCatalog.tenant_id == tenant_id)))
        lk.categories = Lookups._index(categories, "name", "code")
        parties = await rows(select(Party).where(Party.tenant_id == tenant_id, Party.deleted_at.is_(None)))
        lk.parties = Lookups._index(parties, "legal_name", "code")
        assets = await rows(select(Asset).where(Asset.tenant_id == tenant_id, Asset.deleted_at.is_(None)))
        lk.assets = Lookups._index(assets, "name", "code")
        lk.legal = {b.code: b.description for b in await rows(select(LegalBasisCatalog))}
        existing = await rows(select(ProcessingActivity).where(
            ProcessingActivity.tenant_id == tenant_id,
            ProcessingActivity.deleted_at.is_(None),
            ProcessingActivity.is_current.is_(True),
        ))
        lk.existing_keys = {dedup_key(a.title, a.organizational_unit_id, None) for a in existing}
        return lk

    async def _plan(self, job: ImportJob, sheet_name: str, mapping: dict, options: dict) -> tuple[list[NormalizedRow], list[str]]:
        sheets = {s.name: s for s in read_tabular(self._read_file(job), job.filename)}
        sheet = sheets.get(sheet_name)
        if sheet is None:
            raise ImportFlowError(f"Foglio non trovato: {sheet_name}", 422)
        try:
            validate_mapping(mapping, sheet.headers)
        except ImportFileError as exc:
            raise ImportFlowError(str(exc), 422)
        lk = await self._lookups(job.tenant_id)
        seen: set[tuple] = set()
        rows: list[NormalizedRow] = []
        for row_no, values in sheet.as_dicts():
            raw = {fld: values.get(col) for fld, col in mapping.items()}
            row = normalize_row(row_no, raw, lk)
            if row.outcome == "ok":
                key = dedup_key(row.data["title"], row.data["organizational_unit_id"], row.data["unit_text"])
                in_file = key in seen
                existing = key in lk.existing_keys and row.data["organizational_unit_id"] is not None
                if in_file or existing:
                    row.anomalies.append(Anomaly(
                        row.row_no, "title",
                        "DUPLICATE_IN_FILE" if in_file else "DUPLICATE_EXISTING", "warning",
                        "Trattamento già presente nel file" if in_file else "Trattamento già presente nel registro",
                        short(row.data["title"]),
                    ))
                    if options["on_duplicate"] == "skip":
                        row.outcome = "duplicate"
                seen.add(key)
            rows.append(row)
        return rows, sheet.headers

    @staticmethod
    def _summarize(rows: list[NormalizedRow], options: dict) -> dict:
        counts = {"rows_total": len(rows), "ok": 0, "invalid": 0, "duplicates": 0, "warnings": 0}
        for r in rows:
            counts["ok" if r.outcome == "ok" else "invalid" if r.outcome == "invalid" else "duplicates"] += 1
            counts["warnings"] += sum(1 for a in r.anomalies if a.severity == "warning")
        counts["to_import"] = counts["ok"]
        blocked = counts["invalid"] > 0 and not options["skip_invalid_rows"]
        counts["can_commit"] = counts["ok"] > 0 and not blocked
        counts["blocked_by_invalid_rows"] = blocked
        return counts

    # ------------------------------------------------------------- dry-run
    async def dry_run(self, job: ImportJob, actor: str, sheet: str, mapping: dict, options: dict | None) -> dict:
        if job.status not in (ImportStatus.UPLOADED, ImportStatus.DRY_RUN, ImportStatus.FAILED):
            raise ImportFlowError("L'import non è più modificabile", 409)
        opts = {**DEFAULT_OPTIONS, **(options or {})}
        if opts["on_duplicate"] not in ("skip", "import"):
            raise ImportFlowError("on_duplicate ammette solo 'skip' o 'import'", 422)
        opts["skip_invalid_rows"] = bool(opts["skip_invalid_rows"])
        rows, _ = await self._plan(job, sheet, mapping, opts)
        counts = self._summarize(rows, opts)
        anomalies = [a.to_dict() for r in rows for a in r.anomalies]
        report = {
            "counts": counts,
            "anomalies": anomalies[:MAX_ANOMALIES],
            "anomalies_truncated": len(anomalies) > MAX_ANOMALIES,
            "preview": [
                {
                    "row": r.row_no, "outcome": r.outcome, "title": r.data["title"],
                    "unit_resolved": bool(r.data["organizational_unit_id"]),
                    "purposes": len(r.data["purposes"]), "legal_bases": r.data["legal_basis_codes"],
                    "data_subjects": len(r.data["data_subject_ids"]), "data_categories": len(r.data["data_category_ids"]),
                }
                for r in rows[:PREVIEW_ROWS]
            ],
        }
        job.sheet, job.mapping, job.options, job.report = sheet, mapping, opts, report
        job.status = ImportStatus.DRY_RUN
        await self.db.flush()
        await self.audit.log(
            job.tenant_id, "import_job", "dry_run", entity_id=job.id, actor_user_id=actor,
            metadata={"sheet": sheet, **{k: v for k, v in counts.items() if isinstance(v, int)}},
        )
        return report

    # ------------------------------------------------------------- commit
    async def commit(self, job: ImportJob, actor: str) -> dict:
        if job.status == ImportStatus.COMMITTED:
            return {**(job.report or {}), "already_committed": True}
        if job.status != ImportStatus.DRY_RUN or not job.mapping:
            raise ImportFlowError("Eseguire prima il dry-run e verificarne l'esito", 409)
        opts = job.options or DEFAULT_OPTIONS
        rows, _ = await self._plan(job, job.sheet, job.mapping, opts)
        counts = self._summarize(rows, opts)
        expected = (job.report or {}).get("counts", {})
        if counts["to_import"] != expected.get("to_import"):
            raise ImportFlowError(
                "Le anagrafiche sono cambiate dopo il dry-run: rieseguire il dry-run prima di importare", 409
            )
        if counts["blocked_by_invalid_rows"]:
            raise ImportFlowError(
                f"{counts['invalid']} righe non valide: correggere il file oppure scegliere di ignorarle", 422
            )
        if not counts["to_import"]:
            raise ImportFlowError("Nessuna riga importabile", 422)

        # compare-and-set: due commit concorrenti non importano due volte
        claimed = await self.db.execute(
            update(ImportJob)
            .where(ImportJob.id == job.id, ImportJob.status == ImportStatus.DRY_RUN)
            .values(status=ImportStatus.COMMITTING)
        )
        if claimed.rowcount != 1:
            raise ImportFlowError("Import già in corso", 409)
        await self.db.flush()

        tenant_id, job_id = job.tenant_id, job.id
        created_codes: list[str] = []
        try:
            proc = ProcessingService(self.db)
            for row in (r for r in rows if r.outcome == "ok"):
                d = row.data
                activity = await proc.create_activity(
                    tenant_id, actor, d["title"],
                    organizational_unit_id=d["organizational_unit_id"],
                    description=d["description"], source_of_data=d["source_of_data"],
                    collection_method=d["collection_method"],
                    mandatory_provision=d["mandatory_provision"],
                    automated_decision=d["automated_decision"], profiling=d["profiling"],
                    start_date=d["start_date"], last_review_date=d["last_review_date"],
                    next_review_date=d["next_review_date"],
                    purposes=d["purposes"] or None,
                    legal_basis_codes=d["legal_basis_codes"] or None,
                    data_subject_ids=d["data_subject_ids"] or None,
                    data_category_ids=d["data_category_ids"] or None,
                    source=SOURCE_LEGACY, source_reference=f"import:{job_id}:row:{row.row_no}",
                    legacy_notes=d["legacy_notes"],
                )
                if d["asset_ids"]:
                    await AssetService(self.db).set_links(activity.id, [{"asset_id": i} for i in d["asset_ids"]])
                if d["processor_ids"]:
                    await PartyService(self.db).set_processors(activity.id, [{"party_id": i} for i in d["processor_ids"]])
                if d["recipient_ids"]:
                    await PartyService(self.db).set_recipients(activity.id, [{"party_id": i} for i in d["recipient_ids"]])
                created_codes.append(activity.code)
        except Exception as exc:
            await self.db.rollback()  # annulla tutto ciò che questo import ha scritto
            failed = await self.get(tenant_id, job_id)
            failed.status = ImportStatus.FAILED
            failed.report = {**(failed.report or {}), "error": f"{type(exc).__name__}"}
            await self.db.flush()
            await self.audit.log(
                tenant_id, "import_job", "commit_failed", entity_id=job_id, actor_user_id=actor,
                metadata={"error": type(exc).__name__},
            )
            raise ImportFailed(str(exc)) from exc

        job.status = ImportStatus.COMMITTED
        job.committed_at = datetime.utcnow()
        job.report = {
            **(job.report or {}),
            "commit": {
                "created": len(created_codes),
                "skipped_duplicates": counts["duplicates"],
                "skipped_invalid": counts["invalid"],
                "first_code": created_codes[0], "last_code": created_codes[-1],
            },
        }
        self._remove_file(job)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "import_job", "commit", entity_id=job_id, actor_user_id=actor,
            event_type="import.processing_activities.commit",
            metadata=job.report["commit"] | {"sha256": job.sha256},
        )
        return {**job.report, "already_committed": False}

    @staticmethod
    def describe_sheets(sheets: list[Sheet]) -> list[dict]:
        return [
            {"name": s.name, "columns": s.headers, "row_count": len(s.rows), "suggested_mapping": suggest_mapping(s.headers)}
            for s in sheets
        ]
