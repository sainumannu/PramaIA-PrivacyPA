"""Connettori (VideoPA, W1-W2): ruolo INTEGRATION, ping, snapshot di lettura, conservazione a calendario, ETag e riservatezza."""
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio

from backend.auth.privacypa_permissions import DEFAULT_MATRIX, LOCKED_PERMISSIONS
from backend.db.seed_privacypa import _ROLE_CATALOG
from backend.models.privacypa_assessments import Dpia, DpiaStatus
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_notices import NoticeProcessingLink, NoticeStatus, PrivacyNotice, PrivacyNoticeVersion
from backend.models.privacypa_parties import AgreementStatus, Party, PartyType, ProcessingProcessor, ProcessorAgreement
from backend.models.privacypa_processing import ConfidentialityLevel, ProcessingActivity
from backend.models.privacypa_retention import DisposalMethod, RetentionRule, RetentionUnit
from backend.services.privacypa_integration_service import SnapshotService, etag_of_snapshot, rule_days
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
INT = f"{API}/integrations"
POLICY = f"{API}/policy/integrations"
ADMIN = _h(TA, "root", "admin")
VIDEOPA = _h(TA, "videopa")


@pytest_asyncio.fixture
async def env_int(env):
    await env["grant"](TA, "videopa", R.INTEGRATION, ScopeType.TENANT, None)
    await env["grant"](TA, "reader", R.READ_ONLY, ScopeType.TENANT, None)
    return env


def snap(ids, key="proc_a"):
    return f"{INT}/processing-activities/{ids[key]}/snapshot"


async def _add(env, *rows):
    async with env["maker"]() as db:
        db.add_all(rows)
        await db.commit()


async def _set_category(env, value):
    c = env["client"]
    version = (await c.get(POLICY, headers=ADMIN)).json()["row_version"]
    r = await c.put(POLICY, json={"retention_category": value}, headers=ifm(version, ADMIN))
    assert r.status_code == 200, r.text
    return r.json()


def rule(pid, unit, value, *, record_class=None, tenant=TA):
    return RetentionRule(tenant_id=tenant, processing_activity_id=pid, record_class=record_class, retention_unit=unit,
                         retention_value=value, retention_criterion="test", disposal_method=DisposalMethod.DELETION)


# ================================================================ W1: ruolo e ping

def test_integration_role_is_only_in_integration_permissions_and_locked():
    for code, roles in DEFAULT_MATRIX.items():
        assert (R.INTEGRATION in roles) == code.startswith("integration."), code
    assert {"integration.view", "integration.create"} <= LOCKED_PERMISSIONS
    assert R.INTEGRATION in [code for code, _, _ in _ROLE_CATALOG]                   # il catalogo lo crea all'avvio


@pytest.mark.asyncio
async def test_ping_tells_who_you_are_and_only_integration_or_governance_may_call_it(env_int):
    c = env_int["client"]
    r = await c.get(f"{INT}/ping", headers=VIDEOPA)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "contract_version": 1, "tenant_id": TA, "subject": "videopa", "roles": ["INTEGRATION"],
                        "tenant_admin": False, "max_page": 200, "retention_category_configured": False}
    assert (await c.get(f"{INT}/ping", headers=ADMIN)).status_code == 200
    for who in (_h(TA, "reader"), _h(TA, "nobody")):                                   # sola lettura e senza ruolo: no
        assert (await c.get(f"{INT}/ping", headers=who)).status_code == 403
        assert (await c.get(snap(env_int["ids"]), headers=who)).status_code == 403


@pytest.mark.asyncio
async def test_service_account_reads_nothing_else_and_writes_nothing(env_int):
    c, ids = env_int["client"], env_int["ids"]
    for url in (f"{API}/processing-activities", f"{API}/processing-activities/{ids['proc_a']}", f"{API}/dpias", f"{API}/notices",
                f"{API}/parties", f"{API}/audit", f"{API}/breaches", f"{API}/tasks", f"{API}/tags",
                f"{API}/webhooks", f"{API}/outbox", f"{API}/policy/permissions"):
        assert (await c.get(url, headers=VIDEOPA)).status_code == 403, url
    found = await c.get(f"{API}/search?q=Trattamento", headers=VIDEOPA)             # la ricerca non ha un permesso proprio: filtra per modulo
    assert found.status_code == 200 and found.json()["total"] == 0 and found.json()["counts"] == {}
    r = await c.post(f"{API}/processing-activities", json={"title": "x", "organizational_unit_id": "unit-a"}, headers=VIDEOPA)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_tenant_override_cannot_open_integration_to_other_roles(env_int):
    c = env_int["client"]
    r = await c.get(f"{API}/policy/permissions", headers=ADMIN)
    version = r.json()["row_version"]
    put = await c.put(f"{API}/policy/permissions", json={"overrides": {"integration.view": ["OPERATOR"]}}, headers=ifm(version, ADMIN))
    assert put.status_code == 400                                                          # permesso non modificabile


