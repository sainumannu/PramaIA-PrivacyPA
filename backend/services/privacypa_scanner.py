"""
PrivacyPA — Scansione antimalware dei file caricati (§55.5, §148, NFR-017).

Modalità (env `DOCUMENT_SCAN_MODE`):
- `clamav`   : invia il file a un demone clamd (`CLAMAV_HOST`, `CLAMAV_PORT`, default 3310);
- `disabled` : solo sviluppo/test, marca CLEAN dichiarandolo nel dettaglio;
- non impostata: nessuno scanner disponibile, il file resta in QUARANTINED
  e non è scaricabile finché non viene ri-scansionato.
"""
import asyncio
import enum
import os
import struct


class ScanStatus(str, enum.Enum):
    QUARANTINED = "QUARANTINED"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SCAN_FAILED = "SCAN_FAILED"


_CHUNK = 64 * 1024
_TIMEOUT = 30.0


async def scan_bytes(content: bytes) -> tuple[ScanStatus, str]:
    mode = (os.getenv("DOCUMENT_SCAN_MODE") or "").strip().lower()
    if mode == "disabled":
        return ScanStatus.CLEAN, "scansione disabilitata (DOCUMENT_SCAN_MODE=disabled)"
    if mode == "clamav":
        return await _scan_clamav(content)
    return ScanStatus.QUARANTINED, "nessuno scanner antimalware configurato"


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
