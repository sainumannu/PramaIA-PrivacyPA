"""Backup, verifica di ripristino e ripristino (§57): cifratura, documenti, copia esterna, retention, pianificatore, API."""
import hashlib
import json
import os
import sqlite3
import zipfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import backend.db.init_db  # noqa: F401  (registra tutti i modelli)
from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.db.database import Base, get_db
from backend.db.migrate import upgrade_database
from backend.models.backup_models import BackupLog
from backend.models.privacypa_documents import Document, DocumentVersion
from backend.models.privacypa_organization import Organization
from backend.routers import backup_router
from backend.services import backup_crypto, privacypa_document_service
from backend.services.backup_crypto import BackupCryptoError, decrypt_file, encrypt_file, is_encrypted
from backend.services.backup_scheduler import run_scheduled_backup
from backend.services.backup_service import BackupError, BackupService

KEY = "passphrase-di-prova-lunga"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("BACKUP_ENCRYPTION_KEY", "BACKUP_OFFSITE_DIR"):
        monkeypatch.delenv(name, raising=False)


# ================================================================ cifratura

def test_encryption_round_trip_including_empty_and_multi_chunk_files(tmp_path):
    for size in (0, 1, 1024 * 1024, 1024 * 1024 + 1, int(2.5 * 1024 * 1024)):
        src, enc, back = tmp_path / f"s{size}", tmp_path / f"e{size}", tmp_path / f"b{size}"
        src.write_bytes(os.urandom(size))
        encrypt_file(src, enc, KEY)
        assert is_encrypted(enc) and not is_encrypted(src) and enc.read_bytes()[:6] == b"PPABK1"
        decrypt_file(enc, back, KEY)
        assert back.read_bytes() == src.read_bytes(), size
    assert (tmp_path / "e1").read_bytes()[backup_crypto.HEADER_LEN:] != (tmp_path / "s1").read_bytes()               # il contenuto non compare in chiaro