# ================================================================ W2: snapshot

@pytest.mark.asyncio
async def test_snapshot_of_a_bare_processing_has_the_contract_shape(env_int):
    r = await env_int["client"].get(snap(env_int["ids"]), headers=VIDEOPA)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"processing_id", "code", "name", "status", "updated_at", "as_of", "dpia", "dpia_others", "notices",
                         "processors", "retention", "etag"}
    assert body["processing_id"] == env_int["ids"]["proc_a"] and body["name"] == "Trattamento A" and body["status"] == "DRAFT"
    assert body["code"].startswith("TR-")
    assert body["dpia"] is None and body["dpia_others"] == 0 and body["notices"] == [] and body["processors"] == []
    assert body["retention"] == {"days": None, "kind": "NOT_CONFIGURED", "rule_id": None, "rules_total": 0, "rules": []}
    assert r.headers["ETag"] == body["etag"]


@pytest.mark.asyncio
async def test_snapshot_gathers_dpia_notice_processor_and_retention(env_int):
    env, ids = env_int, env_int["ids"]
    pid = ids["proc_a"]
    old, new = datetime(2025, 3, 1, 9, 0), datetime(2026, 3, 1, 9, 0)
    notice = PrivacyNotice(id="n1", tenant_id=TA, code="IN-000002", title="Informativa", owner_unit_id="unit-a",
                           status=NoticeStatus.APPROVED, current_version_id="nv2")
    party = Party(id="p1", tenant_id=TA, code="F-000001", party_type=PartyType.PROCESSOR, legal_name="XYZ S.r.l.")
    await _add(
        env,
        Dpia(id="d-old", tenant_id=TA, code="DPIA-000001", processing_activity_id=pid, title="vecchia", status=DpiaStatus.APPROVED,
             approved_at=old, version_no=2),
        Dpia(id="d-new", tenant_id=TA, code="DPIA-000004", processing_activity_id=pid, title="nuova", status=DpiaStatus.APPROVED,
             approved_at=new, version_no=3, next_review_date=date(2027, 3, 1)),
        Dpia(id="d-draft", tenant_id=TA, code="DPIA-000005", processing_activity_id=pid, title="bozza", status=DpiaStatus.IN_PROGRESS),
        notice,
        PrivacyNoticeVersion(id="nv1", privacy_notice_id="n1", version_no=1, version_label="v1", effective_from=date(2025, 1, 1)),
        PrivacyNoticeVersion(id="nv2", privacy_notice_id="n1", version_no=2, version_label="v2", effective_from=date(2026, 4, 1),
                             published_url="https://ente.example/informativa"),
        party,
        ProcessorAgreement(id="ag1", tenant_id=TA, party_id="p1", status=AgreementStatus.SIGNED, expiry_date=date(2999, 12, 31)),
    )
    await _add(env, NoticeProcessingLink(privacy_notice_id="n1", processing_activity_id=pid),
               ProcessingProcessor(processing_activity_id=pid, party_id="p1", processor_agreement_id="ag1"),
               rule(pid, RetentionUnit.DAYS, 7, record_class="IMMAGINI"))
    await _set_category(env, "immagini")                                                   # il confronto ignora le maiuscole

    body = (await env["client"].get(snap(ids), headers=VIDEOPA)).json()
    assert body["dpia"] == {"id": "d-new", "code": "DPIA-000004", "version": 3, "status": "APPROVED",
                            "approved_at": "2026-03-01T09:00:00", "next_review_date": "2027-03-01"}
    assert body["dpia_others"] == 2
    assert body["notices"] == [{"id": "n1", "code": "IN-000002", "status": "APPROVED", "version": 2, "version_label": "v2",
                                "effective_from": "2026-04-01", "published_url": "https://ente.example/informativa"}]
    assert body["processors"] == [{"party_id": "p1", "name": "XYZ S.r.l.", "agreement": {
        "id": "ag1", "status": "SIGNED", "valid": True, "expiry_date": "2999-12-31"}}]
    assert body["retention"]["kind"] == "FIXED" and body["retention"]["days"] == 7 and body["retention"]["rules_total"] == 1
    assert body["retention"]["rules"][0]["record_class"] == "IMMAGINI" and body["retention"]["rule_id"] == body["retention"]["rules"][0]["id"]


