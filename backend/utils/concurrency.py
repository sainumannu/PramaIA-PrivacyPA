"""
Concorrenza ottimistica (§54): ogni entità modificabile ha `row_version` (incrementato dal DB a
ogni UPDATE, vedi `version_id_col`) esposto come ETag. Le modifiche devono indicare la versione
che l'utente ha letto con `If-Match`; se nel frattempo il record è cambiato → 409, senza
sovrascrivere in silenzio.
"""
import re

from fastapi import Header, HTTPException, status

_IF_MATCH_RE = re.compile(r'^(?:W/)?"?(\d{1,12})"?$')


def etag_of(entity) -> str:
    return f'"{entity.row_version}"'


def require_if_match(if_match: str | None = Header(None, alias="If-Match")) -> int:
    """Dipendenza: estrae la versione da If-Match (`"3"`, `W/"3"` o `3`). 428 se assente o malformata."""
    match = _IF_MATCH_RE.match((if_match or "").strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail='Header If-Match obbligatorio con la versione letta (es. If-Match: "3")',
        )
    return int(match.group(1))


def assert_version(entity, version: int) -> None:
    """409 se il record è stato modificato dopo la lettura dell'utente."""
    if entity.row_version != version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Il record è stato modificato da un altro utente "
                f"(versione corrente {entity.row_version}, letta {version}). Ricaricare e riprovare."
            ),
            headers={"ETag": etag_of(entity)},
        )
