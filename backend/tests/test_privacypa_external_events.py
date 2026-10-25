"""Connettore VideoPA (W3): ricezione degli eventi tecnici, segnalazione per il DPO, task, archiviazione e conservazione."""
import uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_external_events import ExternalEvent, ExternalEventStatus
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_outbox import OutboxEvent
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_tasks import PrivacyTask
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
INT = f"{API}/integrations"
RULE = "EXTERNAL_CHANGE_REVIEW_PENDING"
ADMIN = _h(TA, "root", "admin")
VIDEOPA = _h(TA, "videopa")


@pytest_asyncio.fixture
async def env_ev(env):
    await env["grant"](TA, "videopa", R.INTEGRATION, ScopeType.TENANT, None)
    await env["grant"](TA, "reader", R.READ_ONLY, ScopeType.TENANT, None)
    await env["grant"](TA, "dpo", R.DPO, ScopeType.TENANT, None)
    await env["grant"](TA, "mgr-a", R.MANAGER, ScopeType.UNIT, "unit-a")
    await env["grant"](TB, "videopa", R.INTEGRATION, ScopeType.TENANT, None)
    return env


def ev(processing_id, kind="AudioEnabled", **extra):
    return {"source": "VIDEOPA", "event_id": str(uuid.uuid4()), "type": kind, "occurred_at": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
            "processing_id": processing_id, "subject_ref": "CAM-0042", "summary": "Audio attivato", "link": "https://videopa.example/cameras/CAM-0042",
            **extra}


async def post(env, events, headers=VIDEOPA, **kw):
    return await env["client"].post(f"{INT}/events", json={"events": events}, headers=headers, **kw)


