import asyncio
import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# .env va caricato prima di qualsiasi import del backend: database.py e
# portal_jwt.py leggono DATABASE_URL e PRAMAIA_JWT_SECRET al momento dell'import.
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.db.database import engine  # noqa: E402
from backend.db.init_db import init_database  # noqa: E402
from backend.db.seed_privacypa import seed_privacypa_foundations  # noqa: E402
from backend.routers import example_router, settings_router, license_router, permissions_router, setup_router, backup_router, tenants_router  # noqa: E402
from backend.routers import privacypa_organization_router, privacypa_identity_router, privacypa_catalog_router  # noqa: E402
from backend.routers import privacypa_processing_router  # noqa: E402
from backend.routers import privacypa_asset_router, privacypa_party_router  # noqa: E402
from backend.routers import privacypa_document_router, privacypa_task_router, privacypa_ops_router  # noqa: E402
from backend.routers import privacypa_dpia_router, privacypa_transfer_router, privacypa_risk_router  # noqa: E402
from backend.services.license_service import LicenseService  # noqa: E402
from backend.services.privacypa_webhook_service import run_worker  # noqa: E402
from backend.services.backup_scheduler import start_backup_scheduler, stop_backup_scheduler  # noqa: E402
from backend.services.privacypa_findings_scheduler import start_findings_scheduler, stop_findings_scheduler  # noqa: E402
from backend.utils.logging import setup_logging, get_logger  # noqa: E402
from backend.middleware import LoggingMiddleware  # noqa: E402
from backend.middleware.security import RateLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware  # noqa: E402
from backend.routers import health_router, privacypa_import_router  # noqa: E402
from backend.routers import privacypa_approval_router, privacypa_policy_router  # noqa: E402
from backend.routers import privacypa_retention_router, privacypa_supply_router, privacypa_findings_router  # noqa: E402
from backend.routers import privacypa_outbox_router, privacypa_export_router  # noqa: E402
from backend.routers import privacypa_notice_router, privacypa_dataflow_router, privacypa_workflow_router  # noqa: E402
from backend.routers import privacypa_people_router, privacypa_dpo_router, privacypa_project_router, privacypa_annotation_router, privacypa_query_router, privacypa_webhook_router, privacypa_integration_router, privacypa_access_router  # noqa: E402
from backend.middleware.idempotency import IdempotencyMiddleware  # noqa: E402
from backend.db.database import AsyncSessionLocal  # noqa: E402
from backend.services.privacypa_document_service import DocumentService  # noqa: E402
from backend.services.privacypa_scanner import active_mode  # noqa: E402
from backend.api_errors import register_error_handlers  # noqa: E402

# Initialize logging FIRST
setup_logging()
logger = get_logger(__name__)

APP_NAME = os.getenv("APP_NAME", "PrivacyManager")
PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:3080")


async def _rescan_quarantined_documents():
    try:
        async with AsyncSessionLocal() as db:
            outcome = await DocumentService(db).rescan_quarantined()
            await db.commit()
        if outcome:
            logger.info(f"Documenti in quarantena rianalizzati ({active_mode()}): {outcome}")
    except Exception as e:
        logger.error(f"Rianalisi dei documenti in quarantena non riuscita: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{APP_NAME} starting...")
    await init_database()

    try:
        await seed_privacypa_foundations()
    except Exception as e:
        logger.error(f"PrivacyPA foundations seed failed: {e}")

    # Sync e verifica licenza all'avvio
    license_svc = LicenseService(db=None)
    try:
        logger.info("Syncing license from Portal...")
        result = await license_svc.verify_license(sync_from_portal=True)
        
        if result.valid:
            logger.info(
                f"✅ License valid: {result.license_id} (expires: {result.expires_at}, "
                f"sync: {result.sync_status})"
            )
        else:
            logger.warning(f"⚠️  License validation failed: {result.code} - {result.message}")
            logger.warning("App will start but some features may be blocked")
    except Exception as e:
        logger.error(f"❌ License check error: {e}")
        logger.warning("App will start but license validation is required")
    
    # Start backup scheduler
    try:
        logger.info("Starting backup scheduler...")
        await start_backup_scheduler()
    except Exception as e:
        logger.error(f"Failed to start backup scheduler: {e}")

    # Valutazione giornaliera delle regole (segnalazioni e webhook come authorization.expired_not_revoked)
    try:
        await start_findings_scheduler()
    except Exception as e:
        logger.error(f"Failed to start findings scheduler: {e}")
    
    # Consegna dei webhook (§70): ciclo in background, disattivabile con WEBHOOK_WORKER=false (allora si usa POST /webhooks/dispatch).
    webhook_stop, webhook_task = asyncio.Event(), None
    if os.getenv("WEBHOOK_WORKER", "true").strip().lower() not in ("0", "false", "no"):
        webhook_task = asyncio.create_task(run_worker(AsyncSessionLocal, webhook_stop, float(os.getenv("WEBHOOK_INTERVAL_SECONDS", "5"))))

    # Documenti caricati quando mancava lo scanner antimalware: si rianalizzano appena ce n'è uno
    rescan_task = None
    if active_mode() != "none":
        rescan_task = asyncio.create_task(_rescan_quarantined_documents())

    logger.info(f"{APP_NAME} ready")
    yield

    if rescan_task is not None and not rescan_task.done():
        rescan_task.cancel()

    webhook_stop.set()
    if webhook_task is not None:
        try:
            await asyncio.wait_for(webhook_task, timeout=15)
        except Exception as e:
            logger.error(f"Failed to stop webhook worker: {e}")
    
    # Stop backup scheduler
    try:
        logger.info("Stopping backup scheduler...")
        await stop_backup_scheduler()
    except Exception as e:
        logger.error(f"Failed to stop backup scheduler: {e}")

    try:
        await stop_findings_scheduler()
    except Exception as e:
        logger.error(f"Failed to stop findings scheduler: {e}")
    
    logger.info(f"{APP_NAME} shutting down")


