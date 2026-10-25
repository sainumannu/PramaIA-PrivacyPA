"""
OP08 (RICHIESTE_PLATFORM.md): la rotta che serve il frontend statico in produzione
(backend/main.py, blocco "Frontend statico") non deve mai uscire dalla cartella
frontend/build — un file .env accanto contiene PRAMAIA_JWT_SECRET, condiviso da tutta
la piattaforma, leggibile senza autenticazione da chiunque raggiunga la porta.
"""
import os

import httpx
import pytest

MARKER = "non-deve-uscire-dalla-cartella-build"


@pytest.fixture
def marker_file_above_build():
    import backend.main as main_module

    marker_path = os.path.join(os.path.dirname(main_module._FRONTEND_BUILD_DIR), "MARKER.env")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write(MARKER)
    try:
        yield marker_path
    finally:
        os.remove(marker_path)


@pytest.mark.asyncio
async def test_static_route_never_escapes_frontend_build(marker_file_above_build):
    from backend.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/..%2fMARKER.env", "/%2e%2e/MARKER.env", "/..%5cMARKER.env", "/static/..%2f..%2fMARKER.env"):
            response = await client.get(path)
            assert MARKER not in response.text, path

        index = await client.get("/qualunque/pagina/non/esistente")
        assert "<!doctype html>" in index.text.lower() or "<html" in index.text.lower()
        assert index.headers["cache-control"] == "no-cache"
