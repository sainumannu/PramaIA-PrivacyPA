"""Concorrenza ottimistica (§54): If-Match obbligatorio, 409 su versione stantia, nessuna sovrascrittura silenziosa."""
import pytest
import pytest_asyncio
from sqlalchemy.orm.exc import StaleDataError

from backend.api_errors import register_error_handlers
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.models.privacypa_organization import OrganizationalUnit
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm, version_of

BASE = "/api/privacypa/processing-activities"


def _body(**over):
    # PATCH sostituisce i campi omessi con i default: la descrizione va sempre inviata
    return {"title": "Titolo", "organizational_unit_id": "unit-a", "description": "Descrizione", **over}


@pytest_asyncio.fixture
async def mgr(env):
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    return _h(TA, "mgr")


@pytest.mark.asyncio
async def test_patch_without_if_match_is_rejected_428(env, mgr):
    r = await env["client"].patch(f"{BASE}/{env['ids']['proc_a']}", json=_body(), headers=mgr)
    assert r.status_code == 428
    assert "If-Match" in r.json()["detail"]
    for bad in ('abc', '', '"x"', '*'):
        r = await env["client"].patch(
            f"{BASE}/{env['ids']['proc_a']}", json=_body(), headers={**mgr, "If-Match": bad}
        )
        assert r.status_code == 428, bad


@pytest.mark.asyncio
async def test_two_editors_second_gets_409_and_first_change_survives(env, mgr):
    c, pid = env["client"], env["ids"]["proc_a"]
    seen_by_both = await version_of(c, f"{BASE}/{pid}")

    first = await c.patch(f"{BASE}/{pid}", json=_body(title="Modifica di Anna"), headers=ifm(seen_by_both, mgr))
    assert first.status_code == 200
    assert first.json()["row_version"] > seen_by_both

    second = await c.patch(f"{BASE}/{pid}", json=_body(title="Modifica di Bruno"), headers=ifm(seen_by_both, mgr))
    assert second.status_code == 409
    assert "modificato da un altro utente" in second.json()["detail"]
    assert second.headers["etag"] == f'"{first.json()["row_version"]}"'

    current = (await c.get(f"{BASE}/{pid}", headers=mgr)).json()
    assert current["title"] == "Modifica di Anna"
    assert current["version_no"] == 2  # una sola modifica applicata: nessuna versione di business spuria


@pytest.mark.asyncio
async def test_retry_with_fresh_version_succeeds(env, mgr):
    c, pid = env["client"], env["ids"]["proc_a"]
    v = await version_of(c, f"{BASE}/{pid}")
    assert (await c.patch(f"{BASE}/{pid}", json=_body(title="A"), headers=ifm(v, mgr))).status_code == 200
    assert (await c.patch(f"{BASE}/{pid}", json=_body(title="B"), headers=ifm(v, mgr))).status_code == 409
    v2 = await version_of(c, f"{BASE}/{pid}")
    assert (await c.patch(f"{BASE}/{pid}", json=_body(title="B"), headers=ifm(v2, mgr))).status_code == 200


@pytest.mark.asyncio
async def test_etag_header_matches_row_version_and_accepts_weak_form(env, mgr):
    c, pid = env["client"], env["ids"]["proc_a"]
    got = await c.get(f"{BASE}/{pid}", headers=mgr)
    assert got.headers["etag"] == f'"{got.json()["row_version"]}"'
    ok = await c.patch(
        f"{BASE}/{pid}", json=_body(title="Weak"),
        headers={**mgr, "If-Match": "W/" + got.headers["etag"]},
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_status_transition_requires_the_reviewed_version(env, mgr):
    """Un'approvazione non deve applicarsi a contenuto modificato dopo la revisione."""
    c, pid = env["client"], env["ids"]["proc_a"]
    reviewed = await version_of(c, f"{BASE}/{pid}")
    # qualcun altro modifica il trattamento dopo che l'approvatore lo ha letto
    assert (await c.patch(f"{BASE}/{pid}", json=_body(title="Cambiato"), headers=ifm(reviewed, mgr))).status_code == 200

    stale = await c.post(f"{BASE}/{pid}/status", json={"status": "IN_REVIEW"}, headers=ifm(reviewed, mgr))
    assert stale.status_code == 409
    assert (await c.post(f"{BASE}/{pid}/status", json={"status": "IN_REVIEW"}, headers=mgr)).status_code == 428
    fresh = await version_of(c, f"{BASE}/{pid}")
    ok = await c.post(f"{BASE}/{pid}/status", json={"status": "IN_REVIEW"}, headers=ifm(fresh, mgr))
    assert ok.status_code == 200 and ok.json()["status"] == "IN_REVIEW"


@pytest.mark.asyncio
async def test_version_is_checked_after_scope_so_it_does_not_leak(env):
    """Chi non ha accesso al record riceve 403/404, mai informazioni sulla sua versione."""
    c, pid = env["client"], env["ids"]["proc_a"]
    await env["grant"](TB, "b-mgr", R.PRIVACY_MANAGER)
    other_tenant = await c.patch(f"{BASE}/{pid}", json=_body(), headers=ifm(999, _h(TB, "b-mgr")))
    assert other_tenant.status_code == 404
    assert "etag" not in other_tenant.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["asset", "party", "task", "document", "unit", "dpia", "transfer"])
