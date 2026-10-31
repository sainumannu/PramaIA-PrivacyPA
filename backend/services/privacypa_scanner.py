"""
PrivacyPA — Scansione antimalware dei file caricati (§55.5, §148, NFR-017).

Modalità (env `DOCUMENT_SCAN_MODE`):
- `windows`  : l'antivirus attivo su Windows (Defender, Avast...), con due controlli: il contenuto passa
               per AMSI, l'interfaccia a cui l'antivirus si registra (anche ogni file dentro gli archivi
               zip, .docx, .xlsx, .odt), poi il file viene scritto su disco e riletto, così interviene
               anche la protezione in tempo reale;
- `clamav`   : invia il file a un demone clamd (`CLAMAV_HOST`, `CLAMAV_PORT`, default 3310);
- `disabled` : solo sviluppo/test, marca CLEAN dichiarandolo nel dettaglio;
- `none`     : nessuno scanner, il file resta in QUARANTINED e non è scaricabile finché non viene ri-scansionato;
- non impostata: `windows` se un antivirus è registrato in AMSI, altrimenti `none`.
"""
import asyncio
import enum
import io
import os
import struct
import tempfile
import zipfile


class ScanStatus(str, enum.Enum):
    QUARANTINED = "QUARANTINED"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SCAN_FAILED = "SCAN_FAILED"


_CHUNK = 64 * 1024
_TIMEOUT = 30.0
_MODES = ("windows", "clamav", "disabled", "none")


def amsi_providers() -> list[str]:
    """Antivirus registrati in AMSI (CLSID dei provider); vuoto fuori da Windows."""
    if os.name != "nt":
        return []
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\AMSI\Providers") as key:
            return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError:
        return []


def active_mode() -> str:
    mode = (os.getenv("DOCUMENT_SCAN_MODE") or "").strip().lower()
    if mode:
        return mode if mode in _MODES else "none"
    return "windows" if amsi_providers() else "none"


async def scan_bytes(content: bytes) -> tuple[ScanStatus, str]:
    mode = active_mode()
    if mode == "disabled":
        return ScanStatus.CLEAN, "scansione disabilitata (DOCUMENT_SCAN_MODE=disabled)"
    if mode == "clamav":
        return await _scan_clamav(content)
    if mode == "windows":
        return await asyncio.to_thread(_scan_windows, content)
    return ScanStatus.QUARANTINED, "nessuno scanner antimalware configurato"


# ---------------------------------------------------------------- antivirus di Windows

_AMSI_DETECTED = 32768                      # AMSI_RESULT_DETECTED: da qui in su è malware
_AMSI_BLOCKED_BY_ADMIN = range(16384, 20480)


def _amsi_scan(items: list[tuple[str, bytes]]) -> tuple[int, int, str]:
    """Scansione con l'antivirus registrato in AMSI, in una sola sessione: (HRESULT, AMSI_RESULT, nome)
    della prima voce che non risulta pulita, (0, 0, "") se lo sono tutte."""
    import ctypes
    from ctypes import wintypes
    amsi = ctypes.WinDLL("amsi")
    handle_p = ctypes.POINTER(wintypes.HANDLE)
    amsi.AmsiInitialize.argtypes = [wintypes.LPCWSTR, handle_p]
    amsi.AmsiOpenSession.argtypes = [wintypes.HANDLE, handle_p]
    amsi.AmsiScanBuffer.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG, wintypes.LPCWSTR,
                                    wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
    amsi.AmsiCloseSession.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    amsi.AmsiUninitialize.argtypes = [wintypes.HANDLE]
    for fn in (amsi.AmsiInitialize, amsi.AmsiOpenSession, amsi.AmsiScanBuffer):
        fn.restype = ctypes.c_long
    context, session = wintypes.HANDLE(), wintypes.HANDLE()
    hr = amsi.AmsiInitialize("PrivacyManager", ctypes.byref(context))
    if hr:
        return hr, 0, ""
    try:
        hr = amsi.AmsiOpenSession(context, ctypes.byref(session))
        if hr:
            return hr, 0, ""
        try:
            for name, data in items:
                if not data:
                    continue
                result = ctypes.c_int(0)
                buffer = ctypes.create_string_buffer(data, len(data))
                hr = amsi.AmsiScanBuffer(context, buffer, len(data), name, session, ctypes.byref(result))
                if hr or result.value >= _AMSI_BLOCKED_BY_ADMIN.start:
                    return hr, result.value, name
        finally:
            amsi.AmsiCloseSession(context, session)
    finally:
        amsi.AmsiUninitialize(context)
    return 0, 0, ""