def test_encryption_uses_fresh_salt_and_rejects_wrong_key_tampering_and_truncation(tmp_path):
    src = tmp_path / "plain"
    src.write_bytes(os.urandom(int(2.2 * 1024 * 1024)))
    a, b = tmp_path / "a.enc", tmp_path / "b.enc"
    encrypt_file(src, a, KEY)
    encrypt_file(src, b, KEY)
    assert a.read_bytes() != b.read_bytes()                                                     # sale e nonce casuali

    out = tmp_path / "out"
    with pytest.raises(BackupCryptoError, match="errata"):
        decrypt_file(a, out, "altra-passphrase")
    assert not out.exists()                                                                     # niente file parziale
    data = bytearray(a.read_bytes())
    data[len(data) // 2] ^= 0xFF
    (tmp_path / "tampered.enc").write_bytes(bytes(data))
    with pytest.raises(BackupCryptoError):
        decrypt_file(tmp_path / "tampered.enc", out, KEY)

    raw = a.read_bytes()
    header = backup_crypto.HEADER_LEN
    first_chunk = header + 4 + int.from_bytes(raw[header:header + 4], "big")
    (tmp_path / "cut.enc").write_bytes(raw[:first_chunk])                                        # troncato esattamente tra due blocchi
    with pytest.raises(BackupCryptoError):
        decrypt_file(tmp_path / "cut.enc", out, KEY)
    (tmp_path / "cut2.enc").write_bytes(raw[:-5])
    with pytest.raises(BackupCryptoError):
        decrypt_file(tmp_path / "cut2.enc", out, KEY)
    (tmp_path / "plain.enc").write_bytes(b"non cifrato")
    with pytest.raises(BackupCryptoError, match="non è un backup cifrato"):
        decrypt_file(tmp_path / "plain.enc", out, KEY)
    with pytest.raises(BackupCryptoError, match="Passphrase"):
        encrypt_file(src, out)
    (tmp_path / "swap.enc").write_bytes(raw[:header] + raw[first_chunk:])  # blocco tolto dalla testa
    with pytest.raises(BackupCryptoError):
        decrypt_file(tmp_path / "swap.enc", out, KEY)


# ================================================================ ambiente di prova

@pytest_asyncio.fixture
async def world(tmp_path):
    """Un database SQLite su file con qualche dato, una cartella di documenti coerente e un servizio già configurato."""
    db_file, docs = tmp_path / "app.db", tmp_path / "docs"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    (docs / "TA").mkdir(parents=True)
    content = b"contenuto del documento"
    (docs / "TA" / "doc1.bin").write_bytes(content)
    async with maker() as db:
        db.add(Organization(id="org-1", tenant_id="TA", name="Ente di prova"))
        doc = Document(tenant_id="TA", code="DOC-000001", title="Nomina")
        db.add(doc)
        await db.flush()
        db.add(DocumentVersion(document_id=doc.id, version_no=1, file_name="n.txt", mime_type="text/plain", file_size=len(content),
                               storage_key="TA/doc1.bin", sha256=hashlib.sha256(content).hexdigest()))
        await db.commit()
    world = {"engine": engine, "maker": maker, "db_file": db_file, "docs": docs, "backups": tmp_path / "backups", "tmp": tmp_path}

    def service(db, **override):
        return BackupService(db, db_path=db_file, backup_dir=world["backups"], documents_dir=docs, **override)

    world["service"] = service
    yield world
    await engine.dispose()


async def _orgs(world) -> int:
    async with world["maker"]() as db:
        return len((await db.execute(select(Organization))).scalars().all())


async def _create(world, **kw):
    async with world["maker"]() as db:
        return await world["service"](db).create_backup(user_email="admin@example.org", **kw)


async def _verify(world, filename):
    async with world["maker"]() as db:
        return await world["service"](db).verify_backup(filename, "admin@example.org")


def _failed(result) -> dict[str, str]:
    return {c["name"]: c["detail"] for c in result["checks"] if not c["ok"]}


# ================================================================ creazione, elenco, verifica

@pytest.mark.asyncio
async def test_backup_creates_a_consistent_set_and_it_verifies(world):
    info = await _create(world)
    assert info["type"] == "manual" and info["encrypted"] is False and info["offsite"] is None and info["documents_filename"].startswith("documents_manual_")
    assert (world["backups"] / info["filename"]).is_file() and (world["backups"] / info["documents_filename"]).is_file()
    copy = sqlite3.connect(world["backups"] / info["filename"])
    assert copy.execute("SELECT name FROM privacypa_organizations").fetchone() == ("Ente di prova",)
    copy.close()

    result = await _verify(world, info["filename"])
    assert result["ok"] is True and result["documents_filename"] == info["documents_filename"]
    assert {c["name"] for c in result["checks"]} == {"integrità", "schema", "documenti"}
    assert result["counts"]["privacypa_organizations"] == 1 and result["counts"]["privacypa_documents"] == 1
    assert any("revisione Alembic" in w for w in result["warnings"])                              # creato con create_all: nessuna revisione

    async with world["maker"]() as db:
        service = world["service"](db)
        assert [b["filename"] for b in service.list_backups()] == [info["filename"]]
        assert service.list_backups()[0]["documents_filename"] == info["documents_filename"]
        logs = await service.get_backup_logs()
        assert [(l.operation, l.status) for l in logs] == [("verify", "success"), ("backup", "success")]
        assert (await service.get_config()).last_backup_at is not None


@pytest.mark.asyncio
async def test_encrypted_backup_leaves_no_plaintext_and_needs_the_key_to_verify(world, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY)
    info = await _create(world)
    assert info["encrypted"] is True and info["filename"].endswith(".db.enc") and info["documents_filename"].endswith(".zip.enc")
    assert sorted(p.name for p in world["backups"].iterdir()) == sorted([info["filename"], info["documents_filename"]])          # nessun file in chiaro
    raw = (world["backups"] / info["filename"]).read_bytes()
    assert b"SQLite format" not in raw and b"Ente di prova" not in raw
    ok = await _verify(world, info["filename"])
    assert ok["ok"] and ok["checks"][0] == {"name": "decifratura", "ok": True, "detail": "File decifrato"}

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "un'altra passphrase")
    bad = await _verify(world, info["filename"])
    assert bad["ok"] is False and "decifratura" in _failed(bad)
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY")
    assert "Passphrase" in _failed(await _verify(world, info["filename"]))["decifratura"]