@pytest.mark.asyncio
async def test_expired_or_unsigned_agreement_is_not_valid(env_int):
    env, pid = env_int, env_int["ids"]["proc_a"]
    await _add(env, Party(id="p1", tenant_id=TA, code="F-1", party_type=PartyType.PROCESSOR, legal_name="A"),
               Party(id="p2", tenant_id=TA, code="F-2", party_type=PartyType.PROCESSOR, legal_name="B"))
    await _add(env, ProcessorAgreement(id="ag-exp", tenant_id=TA, party_id="p1", status=AgreementStatus.SIGNED, expiry_date=date(2020, 1, 1)),
               ProcessorAgreement(id="ag-draft", tenant_id=TA, party_id="p2", status=AgreementStatus.DRAFT))
    await _add(env, ProcessingProcessor(processing_activity_id=pid, party_id="p1", processor_agreement_id="ag-exp"),
               ProcessingProcessor(processing_activity_id=pid, party_id="p2", processor_agreement_id="ag-draft"))
    processors = (await env["client"].get(snap(env["ids"]), headers=VIDEOPA)).json()["processors"]
    assert [(p["name"], p["agreement"]["valid"]) for p in processors] == [("A", False), ("B", False)]


# ---------------------------------------------------------------- conservazione (decisione A)

def test_months_and_years_are_calendar_not_30_or_365():
    jan31 = date(2026, 1, 31)
    assert rule_days(RetentionUnit.MONTHS, 1, jan31) == 28                                 # 31 gennaio + 1 mese = 28 febbraio
    assert rule_days(RetentionUnit.MONTHS, 1, date(2024, 1, 31)) == 29                     # anno bisestile
    assert rule_days(RetentionUnit.YEARS, 1, date(2026, 3, 1)) == 365
    assert rule_days(RetentionUnit.YEARS, 1, date(2027, 3, 1)) == 366                      # attraversa il 29 febbraio 2028
    assert rule_days(RetentionUnit.MONTHS, 3, date(2026, 1, 1)) == 90 and rule_days(RetentionUnit.DAYS, 7, jan31) == 7
    assert rule_days(RetentionUnit.EVENT_BASED, None, jan31) is None and rule_days(RetentionUnit.PERMANENT, None, jan31) is None
    assert rule_days(RetentionUnit.DAYS, None, jan31) is None


@pytest.mark.asyncio
async def test_retention_kinds_never_invent_a_number(env_int):
    env, pid, c = env_int, env_int["ids"]["proc_a"], env_int["client"]

    async def retention():
        return (await c.get(snap(env["ids"]), headers=VIDEOPA)).json()["retention"]

    await _add(env, rule(pid, RetentionUnit.YEARS, 10, record_class="ATTI"))
    assert (await retention())["kind"] == "NOT_CONFIGURED"                                 # nessuna categoria indicata
    await _set_category(env, "IMMAGINI")
    assert (await retention())["kind"] == "NO_RULE" and (await retention())["days"] is None

    await _add(env, rule(pid, RetentionUnit.EVENT_BASED, None, record_class="IMMAGINI"))
    block = await retention()
    assert (block["kind"], block["days"]) == ("EVENT_BASED", None)

    async with env["maker"]() as db:                                                       # la regola diventa permanente
        r = (await db.execute(RetentionRule.__table__.select().where(RetentionRule.record_class == "IMMAGINI"))).first()
        await db.execute(RetentionRule.__table__.update().where(RetentionRule.id == r.id).values(retention_unit=RetentionUnit.PERMANENT))
        await db.commit()
    assert (await retention())["kind"] == "PERMANENT"

    await _add(env, rule(pid, RetentionUnit.DAYS, 30, record_class="immagini"))            # due regole diverse per la stessa categoria
    block = await retention()
    assert (block["kind"], block["days"], block["rule_id"]) == ("AMBIGUOUS", None, None) and block["rules_total"] == 3


