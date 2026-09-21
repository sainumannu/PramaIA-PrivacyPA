"""Probe di liveness/readiness (§97). Nessun dato diagnostico nella risposta."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.utils.logging import get_logger

router = APIRouter(tags=["Health"])
logger = get_logger(__name__)


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # il dettaglio resta nel log tecnico (§162)
        logger.error(f"Readiness check fallito: {type(exc).__name__}: {exc}")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