# Swagger/OpenAPI esposti solo su richiesta esplicita (§89): default chiuso.
_docs_enabled = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"


def _read_app_version(default: str) -> str:
    """Legge version.txt (scritto da build-release.ps1 a ogni build — sempre
    sovrascritto, a differenza di .env che viene preservato tra un
    aggiornamento e l'altro). Assente in sviluppo: resta il default."""
    try:
        with open("version.txt", "r", encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


app = FastAPI(
    title=APP_NAME,
    description="Gestione Privacy e GDPR PramaIA",
    version=_read_app_version("1.0.0"),
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Ordine: l'ultimo aggiunto è il più esterno. Il request id deve esistere per tutti gli
# altri strati, e CORS deve avvolgere il rate limit perché anche i 429 siano leggibili dal browser.
app.add_middleware(IdempotencyMiddleware, session_factory=AsyncSessionLocal)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[PORTAL_URL, "http://localhost:3003"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)

# Setup router DEVE essere registrato per primo (accessibile anche senza config)
app.include_router(setup_router.router)
app.include_router(health_router.router)

app.include_router(example_router.router)
app.include_router(settings_router.router)
app.include_router(license_router.router)
app.include_router(permissions_router.router)
app.include_router(backup_router.router)
app.include_router(tenants_router.router)
app.include_router(privacypa_organization_router.router)
app.include_router(privacypa_identity_router.router)
app.include_router(privacypa_catalog_router.router)
app.include_router(privacypa_processing_router.router)
app.include_router(privacypa_asset_router.router)
app.include_router(privacypa_party_router.router)
app.include_router(privacypa_document_router.router)
app.include_router(privacypa_task_router.router)
app.include_router(privacypa_ops_router.router)
app.include_router(privacypa_dpia_router.router)
app.include_router(privacypa_transfer_router.router)
app.include_router(privacypa_risk_router.router)
app.include_router(privacypa_import_router.router)
app.include_router(privacypa_approval_router.router)
app.include_router(privacypa_policy_router.router)
app.include_router(privacypa_retention_router.router)
app.include_router(privacypa_supply_router.router)
app.include_router(privacypa_findings_router.router)
app.include_router(privacypa_outbox_router.router)
app.include_router(privacypa_export_router.router)
app.include_router(privacypa_notice_router.router)
app.include_router(privacypa_dataflow_router.router)
app.include_router(privacypa_workflow_router.router)
app.include_router(privacypa_people_router.router)
app.include_router(privacypa_dpo_router.router)
app.include_router(privacypa_project_router.router)
app.include_router(privacypa_annotation_router.router)
app.include_router(privacypa_query_router.router)
app.include_router(privacypa_webhook_router.router)
app.include_router(privacypa_integration_router.router)
app.include_router(privacypa_access_router.router)


@app.get("/api/config")
async def get_frontend_config():
    """Config pubblica per il frontend (PORTAL_URL dal .env, scritto
    dall'installer — non dal bundle React, che non può leggere l'ambiente
    del backend a tempo di build). PrivacyManager non ha una modalità di
    accesso locale (niente backend/auth/local_auth.py): solo portal_url."""
    return {
        "portal_url": os.getenv("PORTAL_URL", ""),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "PramaIA-PrivacyManager", "version": app.version}


# ----------------------------------------------------------------------------
# Frontend statico (build di produzione): attivo solo se la cartella esiste.
# In sviluppo il frontend gira separatamente (CRA dev server su :3003), quindi
# qui non c'è nulla e questo blocco resta inattivo. Nel pacchetto installer
# (vedi installer/) il build React viene incluso accanto all'eseguibile e
# questa route lo serve, con fallback a index.html per il routing lato client.
# Registrata per ultima: tutte le rotte API/health sopra hanno sempre la precedenza
# (Starlette instrada nell'ordine di registrazione, non per specificità).
# ----------------------------------------------------------------------------
def _resource_path(*parts: str) -> str:
    import sys

    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__ + "/.."))
    return os.path.join(base, *parts)


_FRONTEND_BUILD_DIR = _resource_path("frontend", "build")

if os.path.isdir(_FRONTEND_BUILD_DIR):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(_FRONTEND_BUILD_DIR, "static")),
        name="frontend-static-assets",
    )

    # OP08 (RICHIESTE_PLATFORM.md): realpath() + controllo di contenimento, altrimenti
    # "..%2f.env" (decodificato da uvicorn in "../.env") uscirebbe dalla cartella del
    # frontend e servirebbe il .env con PRAMAIA_JWT_SECRET senza autenticazione. "+ os.sep"
    # evita il falso positivo di una cartella sorella con lo stesso prefisso (es.
    # "build-evil" "contiene" startswith("build") senza esserlo).
    _FRONTEND_BUILD_ROOT = os.path.realpath(_FRONTEND_BUILD_DIR)

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("health/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.realpath(os.path.join(_FRONTEND_BUILD_ROOT, full_path))
        if (
            full_path
            and candidate.startswith(_FRONTEND_BUILD_ROOT + os.sep)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(
            os.path.join(_FRONTEND_BUILD_ROOT, "index.html"),
            headers={"Cache-Control": "no-cache"},
        )
