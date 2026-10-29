"""Outbox transazionale (§130) e portabilità dei dati del tenant (§173)."""
import glob
import hashlib
import io
import json
import tempfile
import zipfile
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio

from backend.models.privacypa_documents import DocumentVersion
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_outbox import OutboxEvent
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
OUTBOX = f"{API}/outbox"
ADMIN = _h(TA, "root", "admin")
ADMIN_B = _h(TB, "root-b", "admin")
TODAY = date.today()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    await grant(TA, "mgr", R.PRIVACY_MANAGER)
    await grant(TA, "dpo", R.DPO)
    await grant(TA, "op", R.OPERATOR)
    await grant(TA, "reader", R.READ_ONLY)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "op", "reader")}


async def _events(env, who=ADMIN, **params):
    r = await env["client"].get(OUTBOX, params=params, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- outbox

@pytest.mark.asyncio
async def test_domain_events_are_published_with_references_and_dates_only(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    v = await version_of(c, f"{API}/processing-activities/{pid}")
    await c.post(f"{API}/processing-activities/{pid}/status", json={"status": "IN_REVIEW", "notes": "note riservate"}, headers=ifm(v, w["op"]))
    party = (await c.post(f"{API}/parties", json={"legal_name": "Fornitore Segreto", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])).json()
    await c.post(f"{API}/contracts", json={"party_id": party["id"], "title": "Contratto riservato", "status": "ACTIVE", "end_date": (TODAY + timedelta(days=20)).isoformat()}, headers=w["mgr"])
    await c.post(f"{API}/tasks", json={"title": "Task con dati", "description": "descrizione", "due_at": "2030-01-05T10:00:00"}, headers=w["mgr"])

    events = await _events(env)
    by_type = {}
    for e in events:
        by_type.setdefault(e["event_type"], []).append(e)
    assert {"approval.requested", "processing_activity.status.in_review", "contract.create", "task.create"} <= set(by_type)
    assert "party.create" not in by_type  # non tutti gli eventi di audit sono pubblicati

    contract = by_type["contract.create"][0]
    assert contract["aggregate_type"] == "contract" and contract["payload"]["data"] == {
        "status": "ACTIVE", "end_date": (TODAY + timedelta(days=20)).isoformat(),
    }
    task = by_type["task.create"][0]["payload"]
    assert task["data"]["due_at"].startswith("2030-01-05") and task["actor"] == "mgr"
    # nessun contenuto descrittivo o personale nei payload
    raw = json.dumps(events)
    for secret in ("Fornitore Segreto", "Contratto riservato", "Task con dati", "descrizione", "note riservate"):
        assert secret not in raw
    assert all(e["retry_count"] == 0 and e["processed_at"] is None and e["payload"]["audit_id"] for e in events)


@pytest.mark.asyncio
async def test_a_rejected_request_publishes_nothing(env, w):
    c, pid = env["client"], env["ids"]["proc_a"]
    before = len(await _events(env))
    stale = await c.post(f"{API}/processing-activities/{pid}/status", json={"status": "IN_REVIEW"}, headers=ifm(99, w["op"]))
    assert stale.status_code == 409
    assert len(await _events(env)) == before


@pytest.mark.asyncio
async def test_finding_events_are_published(env, w):
    c = env["client"]
    party = (await c.post(f"{API}/parties", json={"legal_name": "Cloud", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])).json()
    await c.post(f"{API}/parties/agreements", json={"party_id": party["id"], "status": "SIGNED", "expiry_date": (TODAY - timedelta(days=1)).isoformat()}, headers=w["mgr"])
    (finding,) = (await c.get(f"{API}/findings", params={"code": "AGREEMENT_EXPIRED"}, headers=w["mgr"])).json()
    opened = [e for e in await _events(env, event_type="finding.") if e["aggregate_id"] == finding["id"]]
    assert len(opened) == 1 and opened[0]["payload"]["data"]["rule_code"] == "AGREEMENT_EXPIRED"
    assert opened[0]["payload"]["actor"] == "system"


@pytest.mark.asyncio
async def test_consumer_api_lists_acks_retries_and_purges(env, w):
    c = env["client"]
    for i in range(3):
        await c.post(f"{API}/tasks", json={"title": f"T{i}"}, headers=w["mgr"])
    events = await _events(env, event_type="task.")
    assert len(events) == 3 and [e["created_at"] for e in events] == sorted(e["created_at"] for e in events)
    assert len(await _events(env, event_type="task.", limit=2)) == 2

    assert (await c.post(f"{OUTBOX}/{events[0]['id']}/retry", headers=ADMIN)).json()["retry_count"] == 1
    ack = await c.post(f"{OUTBOX}/ack", json={"ids": [events[0]["id"], events[1]["id"], "unknown"]}, headers=ADMIN)
    assert ack.json() == {"acked": 2}
    assert (await c.post(f"{OUTBOX}/ack", json={"ids": [events[0]["id"]]}, headers=ADMIN)).json() == {"acked": 0}  # idempotente
    assert [e["id"] for e in await _events(env, event_type="task.")] == [events[2]["id"]]
    assert {e["id"] for e in await _events(env, event_type="task.", processed="true")} == {events[0]["id"], events[1]["id"]}
    assert (await c.post(f"{OUTBOX}/{events[0]['id']}/retry", headers=ADMIN)).status_code == 404  # già confermato

    # svuotamento dei confermati vecchi
    async with env["maker"]() as db:
        old = await db.get(OutboxEvent, events[0]["id"])
        old.processed_at = datetime.utcnow() - timedelta(days=40)
        await db.commit()
    assert (await c.delete(f"{OUTBOX}/processed", params={"older_than_days": 30}, headers=ADMIN)).json() == {"removed": 1}
    assert (await c.post(f"{OUTBOX}/ack", json={"ids": []}, headers=ADMIN)).status_code == 422


@pytest.mark.asyncio
async def test_outbox_is_admin_only_and_isolated_by_tenant(env, w):
    c = env["client"]
    await c.post(f"{API}/tasks", json={"title": "Solo A"}, headers=w["mgr"])
    for who in ("mgr", "dpo", "op", "reader"):
        assert (await c.get(OUTBOX, headers=w[who])).status_code == 403
        assert (await c.post(f"{OUTBOX}/ack", json={"ids": ["x"]}, headers=w[who])).status_code == 403
    (mine,) = await _events(env, event_type="task.")
    assert await _events(env, who=ADMIN_B, event_type="task.") == []                       # gli eventi di A non compaiono in B
    assert (await c.post(f"{OUTBOX}/ack", json={"ids": [mine["id"]]}, headers=ADMIN_B)).json() == {"acked": 0}
    assert (await c.post(f"{OUTBOX}/{mine['id']}/retry", headers=ADMIN_B)).status_code == 404
    # nemmeno un override di ente può delegare l'accesso all'outbox
    assert (await c.put(f"{API}/policy/permissions", json={"overrides": {"outbox.view": ["OPERATOR"]}}, headers=ifm(0, ADMIN))).status_code == 400


# ---------------------------------------------------------------- export del tenant

def _open(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


@pytest_asyncio.fixture
async def dataset(env, w):
    c, ids = env["client"], env["ids"]
    doc = (await c.post(f"{API}/documents", json={"title": "Informativa", "links": [{"entity_type": "processing_activity", "entity_id": ids["proc_a"]}]}, headers=w["mgr"])).json()
    up = await c.post(f"{API}/documents/{doc['id']}/versions", files={"file": ("informativa.txt", b"contenuto dell'informativa", "text/plain")}, headers=w["mgr"])
    assert up.status_code == 200, up.text
    async with env["maker"]() as db:
        infected_path = env["tmp_docs"] / "malware.bin"
        infected_path.parent.mkdir(parents=True, exist_ok=True)
        infected_path.write_bytes(b"EICAR")
        db.add(DocumentVersion(
            document_id=doc["id"], version_no=99, file_name="malware.bin", mime_type="application/octet-stream",
            file_size=5, storage_key="malware.bin", sha256="0" * 64, scan_status="INFECTED",
        ))
        await db.commit()
    other = (await c.post(f"{API}/documents", json={"title": "Documento di B"}, headers=ADMIN_B)).json()
    return {"doc": doc, "other": other, "bytes": b"contenuto dell'informativa"}


@pytest.mark.asyncio
async def test_tenant_export_contains_only_the_tenant_data_with_valid_checksums(env, w, dataset):
    c, ids = env["client"], env["ids"]
    before = set(glob.glob(f"{tempfile.gettempdir()}/privacypa-export-*.zip"))
    zf = _open(await c.post(f"{API}/export/tenant", headers=ADMIN))
    names = set(zf.namelist())
    assert {"manifest.json", "checksums.txt", "data/processing_activities.json", "data/audit_log.json"} <= names

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "privacypa-tenant-export" and manifest["tenant_id"] == TA and manifest["extracted_by"] == "root"
    assert manifest["tables"]["privacypa_processing_activities"]["rows"] == 2
    assert {"table": "privacypa_import_jobs", "reason": "stato tecnico interno"} in manifest["excluded_tables"]
    assert manifest["documents"]["included"] == 1
    assert manifest["documents"]["skipped"] == [{"document": f"{dataset['doc']['code']} v99", "reason": "minaccia rilevata dalla scansione antimalware"}]

    processing = json.loads(zf.read("data/processing_activities.json"))
    assert {p["id"] for p in processing} == {ids["proc_a"], ids["proc_b"]}  # niente dati dell'altro ente
    assert ids["proc_x"] not in zf.read("data/processing_activities.json").decode()
    for name in names:
        if name.startswith("data/") and name != "data/audit_log.json":
            assert ids["proc_x"] not in zf.read(name).decode(), name
    # tabelle figlie senza tenant_id, esportate tramite la catena di chiavi
    assert manifest["tables"]["privacypa_processing_data_categories"]["rows"] == 2
    assert {d["processing_activity_id"] for d in json.loads(zf.read("data/processing_data_categories.json"))} == {ids["proc_a"], ids["proc_b"]}
    assert dataset["other"]["id"] not in zf.read("data/documents.json").decode()

    docs = [n for n in names if n.startswith("documents/")]
    assert docs == [f"documents/{dataset['doc']['code']}/v1_informativa.txt"] and zf.read(docs[0]) == dataset["bytes"]
    assert not any("malware" in n for n in names)

    # ogni file elencato nel manifest dei checksum è integro e ogni file del pacchetto è elencato
    listed = dict(line.split("  ", 1)[::-1] for line in zf.read("checksums.txt").decode().strip().splitlines())
    assert set(listed) == names - {"checksums.txt"}
    for name, digest in listed.items():
        assert hashlib.sha256(zf.read(name)).hexdigest() == digest, name
    assert set(glob.glob(f"{tempfile.gettempdir()}/privacypa-export-*.zip")) == before  # nessun file temporaneo residuo


@pytest.mark.asyncio
async def test_tenant_export_is_audited_privileged_and_per_tenant(env, w, dataset):
    c = env["client"]
    for who in ("mgr", "dpo", "op", "reader"):
        assert (await c.post(f"{API}/export/tenant", headers=w[who])).status_code == 403
    await env["grant"](TA, "tenant-admin", R.TENANT_ADMIN)
    assert (await c.post(f"{API}/export/tenant", headers=_h(TA, "tenant-admin"))).status_code == 200

    events = (await c.get(f"{API}/audit", params={"entity_type": "tenant"}, headers=ADMIN)).json()
    assert [e["event_type"] for e in events] == ["tenant.export"] and events[0]["actor_user_id"] == "tenant-admin"
    assert events[0]["metadata"]["documents"] == 1 and events[0]["metadata"]["documents_skipped"] == 1

    zb = _open(await c.post(f"{API}/export/tenant", headers=ADMIN_B))
    assert {p["id"] for p in json.loads(zb.read("data/processing_activities.json"))} == {env["ids"]["proc_x"]}
    assert json.loads(zb.read("manifest.json"))["documents"]["included"] == 0
