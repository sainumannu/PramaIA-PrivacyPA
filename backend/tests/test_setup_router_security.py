"""
OP01 (RICHIESTE_PLATFORM.md): gli endpoint che scrivono .env non devono rispondere a chi non
chiama dalla macchina stessa, e fetch-from-portal non deve contattare un portal_url preso dal
corpo della richiesta se uno e' gia' configurato.

A differenza delle altre app della piattaforma, conftest.py di PramaIA-PrivacyManager non
espone fixture `client`/`db_ready` basate su backend.main.app (la fixture `env` costruisce
una propria FastAPI() minimale con solo i router privacypa_*, senza setup_router): questi
test quindi importano `backend.main.app` ed espongono il proprio client ASGI direttamente,
sullo stesso modello di test_privacypa_hardening.py / test_privacypa_isolation_matrix.py
(che fanno già `from backend.main import app` senza fixture dedicate).
"""
import httpx
import pytest

API = "/api/setup"
REMOTE_CLIENT = ("203.0.113.5", 12345)


async def _remote_client(app):
    transport = httpx.ASGITransport(app=app, client=REMOTE_CLIENT)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _local_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_initialize_rejects_remote_caller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from backend.main import app

    async with await _remote_client(app) as remote:
        response = await remote.post(f"{API}/initialize", json={
            "jwt_secret": "x" * 48,
            "portal_url": "http://attacker.example.com",
        })
    assert response.status_code == 403
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_generate_secret_rejects_remote_caller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from backend.main import app

    async with await _remote_client(app) as remote:
        response = await remote.post(f"{API}/generate-secret")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_fetch_from_portal_rejects_remote_caller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from backend.main import app

    async with await _remote_client(app) as remote:
        response = await remote.post(f"{API}/fetch-from-portal", json={
            "app_id": "PramaIA-PrivacyManager",
            "app_secret": "x" * 32,
            "portal_url": "http://attacker.example.com",
        })
    assert response.status_code == 403


class _PortalResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_fetch_from_portal_ignores_body_url_when_already_configured(tmp_path, monkeypatch):
    """Un .env esistente ha gia' PORTAL_URL=http://portal-legittimo:3080: un chiamante
    locale che prova a far contattare un host diverso (es. un server sotto il suo
    controllo) non deve riuscirci — si usa sempre il PORTAL_URL gia' configurato."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTAL_URL", "http://portal-legittimo:3080")
    # Isola dall'inquinamento di os.environ lasciato da altri test che chiamano
    # save_to_env() (apply_runtime_config scrive su os.environ reale, non via
    # monkeypatch): qui serve solo PORTAL_URL già configurato, non il JWT secret.
    # Senza questo delenv, se test_setup_router.py viene collezionato prima di questo
    # file (ordine alfabetico: "test_setup_router.py" < "test_setup_router_security.py"),
    # il PRAMAIA_JWT_SECRET lasciato da quel test farebbe risultare l'app "già
    # configurata" e l'endpoint risponderebbe 403 invece di 200.
    monkeypatch.delenv("PRAMAIA_JWT_SECRET", raising=False)

    from backend.main import app
    from backend.routers import setup_router

    # is_app_configured() di questa app rilegge sempre il .env da disco
    # (load_dotenv(override=True), per restare aggiornata dopo il wizard) e
    # python-dotenv senza usecwd risale dalla cartella di setup_router.py, non
    # da tmp_path: senza questo patch risalirebbe fino al .env reale del repo,
    # sovrascrivendo i monkeypatch.setenv/delenv di questo test con lo stato
    # reale su disco.
    monkeypatch.setattr(setup_router, "load_dotenv", lambda *a, **k: False)

    requested_urls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            requested_urls.append(url)
            return _PortalResponse(200, {"jwt_secret": "y" * 48, "app_id": "PramaIA-PrivacyManager"})

    # Il client ASGI verso la nostra app deve nascere PRIMA di sostituire
    # httpx.AsyncClient: è un attributo del modulo httpx condiviso globalmente,
    # quindi se patchato prima anche _local_client() costruirebbe un FakeAsyncClient
    # invece del vero client ASGI.
    async with await _local_client(app) as client:
        monkeypatch.setattr(setup_router.httpx, "AsyncClient", FakeAsyncClient)
        response = await client.post(f"{API}/fetch-from-portal", json={
            "app_id": "PramaIA-PrivacyManager",
            "app_secret": "x" * 32,
            "portal_url": "http://attacker.example.com",
        })

    assert response.status_code == 200
    assert len(requested_urls) == 1
    assert requested_urls[0].startswith("http://portal-legittimo:3080")
    assert (tmp_path / ".env").exists()
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "http://portal-legittimo:3080" in env_content
    assert "attacker.example.com" not in env_content