@pytest.mark.asyncio
async def test_two_identical_rules_are_not_ambiguous(env_int):
    env, pid = env_int, env_int["ids"]["proc_a"]
    await _add(env, rule(pid, RetentionUnit.DAYS, 7, record_class="IMMAGINI"), rule(pid, RetentionUnit.DAYS, 7, record_class="IMMAGINI"))
    await _set_category(env, "IMMAGINI")
    block = (await env["client"].get(snap(env["ids"]), headers=VIDEOPA)).json()["retention"]
    assert (block["kind"], block["days"], block["rule_id"]) == ("FIXED", 7, None)


@pytest.mark.asyncio
async def test_retention_month_rule_is_converted_from_the_request_date(env_int):
    env, pid = env_int, env_int["ids"]["proc_a"]
    await _add(env, rule(pid, RetentionUnit.MONTHS, 1, record_class="IMMAGINI"))
    await _set_category(env, "IMMAGINI")
    async with env["maker"]() as db:
        activity = await db.get(ProcessingActivity, pid)
        from backend.auth.privacypa_scope import VisibilityScope
        scope = VisibilityScope(user_id="x", unit_ids=None)
        feb = (await SnapshotService(db).build(TA, [activity], scope, now=datetime(2026, 1, 31, 10)))[0]
        mar = (await SnapshotService(db).build(TA, [activity], scope, now=datetime(2026, 2, 1, 10)))[0]
    assert feb["retention"]["days"] == 28 and mar["retention"]["days"] == 28              # 28 feb e 1 marzo
    async with env["maker"]() as db:
        activity = await db.get(ProcessingActivity, pid)
        long_month = (await SnapshotService(db).build(TA, [activity], scope, now=datetime(2026, 3, 1, 10)))[0]
    assert long_month["retention"]["days"] == 31


# ---------------------------------------------------------------- riservatezza e isolamento

@pytest.mark.asyncio
async def test_restricted_dpia_is_shown_as_restricted_without_details(env_int):
    env, pid = env_int, env_int["ids"]["proc_a"]
    await _add(env, Dpia(id="d-r", tenant_id=TA, code="DPIA-000009", processing_activity_id=pid, title="Titolo riservato",
                         status=DpiaStatus.APPROVED, approved_at=datetime(2026, 1, 1), confidentiality_level=ConfidentialityLevel.RESTRICTED))
    r = await env["client"].get(snap(env["ids"]), headers=VIDEOPA)
    assert r.json()["dpia"] == {"restricted": True}
    assert "Titolo riservato" not in r.text and "DPIA-000009" not in r.text and "d-r" not in r.text
    assert (await env["client"].get(snap(env["ids"]), headers=ADMIN)).json()["dpia"]["id"] == "d-r"     # l'amministratore la vede


@pytest.mark.asyncio
async def test_restricted_processing_is_not_found_for_the_service_account(env_int):
    env, pid = env_int, env_int["ids"]["proc_a"]
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).confidentiality_level = ConfidentialityLevel.RESTRICTED
        await db.commit()
    assert (await env["client"].get(snap(env["ids"]), headers=VIDEOPA)).status_code == 404
    listed = (await env["client"].get(f"{INT}/processing-activities", headers=VIDEOPA)).json()
    assert [i["processing_id"] for i in listed["items"]] == [env["ids"]["proc_b"]]


@pytest.mark.asyncio
async def test_service_account_with_unit_scope_sees_only_that_unit(env_int):
    env = env_int
    await env["grant"](TA, "videopa-a", R.INTEGRATION, ScopeType.UNIT, "unit-a")
    who = _h(TA, "videopa-a")
    assert (await env["client"].get(snap(env["ids"], "proc_a"), headers=who)).status_code == 200
    assert (await env["client"].get(snap(env["ids"], "proc_b"), headers=who)).status_code == 404
    items = (await env["client"].get(f"{INT}/processing-activities", headers=who)).json()["items"]
    assert [i["processing_id"] for i in items] == [env["ids"]["proc_a"]]


