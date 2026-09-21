"""Lo scope di scrittura di documenti e task è quello dei ruoli che hanno il permesso, non quello di lettura (§9.3)."""
import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.tests.conftest import TA, _h, ifm

API = "/api/privacypa"


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    await grant(TA, "mgr", R.PRIVACY_MANAGER)
    # sola lettura sull'ufficio A, può scrivere solo sull'ufficio B
    await grant(TA, "mixed", R.READ_ONLY, ScopeType.UNIT, "unit-a")
    await grant(TA, "mixed", R.OPERATOR, ScopeType.UNIT, "unit-b")
    return {"mgr": _h(TA, "mgr"), "mixed": _h(TA, "mixed")}


def _doc(links, title="Documento"):
    return {"title": title, "links": [{"entity_type": "processing_activity", "entity_id": pid} for pid in links]}


@pytest.mark.asyncio
async def test_document_writes_need_write_scope_on_the_linked_processing(env, w):
    c, ids = env["client"], env["ids"]
    created = await c.post(f"{API}/documents", json=_doc([ids["proc_a"]]), headers=w["mgr"])
    assert created.status_code == 201, created.text
    doc = created.json()

    # lo vede (sola lettura su A) ma non lo modifica, non ci carica file e non lo elimina
    assert (await c.get(f"{API}/documents/{doc['id']}", headers=w["mixed"])).status_code == 200
    body = _doc([ids["proc_a"]], "Rinominato")
    assert (await c.patch(f"{API}/documents/{doc['id']}", json=body, headers=ifm(doc["row_version"], w["mixed"]))).status_code == 403
    upload = await c.post(f"{API}/documents/{doc['id']}/versions", files={"file": ("a.txt", b"ciao", "text/plain")}, headers=w["mixed"])
    assert upload.status_code == 403
    assert (await c.delete(f"{API}/documents/{doc['id']}", headers=w["mixed"])).status_code == 403

    # non può nemmeno collegare un nuovo documento al trattamento dell'ufficio A
    assert (await c.post(f"{API}/documents", json=_doc([ids["proc_a"]]), headers=w["mixed"])).status_code == 403
    on_b = await c.post(f"{API}/documents", json=_doc([ids["proc_b"]]), headers=w["mixed"])
    assert on_b.status_code == 201
    mine = on_b.json()
    assert (await c.patch(f"{API}/documents/{mine['id']}", json=_doc([ids["proc_b"]], "Mio"), headers=ifm(mine["row_version"], w["mixed"]))).status_code == 200
    assert (await c.patch(f"{API}/documents/{mine['id']}", json=_doc([ids["proc_a"]], "Mio"), headers=ifm(2, w["mixed"]))).status_code == 403


@pytest.mark.asyncio
async def test_task_writes_need_write_scope_on_the_source_processing(env, w):
    c, ids = env["client"], env["ids"]
    task = (await c.post(f"{API}/tasks", json={"title": "Verifica", "source_type": "PROCESSING", "source_id": ids["proc_a"]}, headers=w["mgr"])).json()
    assert (await c.get(f"{API}/tasks", headers=w["mixed"])).json()[0]["id"] == task["id"]  # visibile per lettura

    body = {"title": "Cambiato", "source_type": "PROCESSING", "source_id": ids["proc_a"]}
    assert (await c.patch(f"{API}/tasks/{task['id']}", json=body, headers=ifm(task["row_version"], w["mixed"]))).status_code == 403
    assert (await c.delete(f"{API}/tasks/{task['id']}", headers=w["mixed"])).status_code == 403
    assert (await c.post(f"{API}/tasks", json={"title": "X", "source_type": "PROCESSING", "source_id": ids["proc_a"]}, headers=w["mixed"])).status_code == 403

    own = await c.post(f"{API}/tasks", json={"title": "Y", "source_type": "PROCESSING", "source_id": ids["proc_b"]}, headers=w["mixed"])
    assert own.status_code == 201
    manual = await c.post(f"{API}/tasks", json={"title": "Manuale"}, headers=w["mixed"])
    assert manual.status_code == 201
    moved = await c.patch(
        f"{API}/tasks/{manual.json()['id']}", json={"title": "Manuale", "source_type": "PROCESSING", "source_id": ids["proc_a"]},
        headers=ifm(manual.json()["row_version"], w["mixed"]),
    )
    assert moved.status_code == 403
