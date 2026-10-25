"""
Backup, verifica di ripristino e ripristino (§57).

Un backup è un *insieme*: la copia coerente del database (API di backup di SQLite, sicura con il database in uso) e, se ci sono
documenti, un archivio ZIP con la loro cartella e un manifest di checksum. Con `BACKUP_ENCRYPTION_KEY` i file sono cifrati
(`.enc`, vedi `backup_crypto`); con `BACKUP_OFFSITE_DIR` ogni file è copiato anche in un'altra destinazione (condivisione di
rete, altro volume) e la copia è verificata per checksum.

«Un backup non testato non è sufficiente»: `verify_backup` non si limita al checksum, ripete il ripristino in una copia
temporanea (decifra, controlla l'integrità, la revisione dello schema, i dati e la coerenza dei documenti) e non tocca il
database in uso. Il ripristino la esegue sempre prima di sovrascrivere.

Solo SQLite. Con PostgreSQL si usa `pg_dump`/copie del volume (vedi RUNBOOK_RIPRISTINO.md): il servizio lo dichiara, non finge.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.backup_models import BackupConfig, BackupLog
from backend.services import backup_crypto
from backend.services.backup_crypto import BackupCryptoError
from backend.utils.logging import get_logger

logger = get_logger(__name__)

DB_NAME = re.compile(r"^backup_(manual|scheduled)_(\d{8}_\d{6})\.db(\.enc)?$")
DOCS_NAME = re.compile(r"^documents_(manual|scheduled)_(\d{8}_\d{6})\.zip(\.enc)?$")
MANIFEST = "manifest.json"
MAX_REPORTED = 20


class BackupError(ValueError):
    """Operazione non possibile o backup non valido (400)."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_copy(source: Path, destination: Path) -> None:
    src, dst = sqlite3.connect(str(source)), sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _zip_documents(root: Path, destination: Path) -> int:
    files = {}
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            name = path.relative_to(root).as_posix()
            files[name] = _sha256(path)
            zf.write(path, name)
        zf.writestr(MANIFEST, json.dumps({"created_at": datetime.utcnow().isoformat(), "files": files}, sort_keys=True))
    return len(files)


