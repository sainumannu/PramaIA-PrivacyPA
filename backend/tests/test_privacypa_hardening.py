"""Hardening trasversale: security header, request id, rate limit, health probe (§55.6, §95, §97, §132)."""
import os
import re

import httpx
import pytest
from fastapi import Depends, FastAPI

from backend.db.database import get_db
from backend.middleware.security import RateLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from backend.models.privacypa_audit import AuditLog
from backend.routers import health_router
from backend.services.privacypa_audit_service import AuditService
from backend.utils.request_context import get_request_id


def _app(rate=None, setup_rate=None, docs=False) -> FastAPI:
    app = FastAPI(docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None)

    @app.get("/api/ping")
    async def ping():
        return {"request_id": get_request_id()}

    @app.get("/api/health")
    async def api_health():
        return {"status": "ok"}

    @app.get("/api/setup/check")
    async def setup_check():
        return {"ok": True}

    app.include_router(health_router.router)
    app.add_middleware(RateLimitMiddleware, default_per_minute=rate, setup_per_minute=setup_rate)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


def _client(app, **kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, **kw), base_url="http://test")


@pytest.mark.asyncio
async def test_security_headers_and_hsts_only_over_https():
    async with _client(_app()) as c:
        r = await c.get("/api/ping")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
        assert "permissions-policy" in r.headers
        assert "strict-transport-security" not in r.headers

        proxied = await c.get("/api/ping", headers={"X-Forwarded-Proto": "https"})
        assert "max-age=" in proxied.headers["strict-transport-security"]


@pytest.mark.asyncio
async def test_swagger_ui_is_not_blocked_by_the_api_csp():
    async with _client(_app(docs=True)) as c:
        r = await c.get("/docs")
        assert r.status_code == 200
        assert "content-security-policy" not in r.headers


@pytest.mark.asyncio
async def test_request_id_generated_echoed_and_sanitized():
    async with _client(_app()) as c:
        generated = await c.get("/api/ping")
        rid = generated.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", rid)
        assert generated.json()["request_id"] == rid

        echoed = await c.get("/api/ping", headers={"X-Request-ID": "trace-abc_123.456"})
        assert echoed.headers["x-request-id"] == "trace-abc_123.456"
        assert echoed.json()["request_id"] == "trace-abc_123.456"

        for hostile in ("short", "has space in it 12345", "x" * 100, "inj<script>alert(1)"):
            r = await c.get("/api/ping", headers={"X-Request-ID": hostile})
            assert r.headers["x-request-id"] != hostile
            assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


@pytest.mark.asyncio
async def test_request_id_is_stored_in_audit_trail(env):
    app = _app()

    @app.post("/api/do")
    async def do():
        async with env["maker"]() as db:
            await AuditService(db).log("tenant-a", "thing", "create", entity_id="e1")
            await db.commit()
        return {}

    async with _client(app) as c:
        r = await c.post("/api/do")
    async with env["maker"]() as db:
        rows = (await db.execute(AuditLog.__table__.select().where(AuditLog.entity_type == "thing"))).all()
    assert [row.request_id for row in rows] == [r.headers["x-request-id"]]


@pytest.mark.asyncio
async def test_rate_limit_blocks_and_exempts_health():
    async with _client(_app(rate=3, setup_rate=2)) as c:
        assert [(await c.get("/api/ping")).status_code for _ in range(3)] == [200, 200, 200]
        blocked = await c.get("/api/ping")
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) >= 1
        assert "x-request-id" in blocked.headers and "x-content-type-options" in blocked.headers
        # health e probe non sono limitati
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/health/live")).status_code == 200
        # /api/setup ha un bucket separato e più stretto
        assert [(await c.get("/api/setup/check")).status_code for _ in range(3)] == [200, 200, 429]


@pytest.mark.asyncio
async def test_rate_limit_is_per_client_and_proxy_header_needs_opt_in(monkeypatch):
    # senza TRUST_PROXY l'header X-Forwarded-For è ignorato: non si aggira il limite falsificandolo
    async with _client(_app(rate=2)) as c:
        codes = [(await c.get("/api/ping", headers={"X-Forwarded-For": f"10.0.0.{i}"})).status_code for i in range(3)]
        assert codes == [200, 200, 429]

    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _client(_app(rate=2)) as c:
        codes = [(await c.get("/api/ping", headers={"X-Forwarded-For": f"10.0.0.{i}"})).status_code for i in range(4)]
        assert codes == [200, 200, 200, 200]


@pytest.mark.asyncio
async def test_health_probes_do_not_leak_details():
    app = _app()
    async with _client(app) as c:
        assert (await c.get("/health/live")).json() == {"status": "ok"}

        class Broken:
            async def execute(self, *_):
                raise RuntimeError("password=hunter2 host=db.internal")

        async def broken_db():
            yield Broken()

        app.dependency_overrides[get_db] = broken_db
        r = await c.get("/health/ready")
        assert r.status_code == 503
        assert r.json() == {"status": "unavailable"}
        assert "hunter2" not in r.text


@pytest.mark.asyncio
async def test_health_ready_ok_with_working_db(env):
    app = _app()

    async def db():
        async with env["maker"]() as s:
            yield s

    app.dependency_overrides[get_db] = db
    async with _client(app) as c:
        assert (await c.get("/health/ready")).json() == {"status": "ready"}


def test_main_app_wiring():
    from backend.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/health/live", "/health/ready", "/api/health"} <= paths
    classes = {m.cls for m in app.user_middleware}
    assert {RateLimitMiddleware, SecurityHeadersMiddleware, RequestContextMiddleware} <= classes
    if os.getenv("ENABLE_API_DOCS", "false").lower() != "true":
        assert app.openapi_url is None and app.docs_url is None