@pytest.mark.asyncio
async def test_verification_catches_a_corrupted_database(world):
    info = await _create(world, include_documents=False)
    path = world["backups"] / info["filename"]
    data = bytearray(path.read_bytes())
    for i in range(4096 * 2, 4096 * 2 + 200):                                                    # pagine interne rovinate
        data[i] = 0xAB
    path.write_bytes(bytes(data))
    result = await _verify(world, info["filename"])
    assert result["ok"] is False and ("integrità" in _failed(result) or "apertura" in _failed(result))
    path.write_bytes(b"questo non e' un database")
    assert not (await _verify(world, info["filename"]))["ok"]
    async with world["maker"]() as db:
        assert (await world["service"](db).get_backup_logs(1))[0].status == "error"


@pytest.mark.asyncio
async def test_verification_catches_document_problems(world):
    info = await _create(world)
    docs_path = world["backups"] / info["documents_filename"]
    good = docs_path.read_bytes()

    # un file dell'archivio alterato rispetto al manifest
    with zipfile.ZipFile(docs_path) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    entries["TA/doc1.bin"] = b"alterato"
    with zipfile.ZipFile(docs_path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    assert "non corrispondono al manifest" in _failed(await _verify(world, info["filename"]))["documenti"]

    # archivio coerente con il proprio manifest ma senza il file che il database si aspetta
    manifest = json.loads(entries["manifest.json"])
    manifest["files"].pop("TA/doc1.bin")
    with zipfile.ZipFile(docs_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    assert "1 file mancanti" in _failed(await _verify(world, info["filename"]))["documenti"]

    # checksum del database diverso da quello archiviato
    docs_path.write_bytes(good)
    con = sqlite3.connect(world["backups"] / info["filename"])
    con.execute("UPDATE privacypa_document_versions SET sha256 = 'ff'")
    con.commit()
    con.close()
    assert "checksum diverso" in _failed(await _verify(world, info["filename"]))["documenti"]
    docs_path.write_bytes(b"non e' uno zip")
    assert "non leggibile" in _failed(await _verify(world, info["filename"]))["documenti"]


@pytest.mark.asyncio
async def test_documents_without_archive_and_empty_database_are_warnings(world):
    info = await _create(world, include_documents=False)
    result = await _verify(world, info["filename"])
    assert result["ok"] and any("nessun archivio" in w for w in result["warnings"])
    # database creato ma senza dati di PrivacyPA
    con = sqlite3.connect(world["db_file"])
    for table in ("privacypa_document_versions", "privacypa_documents", "privacypa_organizations"):
        con.execute(f"DELETE FROM {table}")
    con.commit()
    con.close()
    empty = await _create(world, include_documents=False)
    assert any("database vuoto" in w for w in (await _verify(world, empty["filename"]))["warnings"])


@pytest.mark.asyncio
async def test_schema_check_uses_the_alembic_revision(world, tmp_path):
    migrated = tmp_path / "migrated.db"
    upgrade_database(f"sqlite:///{migrated.as_posix()}")
    async with world["maker"]() as db:
        service = BackupService(db, db_path=migrated, backup_dir=world["backups"], documents_dir=tmp_path / "nodocs")
        info = await service.create_backup()
        assert (await service.verify_backup(info["filename"]))["ok"]                              # allo stesso livello del codice: nessun avviso di versione

        con = sqlite3.connect(migrated)
        con.execute("UPDATE alembic_version SET version_num = '0005'")
        con.commit()
        con.close()
        older = await service.create_backup()
        result = await service.verify_backup(older["filename"])
        assert result["ok"] and any("0005" in w and "migrazioni" in w for w in result["warnings"])

        con = sqlite3.connect(migrated)
        con.execute("UPDATE alembic_version SET version_num = '9999'")
        con.commit()
        con.close()
        newer = await service.create_backup()
        assert "più recente del codice" in _failed(await service.verify_backup(newer["filename"]))["schema"]

        con = sqlite3.connect(migrated)
        con.execute("UPDATE alembic_version SET version_num = (SELECT version_num FROM alembic_version LIMIT 1)")
        head = "0012"
        con.execute("UPDATE alembic_version SET version_num = ?", (head,))
        con.execute("DROP TABLE privacypa_tags")
        con.commit()
        con.close()
        broken = await service.create_backup()
        assert "Tabelle mancanti" in _failed(await service.verify_backup(broken["filename"]))["schema"]


# ================================================================ ripristino

@pytest.mark.asyncio
async def test_restore_brings_back_the_data_makes_a_safety_backup_and_drops_stale_wal(world):
    info = await _create(world)
    assert await _orgs(world) == 1
    async with world["maker"]() as db:
        await db.execute(text("DELETE FROM privacypa_organizations"))
        await db.commit()
    assert await _orgs(world) == 0
    Path(str(world["db_file"]) + "-wal").write_bytes(b"residuo")
    Path(str(world["db_file"]) + "-shm").write_bytes(b"residuo")

    async with world["maker"]() as db:
        result = await world["service"](db).restore_backup(info["filename"], "admin@example.org")
    assert result["safety_backup"] != info["filename"] and (world["backups"] / result["safety_backup"]).is_file()
    assert await _orgs(world) == 1
    assert not Path(str(world["db_file"]) + "-wal").exists() and not Path(str(world["db_file"]) + "-shm").exists()
    assert not world["db_file"].with_name("app.db.restoring").exists()
    # il backup di sicurezza contiene lo stato prima del ripristino (senza l'ente)
    safety = sqlite3.connect(world["backups"] / result["safety_backup"])
    assert safety.execute("SELECT COUNT(*) FROM privacypa_organizations").fetchone() == (0,)
    safety.close()
    async with world["maker"]() as db:
        assert ("restore", "success") in [(l.operation, l.status) for l in await world["service"](db).get_backup_logs()]


@pytest.mark.asyncio
async def test_restore_of_an_encrypted_backup_and_refusal_of_a_bad_one(world, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY)
    info = await _create(world, include_documents=False)
    async with world["maker"]() as db:
        await db.execute(text("DELETE FROM privacypa_organizations"))
        await db.commit()
    async with world["maker"]() as db:
        await world["service"](db).restore_backup(info["filename"])
    assert await _orgs(world) == 1

    async with world["maker"]() as db:
        await db.execute(text("DELETE FROM privacypa_organizations"))
        await db.commit()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "sbagliata")
    async with world["maker"]() as db:
        with pytest.raises(BackupError, match="non supera la verifica"):
            await world["service"](db).restore_backup(info["filename"])
    assert await _orgs(world) == 0                                                                # il database in uso non è stato sostituito
    async with world["maker"]() as db:
        assert (await world["service"](db).get_backup_logs(1))[0].operation == "restore"


@pytest.mark.asyncio
async def test_restore_documents_never_overwrites_and_rejects_path_traversal(world):
    info = await _create(world)
    docs = world["docs"]
    (docs / "TA" / "doc1.bin").unlink()
    async with world["maker"]() as db:
        service = world["service"](db)
        assert await service.restore_documents(info["documents_filename"]) == {"filename": info["documents_filename"], "restored": 1, "identical": 0, "conflicts": []}
        assert (docs / "TA" / "doc1.bin").read_bytes() == b"contenuto del documento"
        again = await service.restore_documents(info["documents_filename"])
        assert again["identical"] == 1 and again["restored"] == 0
        (docs / "TA" / "doc1.bin").write_bytes(b"modificato dopo il backup")
        conflict = await service.restore_documents(info["documents_filename"])
        assert conflict["conflicts"] == ["TA/doc1.bin"] and (docs / "TA" / "doc1.bin").read_bytes() == b"modificato dopo il backup"

        evil = world["backups"] / "documents_manual_20200101_000000.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../fuori.txt", "x")
            zf.writestr("manifest.json", json.dumps({"files": {"../fuori.txt": hashlib.sha256(b"x").hexdigest()}}))
        with pytest.raises(BackupError, match="non ammesso"):
            await service.restore_documents(evil.name)
    assert not (docs.parent / "fuori.txt").exists()