class BackupService:
    """Servizio asincrono (la sessione è quella dell'applicazione); le operazioni sui file girano in un thread."""

    BACKUP_DIR = Path("backups")
    MAX_RETENTION = 30

    def __init__(self, db: AsyncSession, *, db_path: Path | str | None = None, backup_dir: Path | str | None = None,
                 documents_dir: Path | str | None = None, offsite_dir: Path | str | None = None):
        self.db = db
        self._db_path = Path(db_path) if db_path else None
        self.backup_dir = Path(backup_dir) if backup_dir else self.BACKUP_DIR
        self._documents_dir = Path(documents_dir) if documents_dir else None
        offsite = offsite_dir or os.getenv("BACKUP_OFFSITE_DIR")
        self.offsite_dir = Path(offsite) if offsite else None
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- percorsi
    @property
    def db_path(self) -> Path:
        if self._db_path is not None:
            return self._db_path
        url = make_url(os.getenv("DATABASE_URL") or "sqlite:///./PramaIA-PrivacyManager.db")
        if not url.get_backend_name() == "sqlite":
            raise BackupError("Il backup integrato vale solo per SQLite: con PostgreSQL usare pg_dump (vedi RUNBOOK_RIPRISTINO.md)")
        return Path(url.database or "")

    @property
    def documents_dir(self) -> Path:
        if self._documents_dir is not None:
            return self._documents_dir
        from backend.services import privacypa_document_service

        return Path(privacypa_document_service.STORAGE_ROOT)

    def _path(self, filename: str, pattern: re.Pattern = DB_NAME) -> Path:
        if not pattern.match(filename or ""):
            raise BackupError("Nome del backup non valido")
        path = self.backup_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Backup non trovato: {filename}")
        return path

    # ---------------------------------------------------------------- configurazione
    async def get_config(self) -> BackupConfig:
        config = (await self.db.execute(select(BackupConfig).order_by(BackupConfig.id))).scalars().first()
        if config is None:
            config = BackupConfig(enabled=True, schedule_hour=2, retention_count=7)
            self.db.add(config)
            await self.db.commit()
            await self.db.refresh(config)
        return config

    async def update_config(self, enabled: bool | None = None, schedule_hour: int | None = None, retention_count: int | None = None) -> BackupConfig:
        config = await self.get_config()
        if enabled is not None:
            config.enabled = enabled
        if schedule_hour is not None:
            if not 0 <= schedule_hour <= 23:
                raise BackupError("L'ora deve essere tra 0 e 23")
            config.schedule_hour = schedule_hour
        if retention_count is not None:
            if not 1 <= retention_count <= self.MAX_RETENTION:
                raise BackupError(f"Il numero di copie da conservare deve essere tra 1 e {self.MAX_RETENTION}")
            config.retention_count = retention_count
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def _log(self, operation: str, status: str, filename: str | None = None, size: int | None = None, error: str | None = None, user: str | None = None) -> None:
        self.db.add(BackupLog(operation=operation, status=status, filename=filename, file_size=size, error_message=(error or None) and error[:500], created_by=user))
        await self.db.commit()

    async def get_backup_logs(self, limit: int = 50) -> list[BackupLog]:
        return list((await self.db.execute(select(BackupLog).order_by(BackupLog.id.desc()).limit(limit))).scalars().all())

    # ---------------------------------------------------------------- creazione
    def _unique_stamp(self, kind: str) -> str:
        moment = datetime.now()
        while True:
            stamp = moment.strftime("%Y%m%d_%H%M%S")
            if not any((self.backup_dir / f"{prefix}_{kind}_{stamp}.{ext}{enc}").exists() for prefix, ext in (("backup", "db"), ("documents", "zip")) for enc in ("", ".enc")):
                return stamp
            moment += timedelta(seconds=1)

    def _seal(self, plain: Path) -> Path:
        """Cifra il file se è configurata la passphrase (e rimuove l'originale in chiaro)."""
        if not backup_crypto.passphrase():
            return plain
        sealed = plain.with_name(plain.name + backup_crypto.SUFFIX)
        backup_crypto.encrypt_file(plain, sealed)
        plain.unlink()
        return sealed

    def _copy_offsite(self, path: Path) -> None:
        self.offsite_dir.mkdir(parents=True, exist_ok=True)
        target, temp = self.offsite_dir / path.name, self.offsite_dir / (path.name + ".part")
        shutil.copyfile(path, temp)
        if _sha256(temp) != _sha256(path):
            temp.unlink(missing_ok=True)
            raise BackupError("La copia esterna non coincide con l'originale")
        os.replace(temp, target)

    async def create_backup(self, user_email: str | None = None, scheduled: bool = False, include_documents: bool = True) -> dict:
        kind = "scheduled" if scheduled else "manual"
        try:
            source = self.db_path
            if not source.is_file():
                raise FileNotFoundError(f"Database non trovato: {source}")
            stamp = self._unique_stamp(kind)
            db_file = await asyncio.to_thread(self._make_db_copy, source, self.backup_dir / f"backup_{kind}_{stamp}.db")
            docs_file = None
            root = self.documents_dir
            if include_documents and root.is_dir() and any(p.is_file() for p in root.rglob("*")):
                docs_file = await asyncio.to_thread(self._make_documents_archive, root, self.backup_dir / f"documents_{kind}_{stamp}.zip")
            offsite = await self._offsite_all(user_email, [db_file, docs_file])
            config = await self.get_config()
            config.last_backup_at = datetime.now()
            await self.db.commit()
            await self._log("backup", "success", db_file.name, db_file.stat().st_size, user=user_email)
            await self._apply_retention()
            return {
                "filename": db_file.name, "size": db_file.stat().st_size, "created_at": datetime.now().isoformat(), "type": kind,
                "encrypted": db_file.name.endswith(backup_crypto.SUFFIX), "documents_filename": docs_file.name if docs_file else None, "offsite": offsite,
            }
        except Exception as e:
            logger.error(f"Backup fallito: {e}", exc_info=True)
            await self.db.rollback()
            await self._log("backup", "error", error=str(e), user=user_email)
            raise

    def _make_db_copy(self, source: Path, destination: Path) -> Path:
        _sqlite_copy(source, destination)
        return self._seal(destination)

    def _make_documents_archive(self, root: Path, destination: Path) -> Path:
        _zip_documents(root, destination)
        return self._seal(destination)

    async def _offsite_all(self, user: str | None, paths: list[Path | None]) -> dict | None:
        if self.offsite_dir is None:
            return None
        outcome = {"ok": True, "error": None}
        for path in (p for p in paths if p):
            try:
                await asyncio.to_thread(self._copy_offsite, path)
                await self._log("offsite", "success", path.name, path.stat().st_size, user=user)
            except Exception as e:
                logger.error(f"Copia esterna fallita per {path.name}: {e}")
                outcome = {"ok": False, "error": str(e)}
                await self._log("offsite", "error", path.name, error=str(e), user=user)
        return outcome

    # ---------------------------------------------------------------- elenco ed eliminazione
    def list_backups(self) -> list[dict]:
        items = []
        for path in sorted((p for p in self.backup_dir.iterdir() if DB_NAME.match(p.name)), key=lambda p: (DB_NAME.match(p.name).group(2), p.name), reverse=True):
            kind, stamp, _ = DB_NAME.match(path.name).groups()
            sibling = next((p.name for p in self.backup_dir.iterdir() if (m := DOCS_NAME.match(p.name)) and m.group(1) == kind and m.group(2) == stamp), None)
            stat = path.stat()
            items.append({"filename": path.name, "size": stat.st_size, "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(), "type": kind,
                          "encrypted": path.name.endswith(backup_crypto.SUFFIX), "documents_filename": sibling})
        return items

    def _siblings(self, name: str) -> list[Path]:
        match = DB_NAME.match(name)
        kind, stamp = match.group(1), match.group(2)
        found = [self.backup_dir / name]
        found += [p for p in self.backup_dir.iterdir() if (m := DOCS_NAME.match(p.name)) and m.group(1) == kind and m.group(2) == stamp]
        return found

    async def delete_backup(self, filename: str, user_email: str | None = None) -> bool:
        self._path(filename)
        for path in self._siblings(filename):
            path.unlink(missing_ok=True)
        logger.info(f"Backup eliminato: {filename} (utente {user_email})")
        return True

    async def _apply_retention(self) -> None:
        keep = (await self.get_config()).retention_count
        for old in self.list_backups()[keep:]:
            for path in self._siblings(old["filename"]):
                path.unlink(missing_ok=True)
                if self.offsite_dir:
                    (self.offsite_dir / path.name).unlink(missing_ok=True)

    # ---------------------------------------------------------------- verifica (prova di ripristino)
    async def verify_backup(self, filename: str, user_email: str | None = None) -> dict:
        """Ripete il ripristino in una cartella temporanea e controlla il risultato. Non tocca il database in uso."""
        path = self._path(filename)
        match = DB_NAME.match(filename)
        docs = next((p for p in self._siblings(filename) if DOCS_NAME.match(p.name)), None)
        result = await asyncio.to_thread(self._verify_sync, path, docs)
        result = {"filename": filename, "documents_filename": docs.name if docs else None, **result}
        await self._log("verify", "success" if result["ok"] else "error", filename, error=None if result["ok"] else "; ".join(c["detail"] for c in result["checks"] if not c["ok"]), user=user_email)
        if not result["ok"]:
            logger.error(f"Verifica del backup {filename} FALLITA: {result['checks']}")
        return result

    def _verify_sync(self, path: Path, docs: Path | None) -> dict:
        checks, warnings, counts = [], [], {}

        def check(name: str, ok: bool, detail: str) -> bool:
            checks.append({"name": name, "ok": ok, "detail": detail})
            return ok

        with tempfile.TemporaryDirectory(prefix="privacypa-verify-") as tmp:
            work = Path(tmp) / "restore.db"
            try:
                if path.name.endswith(backup_crypto.SUFFIX):
                    backup_crypto.decrypt_file(path, work)
                    check("decifratura", True, "File decifrato")
                else:
                    shutil.copyfile(path, work)
            except BackupCryptoError as e:
                check("decifratura", False, str(e))
                return {"ok": False, "checks": checks, "warnings": warnings, "counts": counts}
            try:
                conn = sqlite3.connect(f"file:{work}?mode=ro", uri=True)
            except sqlite3.Error as e:
                check("apertura", False, f"Il file non è un database SQLite valido: {e}")
                return {"ok": False, "checks": checks, "warnings": warnings, "counts": counts}
            try:
                try:
                    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                except sqlite3.DatabaseError as e:
                    integrity = f"errore: {e}"
                if not check("integrità", integrity == "ok", "Nessun errore" if integrity == "ok" else f"integrity_check: {str(integrity)[:200]}"):
                    return {"ok": False, "checks": checks, "warnings": warnings, "counts": counts}
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self._check_schema(conn, tables, check, warnings)
                for table in ("privacypa_organizations", "privacypa_processing_activities", "privacypa_documents", "privacypa_audit_log"):
                    if table in tables:
                        counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if counts and not any(counts.values()):
                    warnings.append("Il backup non contiene dati di PrivacyPA (database vuoto)")
                if docs is not None:
                    self._check_documents(conn, tables, docs, Path(tmp), check, warnings)
                elif counts.get("privacypa_documents"):
                    warnings.append("Ci sono documenti ma nessun archivio dei file è associato a questo backup")
            finally:
                conn.close()
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "warnings": warnings, "counts": counts}

    @staticmethod
    def _check_schema(conn, tables: set[str], check, warnings: list[str]) -> None:
        from alembic.script import ScriptDirectory

        from backend.db.database import Base
        from backend.db.migrate import alembic_config

        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone() if "alembic_version" in tables else None
        known = {s.revision for s in ScriptDirectory.from_config(alembic_config()).walk_revisions()}
        if revision is None:
            warnings.append("Database senza revisione Alembic (gestito dal ponte di avvio): nessun controllo di versione")
        elif revision[0] not in known:
            check("schema", False, f"Revisione {revision[0]} sconosciuta a questa versione dell'applicazione (backup più recente del codice)")
            return
        elif revision[0] != head:
            warnings.append(f"Backup alla revisione {revision[0]}, l'applicazione è alla {head}: le migrazioni si applicheranno all'avvio (MIGRATIONS=alembic)")
        missing = sorted(t for t in Base.metadata.tables if t not in tables)
        if missing and (revision is None or revision[0] == head):
            check("schema", False, f"Tabelle mancanti: {', '.join(missing[:10])}")
        else:
            check("schema", True, "Schema compatibile" + (f" ({len(missing)} tabelle da creare con la migrazione)" if missing else ""))

    @staticmethod
    def _check_documents(conn, tables: set[str], archive: Path, tmp: Path, check, warnings: list[str]) -> None:
        source = archive
        try:
            if archive.name.endswith(backup_crypto.SUFFIX):
                source = tmp / "documents.zip"
                backup_crypto.decrypt_file(archive, source)
            with zipfile.ZipFile(source) as zf:
                broken = zf.testzip()
                if broken:
                    check("documenti", False, f"Archivio danneggiato (file {broken})")
                    return
                manifest = json.loads(zf.read(MANIFEST))["files"]
                bad = [n for n, sha in manifest.items() if hashlib.sha256(zf.read(n)).hexdigest() != sha]
                if bad:
                    check("documenti", False, f"{len(bad)} file non corrispondono al manifest: {', '.join(bad[:MAX_REPORTED])}")
                    return
                missing, mismatched = [], []
                if "privacypa_document_versions" in tables:
                    for key, sha in conn.execute("SELECT storage_key, sha256 FROM privacypa_document_versions WHERE storage_key != 'pending'"):
                        name = str(key).replace("\\", "/")
                        if name not in manifest:
                            missing.append(name)
                        elif sha and manifest[name] != sha:
                            mismatched.append(name)
                if missing or mismatched:
                    check("documenti", False, f"Coerenza database/archivio: {len(missing)} file mancanti, {len(mismatched)} con checksum diverso "
                                              f"({', '.join((missing + mismatched)[:MAX_REPORTED])})")
                else:
                    check("documenti", True, f"{len(manifest)} file, checksum e coerenza con il database verificati")
        except (BackupCryptoError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
            check("documenti", False, f"Archivio dei documenti non leggibile: {e}")

    # ---------------------------------------------------------------- ripristino
    async def restore_backup(self, filename: str, user_email: str | None = None) -> dict:
        """Ripristina il database. Prima verifica il backup e ne fa uno di sicurezza dello stato attuale."""
        try:
            path = self._path(filename)
            verdict = await self.verify_backup(filename, user_email)
            if not verdict["ok"]:
                raise BackupError("Il backup non supera la verifica: " + "; ".join(c["detail"] for c in verdict["checks"] if not c["ok"]))
            safety = await self.create_backup(user_email=user_email, include_documents=False)
            target = self.db_path
            await self.db.commit()
            await asyncio.to_thread(self._swap_database, path, target)
            engine = getattr(self.db, "bind", None)
            if engine is not None:
                await engine.dispose()
            await self.db.close()
            await self._log("restore", "success", filename, user=user_email)
            return {"filename": filename, "restored_at": datetime.now().isoformat(), "safety_backup": safety["filename"], "warnings": verdict["warnings"]}
        except Exception as e:
            logger.error(f"Ripristino fallito: {e}", exc_info=True)
            await self.db.rollback()
            await self._log("restore", "error", filename, error=str(e), user=user_email)
            raise

    @staticmethod
    def _swap_database(backup: Path, target: Path) -> None:
        staging = target.with_name(target.name + ".restoring")
        if backup.name.endswith(backup_crypto.SUFFIX):
            backup_crypto.decrypt_file(backup, staging)
        else:
            shutil.copyfile(backup, staging)
        for suffix in ("-wal", "-shm", "-journal"):                # file residui del database sostituito: corromperebbero quello nuovo
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(staging, target)

    async def restore_documents(self, filename: str, user_email: str | None = None) -> dict:
        """Estrae l'archivio dei documenti nella cartella di archiviazione **senza sovrascrivere** file diversi già presenti."""
        path = self._path(filename, DOCS_NAME)
        result = await asyncio.to_thread(self._extract_documents, path, self.documents_dir)
        await self._log("restore_documents", "success" if not result["conflicts"] else "error", filename, user=user_email,
                        error=f"{len(result['conflicts'])} file già presenti e diversi, non sovrascritti" if result["conflicts"] else None)
        return {"filename": filename, **result}

    @staticmethod
    def _extract_documents(archive: Path, root: Path) -> dict:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="privacypa-docs-") as tmp:
            source = archive
            if archive.name.endswith(backup_crypto.SUFFIX):
                source = Path(tmp) / "documents.zip"
                backup_crypto.decrypt_file(archive, source)
            restored, identical, conflicts = 0, 0, []
            base = root.resolve()
            with zipfile.ZipFile(source) as zf:
                manifest = json.loads(zf.read(MANIFEST))["files"]
                for name, sha in manifest.items():
                    target = (base / name).resolve()
                    if base not in target.parents:                   # percorso fuori dalla cartella (zip-slip)
                        raise BackupError(f"Percorso non ammesso nell'archivio: {name}")
                    data = zf.read(name)
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise BackupError(f"Il file {name} non corrisponde al manifest")
                    if target.exists():
                        if _sha256(target) == sha:
                            identical += 1
                        else:
                            conflicts.append(name)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    restored += 1
        return {"restored": restored, "identical": identical, "conflicts": conflicts}
