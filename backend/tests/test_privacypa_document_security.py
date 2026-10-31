"""Sicurezza upload/download documenti (§31.2, §55.5, §116, §147-148, NFR-017)."""
import asyncio
import io
import struct
import zipfile

import pytest

from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.services import privacypa_scanner as scanner
from backend.services.privacypa_document_service import MAX_BYTES, DocumentService, sanitize_filename
from backend.tests.conftest import TA, _h

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def _plain_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("script.sh", "rm -rf /")
    return buf.getvalue()


async def _new_doc(c, who) -> str:
    r = await c.post("/api/privacypa/documents", json={"title": "Doc"}, headers=who)
    assert r.status_code == 201
    return r.json()["id"]


async def _upload(c, who, doc_id, name, content, declared="application/octet-stream"):
    return await c.post(
        f"/api/privacypa/documents/{doc_id}/versions",
        files={"file": (name, content, declared)},
        headers=who,
    )


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\x\\evil.pdf") == "evil.pdf"
    assert sanitize_filename("a\x00b\nc.pdf") == "abc.pdf"
    assert sanitize_filename("   ..  ") == "upload.bin"
    assert sanitize_filename(None) == "upload.bin"
    assert len(sanitize_filename("x" * 500 + ".pdf")) <= 200
    assert sanitize_filename("x" * 500 + ".pdf").endswith(".pdf")


@pytest.mark.asyncio
async def test_storage_key_is_random_and_client_name_never_touches_the_path(env, tmp_path):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)

    r = await _upload(c, who, doc_id, "../../../evil.txt", b"ciao")
    assert r.status_code == 200
    v = r.json()
    assert v["file_name"] == "evil.txt"

    stored = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].name == v["id"]
    assert stored[0].read_bytes() == b"ciao"
    assert "evil" not in str(stored[0])
    assert stored[0].resolve().is_relative_to((tmp_path / "docs").resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,content,declared",
    [
        ("a.pdf", b"MZ\x90\x00\x03\x00\x00\x00", "application/pdf"),          # eseguibile spacciato per PDF
        ("a.exe", PDF, "application/pdf"),                                       # estensione non ammessa
        ("a.txt", PDF, "text/plain"),                                            # contenuto PDF con estensione txt
        ("a.docx", _plain_zip(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.png", PDF, "image/png"),
        ("a.svg", b"<svg onload=alert(1)/>", "image/svg+xml"),
        ("a.html", b"<script>alert(1)</script>", "text/html"),
        ("noext", b"ciao", "text/plain"),
        ("a.txt", b"", "text/plain"),
        ("a.txt", b"bin\x00ary", "text/plain"),
    ],
)
async def test_rejects_disguised_or_unsupported_files(env, name, content, declared):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)
    r = await _upload(c, who, doc_id, name, content, declared)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_rejects_oversized_file(env):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)
    r = await _upload(c, who, doc_id, "big.txt", b"a" * (MAX_BYTES + 1))
    assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,content,mime",
    [
        ("a.pdf", PDF, "application/pdf"),
        ("a.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.png", PNG, "image/png"),
        ("dati.csv", b"a;b\n1;2\n", "text/csv"),
        ("nota.txt", "città è".encode("utf-8"), "text/plain"),
    ],
)
async def test_accepts_valid_files_and_stores_detected_mime(env, name, content, mime):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)
    # il tipo dichiarato dal client è ignorato
    r = await _upload(c, who, doc_id, name, content, declared="application/x-evil")
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"] == mime


@pytest.mark.asyncio
async def test_download_blocked_until_clean_and_rescan_requires_role(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    await env["grant"](TA, "op", R.OPERATOR)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)

    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "none")  # nessuno scanner
    v = (await _upload(c, who, doc_id, "a.txt", b"ciao")).json()
    assert v["scan_status"] == "QUARANTINED"
    r = await c.get(f"/api/privacypa/documents/{doc_id}/file", headers=who)
    assert r.status_code == 409 and "QUARANTINED" in r.text

    url = f"/api/privacypa/documents/{doc_id}/versions/{v['id']}/rescan"
    assert (await c.post(url, headers=_h(TA, "op"))).status_code == 403

    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "disabled")
    assert (await c.post(url, headers=who)).json()["scan_status"] == "CLEAN"
    r = await c.get(f"/api/privacypa/documents/{doc_id}/file", headers=who)
    assert r.status_code == 200 and r.content == b"ciao"
    assert "attachment" in r.headers["content-disposition"]


async def _fake_clamd(reply: bytes):
    async def handle(reader, writer):
        await reader.readexactly(len(b"zINSTREAM\0"))
        while True:
            (n,) = struct.unpack("!I", await reader.readexactly(4))
            if n == 0:
                break
            await reader.readexactly(n)
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_clamav_infected_file_is_quarantined_and_never_current(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)
    good = (await _upload(c, who, doc_id, "a.txt", b"pulito")).json()  # scanner disabled

    server, port = await _fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "clamav")
    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMAV_PORT", str(port))
    try:
        bad = (await _upload(c, who, doc_id, "b.txt", b"virus")).json()
    finally:
        server.close()
    assert bad["scan_status"] == "INFECTED" and "Eicar" in bad["scan_detail"]

    doc = (await c.get(f"/api/privacypa/documents/{doc_id}", headers=who)).json()
    assert doc["current_version_id"] == good["id"]
    assert (await c.get(f"/api/privacypa/documents/{doc_id}/file", headers=who)).content == b"pulito"


