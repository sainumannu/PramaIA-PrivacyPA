"""Gestori di errore comuni dell'API (§53, §54, §162): nessun dettaglio tecnico all'utente."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from backend.services.privacypa_refs import InvalidReference
from backend.utils.request_context import get_request_id


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StaleDataError)
    async def stale_data(_: Request, __: StaleDataError):
        # Due richieste hanno modificato lo stesso record: vince la prima, la seconda riceve 409.
        return JSONResponse(
            {
                "detail": "Il record è stato modificato da un altro utente. Ricaricare e riprovare.",
                "traceId": get_request_id(),
            },
            status_code=409,
        )

    @app.exception_handler(InvalidReference)
    async def invalid_reference(_: Request, exc: InvalidReference):
        # Un riferimento a un elemento inesistente o di un altro ente (§141): sempre «non trovato», senza altri dettagli.
        return JSONResponse({"detail": str(exc), "traceId": get_request_id()}, status_code=400)