async def findings(env, headers=ADMIN):
    r = await env["client"].get(f"{API}/findings", params={"code": RULE}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def rows(env, model, *where):
    async with env["maker"]() as db:
        return list((await db.execute(select(model).where(*where))).scalars())


# ================================================================ ricezione

@pytest.mark.asyncio
async def test_event_is_accepted_and_opens_one_finding_and_one_task(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).privacy_referent_user_id = "referente-1"
        await db.commit()
    r = await post(env, [ev(pid)])
    assert r.status_code == 202
    body = r.json()
    assert body["counts"] == {"ACCEPTED": 1, "DUPLICATE": 0, "UNMATCHED": 0, "REJECTED": 0}
    assert body["results"][0]["outcome"] == "ACCEPTED"

    [finding] = await findings(env)
    assert finding["severity"] == "HIGH" and finding["processing_activity_id"] == pid and finding["params"]["events"] == 1
    assert "AudioEnabled" in finding["message"] and finding["status"] == "OPEN"
    [task] = await rows(env, PrivacyTask, PrivacyTask.title.like("Rivedere modifica tecnica%"))
    assert task.assigned_unit_id == "unit-a" and task.assigned_user_id == "referente-1" and task.source_id == pid    # decisione D
    assert "AudioEnabled" in task.title

    [stored] = await rows(env, ExternalEvent)
    assert stored.status == ExternalEventStatus.PENDING and stored.processing_activity_id == pid and stored.received_by == "videopa"
    assert stored.subject_ref == "CAM-0042" and stored.summary == "Audio attivato"
    # audit e outbox (quindi anche webhook), senza il riassunto descrittivo
    [audit] = await rows(env, AuditLog, AuditLog.event_type == "external_event.received")
    assert "Audio attivato" not in str(audit.new_values) and audit.new_values["type"] == "AudioEnabled"
    assert await rows(env, OutboxEvent, OutboxEvent.event_type == "external_event.received")


@pytest.mark.asyncio
async def test_severity_follows_the_event_type(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    await post(env, [ev(pid, "CameraMoved"), ev(pid, "AnalyticsEnabled")])
    by_type = {f["params"]["type"]: f["severity"] for f in await findings(env)}
    assert by_type == {"CameraMoved": "MEDIUM", "AnalyticsEnabled": "HIGH"}


@pytest.mark.asyncio
async def test_events_of_the_same_type_on_a_processing_share_one_finding_and_one_task(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    await post(env, [ev(pid, "CameraMoved") for _ in range(50)])
    [f] = await findings(env)
    assert f["params"]["events"] == 50 and "50 eventi" in f["message"]
    await post(env, [ev(pid, "CameraMoved")])
    [f2] = await findings(env)
    assert f2["id"] == f["id"] and f2["params"]["events"] == 51                     # stessa segnalazione, contatore aggiornato
    assert len(await rows(env, PrivacyTask, PrivacyTask.title.like("Rivedere modifica tecnica%"))) == 1
    await post(env, [ev(pid, "PrivacyMaskChanged"), ev(env["ids"]["proc_b"], "CameraMoved")])
    assert len(await findings(env)) == 3                                            # altro tipo e altro trattamento: segnalazioni distinte


@pytest.mark.asyncio
async def test_a_batch_of_100_is_accepted_and_101_is_not(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    assert (await post(env, [ev(pid, "CameraMoved") for _ in range(101)])).status_code == 422
    assert not await rows(env, ExternalEvent)
    r = await post(env, [ev(pid, "CameraMoved") for _ in range(100)])
    assert r.status_code == 202 and r.json()["counts"]["ACCEPTED"] == 100
    for bad in ({"events": []}, {"events": "x"}, {}):
        assert (await env["client"].post(f"{INT}/events", json=bad, headers=VIDEOPA)).status_code == 422


@pytest.mark.asyncio
async def test_duplicates_are_recognised_across_and_within_calls(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    one = ev(pid)
    first = (await post(env, [one, one])).json()
    assert [r["outcome"] for r in first["results"]] == ["ACCEPTED", "DUPLICATE"]
    again = (await post(env, [one])).json()
    assert again["counts"]["DUPLICATE"] == 1 and len(await rows(env, ExternalEvent)) == 1
    # lo stesso event_id in un altro ente è un altro evento
    other = (await post(env, [{**one, "processing_id": env["ids"]["proc_x"]}], headers=_h(TB, "videopa"))).json()
    assert other["counts"]["ACCEPTED"] == 1


@pytest.mark.asyncio
async def test_rejected_events_carry_a_reason_and_do_not_block_the_others(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    future = (datetime.utcnow() + timedelta(days=3)).isoformat()
    events = [
        ev(pid, "TipoInventato"), ev(pid, source="ALTRO"), ev(pid, occurred_at="ieri"), ev(pid, occurred_at=future),
        ev(pid, summary="x" * 201), ev(pid, link="javascript:alert(1)"), {**ev(pid), "event_id": ""}, "non un oggetto",
        ev(pid, subject_ref="c" * 101), ev(pid, "CameraMoved"),
    ]
    res = (await post(env, events)).json()
    outcomes = [r["outcome"] for r in res["results"]]
    assert outcomes == ["REJECTED"] * 9 + ["ACCEPTED"]
    reasons = " | ".join(r["reason"] for r in res["results"][:9])
    for fragment in ("Tipo di evento sconosciuto", "Sorgente sconosciuta", "ISO 8601", "nel futuro", "200 caratteri", "http(s)", "event_id", "oggetto", "100 caratteri"):
        assert fragment in reasons
    assert len(await rows(env, ExternalEvent)) == 1                                  # i rifiutati non lasciano nulla


@pytest.mark.asyncio
async def test_only_contract_fields_are_kept(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    await post(env, [ev(pid, ip="10.1.2.3", latitude=45.1, longitude=9.2, password="segreto", evidence="frame.jpg", rtsp="rtsp://u:p@10.0.0.1")])
    [e] = await rows(env, ExternalEvent)
    dump = str(e.to_dict()) + str({c.name: getattr(e, c.name) for c in ExternalEvent.__table__.columns})
    for leaked in ("10.1.2.3", "45.1", "9.2", "segreto", "frame.jpg", "rtsp"):
        assert leaked not in dump
    assert {c.name for c in ExternalEvent.__table__.columns} == {
        "id", "row_version", "tenant_id", "source", "event_id", "event_type", "occurred_at", "processing_activity_id", "claimed_processing_id",
        "subject_ref", "summary", "link", "status", "received_by", "received_at", "archived_by", "archived_at", "archive_note"}


@pytest.mark.asyncio
async def test_unknown_foreign_or_out_of_scope_processing_is_unmatched_without_side_effects(env_ev):
    env, ids = env_ev, env_ev["ids"]
    await env["grant"](TA, "videopa-b", R.INTEGRATION, ScopeType.UNIT, "unit-b")
    mine = (await post(env, [ev(ids["proc_x"]), ev("inesistente"), ev(None)], headers=VIDEOPA)).json()       # altro ente, inesistente, senza id
    assert [r["outcome"] for r in mine["results"]] == ["UNMATCHED"] * 3
    scoped = (await post(env, [ev(ids["proc_a"])], headers=_h(TA, "videopa-b"))).json()                      # struttura fuori dall'ambito
    assert scoped["results"][0]["outcome"] == "UNMATCHED"
    assert "unit-a" not in str(scoped) and await findings(env) == []
    assert not await rows(env, PrivacyTask)
    assert {e.status for e in await rows(env, ExternalEvent)} == {ExternalEventStatus.UNMATCHED}
    assert all(e.processing_activity_id is None for e in await rows(env, ExternalEvent))
    assert (await rows(env, ExternalEvent, ExternalEvent.claimed_processing_id == ids["proc_x"]))                # resta per lo smistamento
    # Chi ha l'intero ente li vede; un responsabile di una sola struttura no.
    listed = (await env["client"].get(f"{INT}/external-events?status=UNMATCHED", headers=ADMIN)).json()
    assert listed["total"] == 4
    assert (await env["client"].get(f"{INT}/external-events", headers=_h(TA, "mgr-a"))).json()["total"] == 0


@pytest.mark.asyncio
async def test_only_the_service_account_and_admins_can_send_events(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    for who in (_h(TA, "reader"), _h(TA, "dpo"), _h(TA, "mgr-a"), _h(TA, "nobody")):
        assert (await post(env, [ev(pid)], headers=who)).status_code == 403
    assert (await post(env, [ev(pid)], headers=ADMIN)).status_code == 202
    assert len(await rows(env, ExternalEvent)) == 1


# ================================================================ chiusura

async def _first_event(env):
    return (await env["client"].get(f"{INT}/external-events", headers=ADMIN)).json()["items"][0]


@pytest.mark.asyncio
async def test_archiving_with_a_note_closes_the_finding(env_ev):
    env, pid, c = env_ev, env_ev["ids"]["proc_a"], env_ev["client"]
    await post(env, [ev(pid, "CameraMoved"), ev(pid, "CameraMoved")])
    events = (await c.get(f"{INT}/external-events?processing_id={pid}", headers=_h(TA, "dpo"))).json()["items"]
    assert len(events) == 2
    url = lambda e: f"{INT}/external-events/{e['id']}/archive"

    assert (await c.post(url(events[0]), json={"note": "x"}, headers=_h(TA, "dpo"))).status_code == 428                 # If-Match
    assert (await c.post(url(events[0]), json={"note": ""}, headers=ifm(1, _h(TA, "dpo")))).status_code == 422          # nota obbligatoria
    assert (await c.post(url(events[0]), json={"note": "x"}, headers=ifm(1, _h(TA, "reader")))).status_code == 403
    assert (await c.post(url(events[0]), json={"note": "x"}, headers=ifm(1, VIDEOPA))).status_code == 403
    assert (await c.post(url(events[0]), json={"note": "x"}, headers=ifm(7, _h(TA, "dpo")))).status_code == 409          # versione vecchia

    done = await c.post(url(events[0]), json={"note": "Nessun impatto: manutenzione"}, headers=ifm(1, _h(TA, "dpo")))
    assert done.status_code == 200 and done.json()["status"] == "ARCHIVED" and done.json()["archived_by"] == "dpo"
    [f] = await findings(env)
    assert f["params"]["events"] == 1                                                  # ne resta uno: la segnalazione resta, con il contatore giusto
    again = await c.post(url(events[0]), json={"note": "ancora"}, headers=ifm(2, _h(TA, "dpo")))
    assert again.status_code == 409                                                    # già archiviato

    await c.post(url(events[1]), json={"note": "Nessun impatto"}, headers=ifm(1, _h(TA, "dpo")))
    assert await findings(env) == []                                                   # nessun evento pendente: segnalazione chiusa
    resolved = (await c.get(f"{API}/findings", params={"code": RULE, "status": "RESOLVED"}, headers=ADMIN)).json()
    assert len(resolved) == 1
    assert len(await rows(env, AuditLog, AuditLog.event_type == "external_event.archived")) == 2


@pytest.mark.asyncio
async def test_a_later_review_of_the_processing_closes_the_finding_but_the_same_day_does_not(env_ev):
    env, pid = env_ev, env_ev["ids"]["proc_a"]
    await post(env, [ev(pid, "VmsChanged")])
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).last_review_date = datetime.utcnow().date() - timedelta(days=1)   # prima dell'evento
        await db.commit()
    assert len(await findings(env)) == 1
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).last_review_date = datetime.utcnow().date()                       # lo stesso giorno
        await db.commit()
    assert len(await findings(env)) == 1
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).last_review_date = datetime.utcnow().date() + timedelta(days=1)   # dopo
        await db.commit()
    assert await findings(env) == []


@pytest.mark.asyncio
async def test_listing_follows_the_users_scope_and_filters(env_ev):
    env, ids, c = env_ev, env_ev["ids"], env_ev["client"]
    await post(env, [ev(ids["proc_a"], "CameraMoved"), ev(ids["proc_b"], "AudioEnabled")])
    mgr = (await c.get(f"{INT}/external-events", headers=_h(TA, "mgr-a"))).json()
    assert [e["processing_activity_id"] for e in mgr["items"]] == [ids["proc_a"]]                 # solo la sua struttura
    allv = (await c.get(f"{INT}/external-events", headers=ADMIN)).json()
    assert allv["total"] == 2
    assert (await c.get(f"{INT}/external-events?type=AudioEnabled", headers=ADMIN)).json()["total"] == 1
    assert (await c.get(f"{INT}/external-events?processing_id={ids['proc_b']}", headers=ADMIN)).json()["total"] == 1
    assert (await c.get(f"{INT}/external-events?status=ARCHIVED", headers=ADMIN)).json()["total"] == 0
    for who in (_h(TA, "reader"), VIDEOPA, _h(TA, "nobody")):
        assert (await c.get(f"{INT}/external-events", headers=who)).status_code == 403
    # il responsabile della struttura A non archivia (solo la governance) e non vede quello della B
    assert (await c.post(f"{INT}/external-events/{allv['items'][0]['id']}/archive", json={"note": "x"}, headers=ifm(1, _h(TA, "mgr-a")))).status_code == 403


@pytest.mark.asyncio
async def test_another_tenant_never_sees_or_archives_our_events(env_ev):
    env, pid, c = env_ev, env_ev["ids"]["proc_a"], env_ev["client"]
    await post(env, [ev(pid)])
    event = await _first_event(env)
    other = _h(TB, "root", "admin")
    assert (await c.get(f"{INT}/external-events", headers=other)).json()["total"] == 0
    assert (await c.post(f"{INT}/external-events/{event['id']}/archive", json={"note": "x"}, headers=ifm(1, other))).status_code == 404
    assert await findings(env, headers=other) == []
    assert (await rows(env, ExternalEvent))[0].status == ExternalEventStatus.PENDING


# ================================================================ conservazione

@pytest.mark.asyncio
async def test_events_older_than_the_retention_are_deleted_on_the_next_delivery(env_ev):
    env, pid, c = env_ev, env_ev["ids"]["proc_a"], env_ev["client"]
    await post(env, [ev(pid, "CameraMoved")])
    async with env["maker"]() as db:
        old = (await db.execute(select(ExternalEvent))).scalars().one()
        old.received_at = datetime.utcnow() - timedelta(days=800)                       # ~26 mesi
        await db.commit()
    await post(env, [ev(pid, "CameraInstalled")])
    assert [e.event_type for e in await rows(env, ExternalEvent)] == ["CameraInstalled"]

    async with env["maker"]() as db:                                                    # parametro dell'ente: 12 mesi
        (await db.execute(select(ExternalEvent))).scalars().one().received_at = datetime.utcnow() - timedelta(days=400)
        await db.commit()
    version = (await c.get(f"{API}/policy/integrations", headers=ADMIN)).json()["row_version"]
    saved = await c.put(f"{API}/policy/integrations", json={"external_events_retention_months": 12}, headers=ifm(version, ADMIN))
    assert saved.status_code == 200 and saved.json()["values"]["external_events_retention_months"] == 12
    await post(env, [ev(pid, "CameraMoved")])
    assert [e.event_type for e in await rows(env, ExternalEvent)] == ["CameraMoved"]
    for bad in (0, 121):
        r = await c.put(f"{API}/policy/integrations", json={"external_events_retention_months": bad}, headers=ifm(saved.json()["row_version"], ADMIN))
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_rule_is_configurable_like_the_others_and_disabling_it_closes_findings(env_ev):
    env, pid, c = env_ev, env_ev["ids"]["proc_a"], env_ev["client"]
    await post(env, [ev(pid)])
    rule = next(r for r in (await c.get(f"{API}/compliance/rules", headers=ADMIN)).json() if r["code"] == RULE)
    assert rule["enabled"] and rule["severity"] == "MEDIUM"                            # la gravità dell'evento sovrascrive quella della regola
    off = await c.put(f"{API}/compliance/rules/{RULE}", json={"enabled": False}, headers=ifm(rule["row_version"], ADMIN))
    assert off.status_code == 200, off.text
    assert await findings(env) == []
    assert (await rows(env, ExternalEvent))[0].status == ExternalEventStatus.PENDING     # l'evento resta: la regola non lo cancella