# ================================================================ copia esterna, retention, nomi

@pytest.mark.asyncio
async def test_offsite_copy_is_verified_and_its_failure_does_not_lose_the_backup(world):
    offsite = world["tmp"] / "offsite"
    async with world["maker"]() as db:
        info = await world["service"](db, offsite_dir=offsite).create_backup()
    assert info["offsite"] == {"ok": True, "error": None}
    for name in (info["filename"], info["documents_filename"]):
        assert (offsite / name).read_bytes() == (world["backups"] / name).read_bytes()
    assert not list(offsite.glob("*.part"))

    blocked = world["tmp"] / "non-una-cartella"
    blocked.write_text("sono un file")
    async with world["maker"]() as db:
        service = world["service"](db, offsite_dir=blocked)
        failed = await service.create_backup()
        assert failed["offsite"]["ok"] is False and (world["backups"] / failed["filename"]).is_file()       # il backup locale c'è comunque
        assert ("offsite", "error") in [(l.operation, l.status) for l in await service.get_backup_logs()]


@pytest.mark.asyncio
async def test_retention_removes_old_sets_including_documents_and_offsite_copies(world):
    offsite = world["tmp"] / "offsite"
    async with world["maker"]() as db:
        service = world["service"](db, offsite_dir=offsite)
        await service.update_config(retention_count=2)
        made = [await service.create_backup() for _ in range(3)]
    kept = sorted(b["filename"] for b in made)[1:]
    assert [b["filename"] for b in BackupService(None, backup_dir=world["backups"]).list_backups()] == sorted(kept, reverse=True)
    oldest = sorted(made, key=lambda b: b["filename"])[0]
    for folder in (world["backups"], offsite):
        assert not (folder / oldest["filename"]).exists() and not (folder / oldest["documents_filename"]).exists()
        assert len(list(folder.iterdir())) == 4


