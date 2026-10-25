"""
Riallineamento del JWT secret quando il Portal lo cambia (portal_secret_sync.py):
l'app se lo riprende dal Portal con le credenziali di servizio già nel .env, senza che
si debba entrare nelle sue impostazioni (impossibile, con il segreto sbagliato).
"""
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException

from backend.auth import portal_jwt
from backend.routers import setup_router
from backend.services import portal_secret_sync

OLD_SECRET = "vecchio-segreto-" + "o" * 40
NEW_SECRET = "nuovo-segreto-" + "n" * 40
CLIENT_SECRET = "c" * 43
PORTAL_URL = "http://portal.local:8091"
DATABASE_URL = "sqlite:///./dati-reali.db"


@pytest.fixture
def configured_app(monkeypatch, tmp_path):
    """App già configurata e in uso, con .env nella cartella di lavoro (tmp)."""
    monkeypatch.chdir(tmp_path)
    env = {
        "PRAMAIA_JWT_SECRET": OLD_SECRET,
        "PORTAL_URL": PORTAL_URL,
        "PORTAL_API_URL": PORTAL_URL,
        "DATABASE_URL": DATABASE_URL,
        "APP_ID": "PramaIA-PrivacyManager",
        "APP_CLIENT_SECRET": CLIENT_SECRET,
    }
    # setenv anche per i valori che save_to_env riscrive in os.environ: a fine test
    # monkeypatch ripristina lo stato precedente.
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    Path(".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    monkeypatch.setattr(portal_secret_sync, "_last_attempt", None)
    # Alcune app rileggono il .env con load_dotenv in is_app_configured(): nei test
    # troverebbe quello vero del progetto, non quello in tmp.
    if hasattr(setup_router, "load_dotenv"):
        monkeypatch.setattr(setup_router, "load_dotenv", lambda *a, **kw: None)
    return tmp_path


def _portal_returns(monkeypatch, response: httpx.Response, calls: list | None = None):
    def fake_get(url, headers=None, timeout=None):
        if calls is not None:
            calls.append((url, headers))
        return response
    monkeypatch.setattr(portal_secret_sync.httpx, "get", fake_get)


def _env_file() -> str:
    return Path(".env").read_text(encoding="utf-8")


# --- Servizio ---

def test_resync_takes_new_secret_from_portal(configured_app, monkeypatch):
    calls = []
    _portal_returns(monkeypatch, httpx.Response(200, json={"jwt_secret": NEW_SECRET}), calls)

    assert portal_secret_sync.resync_jwt_secret() == {"changed": True}

    assert calls == [(f"{PORTAL_URL}/api/platform/jwt-secret",
                      {"X-App-ID": "PramaIA-PrivacyManager", "X-App-Secret": CLIENT_SECRET})]
    assert portal_jwt.get_jwt_secret() == NEW_SECRET  # attivo subito, senza riavvio
    assert f"PRAMAIA_JWT_SECRET={NEW_SECRET}" in _env_file()
    assert f"DATABASE_URL={DATABASE_URL}" in _env_file()


def test_resync_already_aligned_does_not_rewrite(configured_app, monkeypatch):
    _portal_returns(monkeypatch, httpx.Response(200, json={"jwt_secret": OLD_SECRET}))
    before = _env_file()
    assert portal_secret_sync.resync_jwt_secret() == {"changed": False}
    assert _env_file() == before


def test_resync_without_client_secret_explains_what_to_do(configured_app, monkeypatch):
    monkeypatch.delenv("APP_CLIENT_SECRET")
    with pytest.raises(portal_secret_sync.ResyncError) as exc:
        portal_secret_sync.resync_jwt_secret()
    assert exc.value.status_code == 409
    assert exc.value.code == "no_credentials"


def test_resync_rejected_by_portal(configured_app, monkeypatch):
    _portal_returns(monkeypatch, httpx.Response(401, json={"detail": "Invalid app secret"}))
    with pytest.raises(portal_secret_sync.ResyncError) as exc:
        portal_secret_sync.resync_jwt_secret()
    assert exc.value.code == "portal_rejected"
    assert portal_jwt.get_jwt_secret() == OLD_SECRET


def test_resync_portal_unreachable(configured_app, monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(portal_secret_sync.httpx, "get", fail)
    with pytest.raises(portal_secret_sync.ResyncError) as exc:
        portal_secret_sync.resync_jwt_secret()
    assert exc.value.status_code == 503


def test_resync_is_rate_limited(configured_app, monkeypatch):
    _portal_returns(monkeypatch, httpx.Response(200, json={"jwt_secret": OLD_SECRET}))
    portal_secret_sync.resync_jwt_secret()
    with pytest.raises(portal_secret_sync.ResyncError) as exc:
        portal_secret_sync.resync_jwt_secret()
    assert exc.value.status_code == 429


# --- Endpoint ---

def _client(remote_host: str = "127.0.0.1") -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(setup_router.router)
    transport = httpx.ASGITransport(app=app, client=(remote_host, 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_resync_endpoint_answers_remote_callers_without_login(configured_app, monkeypatch):
    """Il Portal chiama da un'altra macchina e senza token (con il segreto sbagliato non
    ce n'è uno valido): l'endpoint deve rispondere comunque."""
    _portal_returns(monkeypatch, httpx.Response(200, json={"jwt_secret": NEW_SECRET}))
    async with _client("192.168.1.94") as c:
        r = await c.post("/api/setup/resync")
    assert r.status_code == 200
    assert r.json()["changed"] is True
    assert portal_jwt.get_jwt_secret() == NEW_SECRET


@pytest.mark.asyncio
async def test_resync_endpoint_ignores_caller_supplied_values(configured_app, monkeypatch):
    calls = []
    _portal_returns(monkeypatch, httpx.Response(200, json={"jwt_secret": NEW_SECRET}), calls)
    async with _client("192.168.1.94") as c:
        await c.post("/api/setup/resync", json={"portal_url": "http://evil", "jwt_secret": "x" * 64})
    assert calls[0][0].startswith(PORTAL_URL)
    assert portal_jwt.get_jwt_secret() == NEW_SECRET


@pytest.mark.asyncio
async def test_resync_endpoint_reports_errors(configured_app, monkeypatch):
    monkeypatch.delenv("APP_CLIENT_SECRET")
    async with _client() as c:
        r = await c.post("/api/setup/resync")
    assert r.status_code == 409
    assert "APP_CLIENT_SECRET" in r.json()["detail"]


@pytest.mark.asyncio
async def test_check_reports_service_credentials(configured_app):
    async with _client() as c:
        assert (await c.get("/api/setup/check")).json()["has_service_credentials"] is True


def _mock_portal_async(monkeypatch, jwt_secret: str, calls: list):
    original = httpx.AsyncClient

    def handler(request):
        calls.append(dict(request.headers))
        return httpx.Response(200, json={"jwt_secret": jwt_secret, "version": "1.0.0"})

    monkeypatch.setattr(setup_router.httpx, "AsyncClient",
                        lambda *a, **kw: original(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_fetch_from_portal_reconfigures_only_secrets_when_configured(configured_app, monkeypatch):
    """Se è cambiato anche il secret API: dal server si inserisce quello nuovo e l'app si
    riprende il JWT secret. DATABASE_URL e APP_ID restano quelli in uso."""
    calls = []
    new_client_secret = "z" * 43
    async with _client() as c:
        _mock_portal_async(monkeypatch, NEW_SECRET, calls)
        r = await c.post("/api/setup/fetch-from-portal", json={
            "app_id": "altra-app", "app_secret": new_client_secret,
            "portal_url": "http://evil", "database_url": "sqlite:///./vuoto.db",
        })
    assert r.status_code == 200, r.text
    assert calls[0]["x-app-id"] == "PramaIA-PrivacyManager"
    env = _env_file()
    assert f"PRAMAIA_JWT_SECRET={NEW_SECRET}" in env
    assert f"APP_CLIENT_SECRET={new_client_secret}" in env
    assert f"DATABASE_URL={DATABASE_URL}" in env and "vuoto.db" not in env
    assert "APP_ID=PramaIA-PrivacyManager" in env and "altra-app" not in env
    assert "evil" not in env


@pytest.mark.asyncio
async def test_fetch_from_portal_still_localhost_only(configured_app):
    async with _client("192.168.1.50") as c:
        r = await c.post("/api/setup/fetch-from-portal", json={
            "app_secret": "z" * 43, "portal_url": PORTAL_URL,
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_manual_secret_refused_once_configured(configured_app):
    async with _client() as c:
        r = await c.post("/api/setup/initialize", json={
            "jwt_secret": "Kq8" * 20, "portal_url": PORTAL_URL,
        })
    assert r.status_code == 403
    assert portal_jwt.get_jwt_secret() == OLD_SECRET


# --- Riallineamento automatico su firma non valida ---

def test_invalid_signature_triggers_background_resync(configured_app, monkeypatch):
    triggered = []
    monkeypatch.setattr(portal_secret_sync, "trigger_background_resync", lambda: triggered.append(True))
    token = jwt.encode({"sub": "u", "iss": "pramaia-portal", "exp": 9999999999}, NEW_SECRET, algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        portal_jwt.decode_portal_token(token)
    assert exc.value.status_code == 401
    assert triggered == [True]


def test_expired_token_does_not_trigger_resync(configured_app, monkeypatch):
    triggered = []
    monkeypatch.setattr(portal_secret_sync, "trigger_background_resync", lambda: triggered.append(True))
    token = jwt.encode({"sub": "u", "iss": "pramaia-portal", "exp": 1}, OLD_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        portal_jwt.decode_portal_token(token)
    assert triggered == []


def test_background_trigger_skipped_without_credentials(configured_app, monkeypatch):
    started = []
    monkeypatch.delenv("APP_CLIENT_SECRET")
    monkeypatch.setattr(portal_secret_sync.threading, "Thread", lambda *a, **kw: started.append(True))
    portal_secret_sync.trigger_background_resync()
    assert started == []