async def test_other_entities_enforce_if_match(env, mgr, kind):
    c, maker = env["client"], env["maker"]
    if kind == "asset":
        async with maker() as db:
            a = Asset(tenant_id=TA, code="AS-000001", name="Sistema")
            db.add(a)
            await db.commit()
            url, body, version = f"/api/privacypa/assets/{a.id}", {"name": "Nuovo nome"}, a.row_version
    elif kind == "party":
        created = (await c.post("/api/privacypa/parties", json={"legal_name": "Beta S.r.l."}, headers=mgr)).json()
        url, body, version = f"/api/privacypa/parties/{created['id']}", {"legal_name": "Beta Srl"}, created["row_version"]
    elif kind == "task":
        created = (await c.post("/api/privacypa/tasks", json={"title": "T"}, headers=mgr)).json()
        url, body, version = f"/api/privacypa/tasks/{created['id']}", {"title": "T2"}, created["row_version"]
    elif kind == "document":
        created = (await c.post("/api/privacypa/documents", json={"title": "D"}, headers=mgr)).json()
        url, body, version = f"/api/privacypa/documents/{created['id']}", {"title": "D2"}, created["row_version"]
    elif kind == "dpia":
        created = (await c.post("/api/privacypa/dpias", json={"title": "P"}, headers=mgr)).json()
        url, body, version = f"/api/privacypa/dpias/{created['id']}", {"title": "P2"}, created["row_version"]
    elif kind == "transfer":
        created = (await c.post(
            "/api/privacypa/transfers",
            json={"title": "Tr", "country_code": "US", "mechanism": "SCC"}, headers=mgr,
        )).json()
        url, body, version = (
            f"/api/privacypa/transfers/{created['id']}",
            {"title": "Tr2", "country_code": "US", "mechanism": "SCC", "tia_required": True},
            created["row_version"],
        )
    else:
        async with maker() as db:
            unit = await db.get(OrganizationalUnit, "unit-a")
            url, body, version = "/api/privacypa/organizational-units/unit-a", {"name": "Rinominata"}, unit.row_version

    assert (await c.patch(url, json=body, headers=mgr)).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(version + 5, mgr))).status_code == 409
    assert (await c.patch(url, json=body, headers=ifm(version, mgr))).status_code == 200
    assert (await c.patch(url, json=body, headers=ifm(version, mgr))).status_code == 409  # ora stantia


@pytest.mark.asyncio
async def test_db_level_guard_detects_concurrent_update_race(env):
    """Anche senza If-Match, due sessioni che aggiornano la stessa riga non si sovrascrivono."""
    maker, pid = env["maker"], env["ids"]["proc_a"]
    async with maker() as s1, maker() as s2:
        a1 = await ProcessingService(s1).get_activity(TA, pid)
        a2 = await ProcessingService(s2).get_activity(TA, pid)
        a1.title = "Sessione 1"
        await s1.commit()
        a2.title = "Sessione 2"
        with pytest.raises(StaleDataError):
            await s2.commit()


@pytest.mark.asyncio
async def test_stale_data_error_is_mapped_to_409_without_technical_details():
    import httpx
    from fastapi import FastAPI

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise StaleDataError("UPDATE statement on table 'x' expected to update 1 row(s); 0 were matched")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/boom")
    assert r.status_code == 409
    assert "UPDATE" not in r.text and "table" not in r.text