@pytest.mark.asyncio
async def test_backup_names_are_validated_and_delete_removes_the_whole_set(world):
    info = await _create(world)
    async with world["maker"]() as db:
        service = world["service"](db)
        for bad in ("../app.db", "backup_manual_20200101_000000.db/../x", "app.db", "backup_manual_1_2.db", ""):
            with pytest.raises(BackupError):
                await service.verify_backup(bad)
        with pytest.raises(FileNotFoundError):
            await service.verify_backup("backup_manual_20200101_000000.db")
        assert await service.delete_backup(info["filename"])
    assert list(world["backups"].iterdir()) == []


@pytest.mark.asyncio
async def test_only_sqlite_is_supported_and_it_says_so(world, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    async with world["maker"]() as db:
        with pytest.raises(BackupError, match="pg_dump"):
            await BackupService(db, backup_dir=world["backups"]).create_backup()
        assert (await BackupService(db, backup_dir=world["backups"]).get_backup_logs(1))[0].status == "error"


# ================================================================ pianificatore

@pytest.mark.asyncio
async def test_scheduled_run_creates_and_verifies_and_never_raises(world):
    verdict = await run_scheduled_backup(world["maker"], db_path=world["db_file"], backup_dir=world["backups"], documents_dir=world["docs"])
    assert verdict["ok"] is True and verdict["filename"].startswith("backup_scheduled_")
    async with world["maker"]() as db:
        assert [(l.operation, l.created_by) for l in await world["service"](db).get_backup_logs()] == [("verify", "system@scheduler"), ("backup", "system@scheduler")]
    missing = await run_scheduled_backup(world["maker"], db_path=world["tmp"] / "non-esiste.db", backup_dir=world["backups"], documents_dir=world["docs"])
    assert missing is None                                                                        # l'errore resta nel registro, il pianificatore sopravvive
    async with world["maker"]() as db:
        assert (await world["service"](db).get_backup_logs(1))[0].status == "error"


# ================================================================ API

@pytest_asyncio.fixture
async def api(world, monkeypatch):
    monkeypatch.setattr(BackupService, "BACKUP_DIR", world["backups"])
    monkeypatch.setattr(privacypa_document_service, "STORAGE_ROOT", world["docs"])
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{world['db_file'].as_posix()}")
    app = FastAPI()
    app.include_router(backup_router.router)

    async def override_db():
        async with world["maker"]() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    def who(request: Request) -> TokenPayload:
        return TokenPayload(json.loads(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = who
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


ADMIN = {"X-Test-User": json.dumps({"sub": "adm", "email": "adm@example.org", "app_admin_for": ["privacypa"]})}
USER = {"X-Test-User": json.dumps({"sub": "u", "email": "u@example.org", "roles": []})}


@pytest.mark.asyncio
async def test_api_is_app_admin_only_and_the_full_cycle_works(api, world):
    for method, path in (("GET", "/api/backup/list"), ("GET", "/api/backup/logs"), ("POST", "/api/backup/create"), ("GET", "/api/backup/config")):
        assert (await api.request(method, path, headers=USER)).status_code == 403
    assert (await api.post("/api/backup/verify", json={"filename": "x"}, headers=USER)).status_code == 403

    created = (await api.post("/api/backup/create", headers=ADMIN)).json()
    name = created["backup"]["filename"]
    assert created["success"] and created["backup"]["documents_filename"]
    assert [b["filename"] for b in (await api.get("/api/backup/list", headers=ADMIN)).json()] == [name]
    logs = await api.get("/api/backup/logs", headers=ADMIN)                                       # /logs non è più intercettata da /{filename}
    assert logs.status_code == 200 and logs.json()[0]["operation"] == "backup" and logs.json()[0]["created_by"] == "adm@example.org"
    config = (await api.get("/api/backup/config", headers=ADMIN)).json()
    assert config["encryption"] is False and config["offsite"] is False and config["last_backup_at"]
    assert (await api.put("/api/backup/config", json={"retention_count": 3}, headers=ADMIN)).json()["retention_count"] == 3
    assert (await api.put("/api/backup/config", json={"retention_count": 99}, headers=ADMIN)).status_code == 422

    verdict = (await api.post("/api/backup/verify", json={"filename": name}, headers=ADMIN)).json()
    assert verdict["ok"] is True and verdict["checks"]
    assert (await api.post("/api/backup/verify", json={"filename": "../etc/passwd"}, headers=ADMIN)).status_code == 400
    assert (await api.post("/api/backup/verify", json={"filename": "backup_manual_20200101_000000.db"}, headers=ADMIN)).status_code == 404

    download = await api.get(f"/api/backup/{name}", headers=ADMIN)
    assert download.status_code == 200 and download.content == (world["backups"] / name).read_bytes()
    assert (await api.get("/api/backup/..%2Fapp.db", headers=ADMIN)).status_code in (400, 404)

    async with world["maker"]() as db:
        await db.execute(text("DELETE FROM privacypa_organizations"))
        await db.commit()
    assert (await api.post("/api/backup/restore", json={"filename": name, "confirm": False}, headers=ADMIN)).status_code == 400
    restored = await api.post("/api/backup/restore", json={"filename": name, "confirm": True}, headers=ADMIN)
    assert restored.status_code == 200 and restored.json()["safety_backup"] != name
    assert await _orgs(world) == 1

    docs = await api.post("/api/backup/restore-documents", json={"filename": created["backup"]["documents_filename"], "confirm": True}, headers=ADMIN)
    assert docs.status_code == 200 and docs.json()["identical"] == 1
    assert (await api.delete(f"/api/backup/{name}", headers=ADMIN)).status_code == 200
    assert (await api.delete(f"/api/backup/{name}", headers=ADMIN)).status_code == 404
