import asyncio
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from backend.db.database import engine
from backend.db.init_db import init_database
from backend.db.seed_privacypa import seed_privacypa_foundations
from backend.routers import example_router, settings_router, license_router, permissions_router, setup_router, backup_router
from backend.routers import privacypa_organization_router, privacypa_identity_router, privacypa_catalog_router
from backend.routers import privacypa_processing_router
from backend.routers import privacypa_asset_router, privacypa_party_router
from backend.routers import privacypa_document_router, privacypa_task_router, privacypa_ops_router
from backend.routers import privacypa_dpia_router, privacypa_transfer_router, privacypa_risk_router
from backend.services.license_service import LicenseService
from backend.services.privacypa_webhook_service import run_worker
from backend.services.backup_scheduler import start_backup_scheduler, stop_backup_scheduler
from backend.utils.logging import setup_logging, get_logger
from backend.middleware import LoggingMiddleware
from backend.middleware.security import RateLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from backend.routers import health_router, privacypa_import_router
from backend.routers import privacypa_approval_router, privacypa_policy_router
from backend.routers import privacypa_retention_router, privacypa_supply_router, privacypa_findings_router
from backend.routers import privacypa_outbox_router, privacypa_export_router
from backend.routers import privacypa_notice_router, privacypa_dataflow_router, privacypa_workflow_router
from backend.routers import privacypa_people_router, privacypa_dpo_router, privacypa_project_router, privacypa_annotation_router, privacypa_query_router, privacypa_webhook_router
from backend.middleware.idempotency import IdempotencyMiddleware
from backend.db.database import AsyncSessionLocal
from backend.api_errors import register_error_handlers

load_dotenv()

# Initialize logging FIRST
setup_logging()
logger = get_logger(__name__)

APP_NAME = os.getenv("APP_NAME", "PrivacyManager")
PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:3080")


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
    
    # Consegna dei webhook (§70): ciclo in background, disattivabile con WEBHOOK_WORKER=false (allora si usa POST /webhooks/dispatch).
    webhook_stop, webhook_task = asyncio.Event(), None
    if os.getenv("WEBHOOK_WORKER", "true").strip().lower() not in ("0", "false", "no"):
        webhook_task = asyncio.create_task(run_worker(AsyncSessionLocal, webhook_stop, float(os.getenv("WEBHOOK_INTERVAL_SECONDS", "5"))))

    logger.info(f"{APP_NAME} ready")
    yield

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
    
    logger.info(f"{APP_NAME} shutting down")


# Swagger/OpenAPI esposti solo su richiesta esplicita (§89): default chiuso.
_docs_enabled = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"

app = FastAPI(
    title=APP_NAME,
    description="Gestione Privacy e GDPR PramaIA",
    version="1.0.0",
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


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "PramaIA-PrivacyManager"}