def _realtime_check(content: bytes) -> tuple[ScanStatus, str] | None:
    """Scrive il file e lo rilegge: se la protezione in tempo reale lo blocca o lo toglie, è infetto."""
    fd, path = tempfile.mkstemp(prefix="privacypa-scan-", suffix=".bin")
    try:
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
        except OSError as exc:
            return ScanStatus.SCAN_FAILED, f"antivirus di Windows: file temporaneo non scrivibile ({type(exc).__name__})"
        try:
            with open(path, "rb") as fh:
                intact = fh.read() == content
        except OSError:
            intact = False
        if not intact:
            return ScanStatus.INFECTED, "antivirus di Windows: file bloccato dalla protezione in tempo reale"
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_ARCHIVE_MAX_ENTRIES = 2000
_ARCHIVE_MAX_BYTES = 200 * 1024 * 1024     # contro gli zip «bomba»


def _archive_members(content: bytes) -> list[tuple[str, bytes]] | str:
    """File contenuti in un archivio zip (anche .docx, .xlsx, .odt), un livello; un messaggio se non si può aprire."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = [i for i in archive.infolist() if not i.is_dir()]
            if len(entries) > _ARCHIVE_MAX_ENTRIES or sum(i.file_size for i in entries) > _ARCHIVE_MAX_BYTES:
                return "archivio troppo grande da analizzare"
            return [(i.filename, archive.read(i)) for i in entries]
    except RuntimeError:
        return "archivio protetto da password"
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError):
        return "archivio non leggibile"


def _scan_windows(content: bytes) -> tuple[ScanStatus, str]:
    members = _archive_members(content)
    if isinstance(members, str):
        return ScanStatus.SCAN_FAILED, f"antivirus di Windows: {members}"
    try:
        hr, result, name = _amsi_scan([("documento", content)] + members)
    except OSError as exc:
        return ScanStatus.SCAN_FAILED, f"antivirus di Windows non disponibile ({type(exc).__name__})"
    if hr:
        return ScanStatus.SCAN_FAILED, f"antivirus di Windows: scansione non riuscita (0x{hr & 0xFFFFFFFF:08X})"
    if result >= _AMSI_DETECTED:
        where = "" if name == "documento" else f" in {name}"
        return ScanStatus.INFECTED, f"antivirus di Windows: minaccia rilevata{where}"[:255]
    if result in _AMSI_BLOCKED_BY_ADMIN:
        return ScanStatus.SCAN_FAILED, "antivirus di Windows: contenuto bloccato dai criteri dell'amministratore"
    return _realtime_check(content) or (ScanStatus.CLEAN, "antivirus di Windows: nessuna minaccia rilevata")


async def _scan_clamav(content: bytes) -> tuple[ScanStatus, str]:
    host = os.getenv("CLAMAV_HOST", "localhost")
    port = int(os.getenv("CLAMAV_PORT", "3310"))
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), _TIMEOUT)
        try:
            writer.write(b"zINSTREAM\0")
            for i in range(0, len(content), _CHUNK):
                chunk = content[i:i + _CHUNK]
                writer.write(struct.pack("!I", len(chunk)) + chunk)
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            reply = (await asyncio.wait_for(reader.read(1024), _TIMEOUT)).rstrip(b"\0\n").decode("utf-8", "replace")
        finally:
            writer.close()
    except (OSError, asyncio.TimeoutError) as exc:
        return ScanStatus.SCAN_FAILED, f"scanner non raggiungibile: {type(exc).__name__}"
    if reply.endswith("OK"):
        return ScanStatus.CLEAN, "clamav: nessuna minaccia rilevata"
    if reply.endswith("FOUND"):
        return ScanStatus.INFECTED, f"clamav: {reply.split(':', 1)[-1].strip()}"[:255]
    return ScanStatus.SCAN_FAILED, f"risposta scanner inattesa: {reply}"[:255]
