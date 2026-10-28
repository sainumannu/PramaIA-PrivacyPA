"""
PrivacyPA — Moduli di licenza (standard della piattaforma: `LICENZE_MODULI_STANDARD.md` dell'HelpDesk).

Il catalogo si espone su `GET /api/license/modules` per il Portal. Oggi l'unico modulo opzionale è `access_governance`
(API per le richieste di accesso dall'HelpDesk): con una licenza valida che non lo comprende le sue rotte rispondono
403 `MODULE_NOT_LICENSED`. Come prevede lo standard, una licenza assente o non verificabile e qualsiasi errore del
controllo non bloccano (tolleranza; la sola lettura dopo la tolleranza non è ancora applicata in PrivacyPA).
"""
import asyncio
import logging
import os
import time

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

UMBRELLA_MODULE = "privacypa"
ACCESS_GOVERNANCE = "access_governance"
WILDCARD = "*"
CACHE_SECONDS = 300

MODULE_CATALOG = [
    {"code": ACCESS_GOVERNANCE, "tab": None,
     "name": {"it": "Richieste di accesso (HelpDesk)", "en": "Access requests (HelpDesk)"},
     # La dipendenza dall'altra licenza è scritta qui perché chi emette le licenze vede una sola app alla volta
     "description": {"it": "Catalogo dei profili, valutazione delle richieste di accesso e decisione dei casi non standard per l'HelpDesk. "
                           "Funziona solo se anche la licenza dell'HelpDesk comprende il modulo access_requests.",
                     "en": "Profile catalog, access request assessment and non-standard case decisions for HelpDesk. "
                           "Works only if the HelpDesk license also includes the access_requests module."},
     "in_umbrella": False, "enforcement": "ui+api"},
]


def catalog() -> dict:
    return {
        "app_id": os.getenv("APP_ID", "PramaIA-PrivacyManager"),
        "app_version": os.getenv("APP_VERSION", "1.0.0"),
        "modules": MODULE_CATALOG,
        "umbrella_module": {"code": UMBRELLA_MODULE, "grants": [m["code"] for m in MODULE_CATALOG if m["in_umbrella"]]},
        "wildcard": WILDCARD,
        "always_available": [UMBRELLA_MODULE],
    }


_cache: dict = {"at": 0.0, "modules": None}
_lock = asyncio.Lock()


async def licensed_modules() -> list[str] | None:
    """Moduli della licenza valida; None se la licenza non è valida o verificabile (nessun blocco). Cache di 5 minuti."""
    async with _lock:
        if time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["modules"]
        from backend.services.license_service import LicenseService

        result = await LicenseService(db=None).verify_license()
        if result.valid:
            _cache["modules"] = list(result.allowed_modules or [])
        elif result.sync_status != "portal_unreachable":
            _cache["modules"] = None
        # Portal irraggiungibile per un momento: resta valido l'ultimo esito noto.
        _cache["at"] = time.monotonic()
        return _cache["modules"]


def require_licensed_module(module: str):
    """Dipendenza di router: 403 MODULE_NOT_LICENSED se una licenza valida non concede il modulo."""

    async def _check() -> None:
        try:
            modules = await licensed_modules()
        except Exception as e:  # lo standard: un errore del controllo di licenza non ferma l'app
            logger.error(f"Controllo del modulo di licenza {module} non riuscito: {e}")
            return
        if modules is None or WILDCARD in modules or module in modules:
            return
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "MODULE_NOT_LICENSED", "message": f"Il modulo di licenza «{module}» non è concesso"},
        )

    _check.license_module = module
    return _check