@pytest.mark.asyncio
async def test_another_tenant_is_not_found_and_never_appears(env_int):
    env = env_int
    c = env["client"]
    assert (await c.get(snap(env["ids"], "proc_x"), headers=VIDEOPA)).status_code == 404
    assert (await c.get(f"{INT}/processing-activities/inesistente/snapshot", headers=VIDEOPA)).status_code == 404
    items = (await c.get(f"{INT}/processing-activities", headers=VIDEOPA)).json()["items"]
    assert env["ids"]["proc_x"] not in {i["processing_id"] for i in items}
    await _add(env, rule(env["ids"]["proc_x"], RetentionUnit.DAYS, 1, record_class="IMMAGINI", tenant=TB),
               Dpia(id="d-x", tenant_id=TB, code="DPIA-000001", processing_activity_id=env["ids"]["proc_x"], title="altro ente"))
    assert "d-x" not in (await c.get(f"{INT}/processing-activities", headers=VIDEOPA)).text
    # Lo stesso account con lo stesso `sub` in un altro ente non ha ruolo: nessun accesso.
    assert (await c.get(snap(env["ids"], "proc_x"), headers=_h(TB, "videopa"))).status_code == 403


# ---------------------------------------------------------------- ETag e paginazione

@pytest.mark.asyncio
async def test_etag_supports_304_and_changes_with_the_content(env_int):
    env, pid, c = env_int, env_int["ids"]["proc_a"], env_int["client"]
    first = await c.get(snap(env["ids"]), headers=VIDEOPA)
    etag = first.headers["ETag"]
    again = await c.get(snap(env["ids"]), headers={**VIDEOPA, "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b"" and again.headers["ETag"] == etag
    assert (await c.get(snap(env["ids"]), headers={**VIDEOPA, "If-None-Match": f'W/{etag}, "altro"'})).status_code == 304
    assert (await c.get(snap(env["ids"]), headers={**VIDEOPA, "If-None-Match": "*"})).status_code == 304
    assert (await c.get(snap(env["ids"]), headers={**VIDEOPA, "If-None-Match": '"vecchio"'})).status_code == 200

    await _add(env, rule(pid, RetentionUnit.DAYS, 3, record_class="IMMAGINI"))
    changed = await c.get(snap(env["ids"]), headers={**VIDEOPA, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["ETag"] != etag and changed.json()["retention"]["rules_total"] == 1


def test_etag_ignores_as_of_only():
    a = {"x": 1, "as_of": "2026-01-01T00:00:00"}
    assert etag_of_snapshot(a) == etag_of_snapshot({**a, "as_of": "2030-01-01T00:00:00"})
    assert etag_of_snapshot(a) != etag_of_snapshot({**a, "x": 2})


@pytest.mark.asyncio
async def test_list_filters_by_updated_since_and_paginates(env_int):
    env, c = env_int, env_int["client"]
    everything = (await c.get(f"{INT}/processing-activities", headers=VIDEOPA)).json()
    assert everything["total"] == 2 and everything["limit"] == 200 and {"items", "as_of", "offset"} <= set(everything)
    assert all(i["etag"] and i["retention"]["kind"] == "NOT_CONFIGURED" for i in everything["items"])
    page = (await c.get(f"{INT}/processing-activities?limit=1&offset=1", headers=VIDEOPA)).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and {**page["items"][0], "as_of": 0} == {**everything["items"][1], "as_of": 0}

    later = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    assert (await c.get(f"{INT}/processing-activities", params={"updated_since": later}, headers=VIDEOPA)).json()["items"] == []
    early = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    assert (await c.get(f"{INT}/processing-activities", params={"updated_since": early}, headers=VIDEOPA)).json()["total"] == 2

    # Una regola di conservazione nuova sposta `updated_at` del solo trattamento interessato.
    async with env["maker"]() as db:
        for pid in (env["ids"]["proc_a"], env["ids"]["proc_b"]):
            row = await db.get(ProcessingActivity, pid)
            row.created_at = row.updated_at = datetime(2020, 1, 1)
        await db.commit()
    cutoff = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=5)
    await _add(env, rule(env["ids"]["proc_b"], RetentionUnit.DAYS, 5, record_class="IMMAGINI"))
    changed = (await c.get(f"{INT}/processing-activities", params={"updated_since": cutoff.isoformat()}, headers=VIDEOPA)).json()
    assert [i["processing_id"] for i in changed["items"]] == [env["ids"]["proc_b"]]

    only_draft = (await c.get(f"{INT}/processing-activities?status=ACTIVE", headers=VIDEOPA)).json()
    assert only_draft["total"] == 0
    assert (await c.get(f"{INT}/processing-activities?limit=201", headers=VIDEOPA)).status_code == 422
    assert (await c.get(f"{INT}/processing-activities?updated_since=ieri", headers=VIDEOPA)).status_code == 422


@pytest.mark.asyncio
async def test_page_of_200_snapshots_uses_grouped_queries(env_int):
    env, c = env_int, env_int["client"]
    async with env["maker"]() as db:
        base = await db.get(ProcessingActivity, env["ids"]["proc_a"])
        for n in range(210):
            db.add(ProcessingActivity(tenant_id=TA, code=f"TR-9{n:05d}", title=f"Bulk {n}", organizational_unit_id="unit-a",
                                      status=base.status, confidentiality_level=base.confidentiality_level))
        await db.commit()
    r = (await c.get(f"{INT}/processing-activities", headers=VIDEOPA)).json()
    assert r["total"] == 212 and len(r["items"]) == 200
    rest = (await c.get(f"{INT}/processing-activities?offset=200", headers=VIDEOPA)).json()
    assert len(rest["items"]) == 12
    assert len({i["processing_id"] for i in r["items"]} | {i["processing_id"] for i in rest["items"]}) == 212


# ---------------------------------------------------------------- parametri dell'ente

@pytest.mark.asyncio
async def test_retention_category_setting_is_admin_only_audited_and_validated(env_int):
    env = env_int
    c = env["client"]
    assert (await c.get(POLICY, headers=VIDEOPA)).status_code == 403                       # nemmeno l'account di servizio
    first = (await c.get(POLICY, headers=ADMIN)).json()
    assert first == {"row_version": 0, "values": {"retention_category": None, "external_events_retention_months": 24, "access_training_blocking": False}}
    assert (await c.put(POLICY, json={"retention_category": "IMMAGINI"}, headers=ifm(0, VIDEOPA))).status_code == 403
    assert (await c.put(POLICY, json={"retention_category": "IMMAGINI"}, headers=ADMIN)).status_code == 428      # manca If-Match
    saved = await _set_category(env, "  Immagini di videosorveglianza ")
    assert saved["values"] == {"retention_category": "Immagini di videosorveglianza", "external_events_retention_months": 24,
                               "access_training_blocking": False}
    assert (await c.put(POLICY, json={"retention_category": "x" * 101}, headers=ifm(saved["row_version"], ADMIN))).status_code == 422
    stale = await c.put(POLICY, json={"retention_category": "ALTRA"}, headers=ifm(first["row_version"] or 99, ADMIN))
    assert stale.status_code in (409, 412)
    cleared = await _set_category(env, "")
    assert cleared["values"]["retention_category"] is None
    ping = (await c.get(f"{INT}/ping", headers=VIDEOPA)).json()
    assert ping["retention_category_configured"] is False

    from sqlalchemy import select
    async with env["maker"]() as db:
        events = (await db.execute(select(AuditLog).where(AuditLog.event_type == "policy.integrations.updated"))).scalars().all()
    assert len(events) == 2
    assert "Immagini di videosorveglianza" in str(events[0].new_values)


# ---------------------------------------------------------------- dati di prova

def test_demo_seed_runs_and_prepares_the_videopa_scenarios(tmp_path):
    """Il seed di prova completo (`seed_demo_data.py`) gira su un database nuovo, due volte, e crea gli scenari del connettore."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp_path / 'seed.db').as_posix()}", "PYTHONIOENCODING": "utf-8"}
    first = subprocess.run([sys.executable, "seed_demo_data.py"], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert first.returncode == 0, first.stderr[-2000:]
    assert "errore" not in first.stdout.lower()
    assert "integrazioni:" in first.stdout and "svc-videopa" in first.stdout and "5 eventi ricevuti" in first.stdout
    second = subprocess.run([sys.executable, "seed_demo_data.py"], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert second.returncode == 0 and "integrazioni:" not in second.stdout          # idempotente