@pytest.mark.asyncio
async def test_clamav_clean_and_unreachable(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)

    server, port = await _fake_clamd(b"stream: OK\0")
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "clamav")
    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMAV_PORT", str(port))
    try:
        assert (await _upload(c, who, doc_id, "a.txt", b"ok")).json()["scan_status"] == "CLEAN"
    finally:
        server.close()
        await server.wait_closed()

    # scanner spento: il file è salvato ma non scaricabile
    v = (await _upload(c, who, doc_id, "b.txt", b"ok2")).json()
    assert v["scan_status"] == "SCAN_FAILED"
    assert (await c.get(f"/api/privacypa/documents/{doc_id}/file", headers=who)).status_code == 409


# ---------------------------------------------------------------- antivirus di Windows (AMSI simulato)

def _fake_amsi(monkeypatch, hr=0):
    """AMSI che segnala come malware ogni contenuto con la parola MALWARE; registra i nomi analizzati."""
    seen = []

    def scan(items):
        for name, data in items:
            seen.append(name)
            if hr:
                return hr, 0, name
            if data and b"MALWARE" in data:
                return 0, 32768, name
        return 0, 0, ""

    monkeypatch.setattr(scanner, "_amsi_scan", scan)
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "windows")
    return seen


def _docx_with(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:   # compresso come un vero .docx
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr(name, data)
    return buf.getvalue()


def test_auto_mode_follows_registered_antivirus(monkeypatch):
    monkeypatch.delenv("DOCUMENT_SCAN_MODE", raising=False)
    monkeypatch.setattr(scanner, "amsi_providers", lambda: [])
    assert scanner.active_mode() == "none"
    monkeypatch.setattr(scanner, "amsi_providers", lambda: ["{FB904E4E-D2C7-4C8D-8492-B620BB9896B1}"])
    assert scanner.active_mode() == "windows"
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "none")
    assert scanner.active_mode() == "none"
    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "sconosciuta")
    assert scanner.active_mode() == "none"


@pytest.mark.asyncio
async def test_windows_scans_document_and_archive_members(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    doc_id = await _new_doc(c, who)
    seen = _fake_amsi(monkeypatch)

    v = (await _upload(c, who, doc_id, "a.pdf", PDF)).json()
    assert v["scan_status"] == "CLEAN" and "nessuna minaccia" in v["scan_detail"]
    assert (await c.get(f"/api/privacypa/documents/{doc_id}/file", headers=who)).content == PDF

    # il .docx è un archivio: si analizza anche ogni file contenuto, con il suo nome
    seen.clear()
    bad = (await _upload(c, who, doc_id, "b.docx", _docx_with("word/vbaProject.bin", b"MALWARE" * 50))).json()
    assert bad["scan_status"] == "INFECTED" and "word/vbaProject.bin" in bad["scan_detail"]
    assert seen[0] == "documento" and "word/document.xml" in seen
    doc = (await c.get(f"/api/privacypa/documents/{doc_id}", headers=who)).json()
    assert doc["current_version_id"] == v["id"]

    _fake_amsi(monkeypatch, hr=0x80070005)
    v = (await _upload(c, who, doc_id, "c.pdf", PDF)).json()
    assert v["scan_status"] == "SCAN_FAILED" and "0x80070005" in v["scan_detail"]


def test_windows_realtime_protection_blocking_the_file_means_infected(monkeypatch):
    _fake_amsi(monkeypatch)
    real_open = open

    def blocked(path, mode="r", *args, **kwargs):
        if "privacypa-scan-" in str(path) and "r" in mode:
            raise OSError(22, "Invalid argument")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(scanner, "open", blocked, raising=False)
    status, detail = scanner._scan_windows(PDF)
    assert status == scanner.ScanStatus.INFECTED and "tempo reale" in detail


def test_windows_unreadable_archive_is_not_clean(monkeypatch):
    _fake_amsi(monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "ciao")
    data = buf.getvalue().replace(b"ciao", b"ciaX")   # CRC errato
    status, detail = scanner._scan_windows(data)
    assert status == scanner.ScanStatus.SCAN_FAILED and "archivio" in detail


@pytest.mark.asyncio
async def test_quarantined_documents_are_rescanned_when_a_scanner_appears(env, monkeypatch):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    who = _h(TA, "mgr")
    good_doc, bad_doc = await _new_doc(c, who), await _new_doc(c, who)
    clean = (await _upload(c, who, bad_doc, "v1.pdf", PDF)).json()        # scanner disabled: CLEAN

    monkeypatch.setenv("DOCUMENT_SCAN_MODE", "none")
    waiting = (await _upload(c, who, good_doc, "a.pdf", PDF)).json()
    infected = (await _upload(c, who, bad_doc, "v2.txt", b"MALWARE")).json()
    assert waiting["scan_status"] == infected["scan_status"] == "QUARANTINED"
    assert (await c.get(f"/api/privacypa/documents/{good_doc}/file", headers=who)).status_code == 409

    _fake_amsi(monkeypatch)
    async with env["maker"]() as db:
        outcome = await DocumentService(db).rescan_quarantined()
        await db.commit()
    assert outcome == {"CLEAN": 1, "INFECTED": 1}

    assert (await c.get(f"/api/privacypa/documents/{good_doc}/file", headers=who)).content == PDF
    # la versione risultata infetta non resta corrente: torna quella pulita
    doc = (await c.get(f"/api/privacypa/documents/{bad_doc}", headers=who)).json()
    assert doc["current_version_id"] == clean["id"]
