"""Scadenze relative configurabili (§156): prossima revisione dall'approvazione, in mesi di calendario e per ente."""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from backend.models.privacypa_assessments import Dpia, DpiaStatus
from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.models.privacypa_processing import ProcessingActivity, ProcessingStatus
from backend.services.privacypa_deadlines import add_months, add_months_to_date, renewed_review_date
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
POLICY = f"{API}/policy/deadlines"
ADMIN, ADMIN_B = _h(TA, "root", "admin"), _h(TB, "root", "admin")
TODAY = date.today()


# ================================================================ calcolo

def test_months_are_calendar_months_not_thirty_days_or_365_day_years():
    assert add_months_to_date(date(2026, 1, 31), 1) == date(2026, 2, 28)                # il giorno si riporta a fine mese
    assert add_months_to_date(date(2028, 1, 31), 1) == date(2028, 2, 29)                # anno bisestile
    assert add_months_to_date(date(2028, 2, 29), 12) == date(2029, 2, 28)               # 12 mesi non sono 365 giorni
    assert add_months_to_date(date(2027, 3, 15), 12) == date(2028, 3, 15)               # anche attraverso il 29 febbraio
    assert add_months_to_date(date(2026, 11, 30), 3) == date(2027, 2, 28)               # cambio d'anno
    assert add_months_to_date(date(2026, 5, 10), 0) == date(2026, 5, 10)
    assert add_months(datetime(2026, 1, 31, 14, 30), 1) == datetime(2026, 2, 28, 14, 30)   # l'ora si conserva


def test_renewal_keeps_future_dates_and_disabled_calculation():
    approved = date(2026, 6, 15)
    assert renewed_review_date(None, approved, 12) == date(2027, 6, 15)
    assert renewed_review_date(date(2026, 1, 1), approved, 12) == date(2027, 6, 15)     # ciclo scaduto: si rinnova
    assert renewed_review_date(date(2026, 6, 15), approved, 6) == date(2026, 12, 15)    # scade oggi: si rinnova
    assert renewed_review_date(date(2027, 1, 1), approved, 12) == date(2027, 1, 1)      # data futura già indicata: non si sposta
    assert renewed_review_date(None, approved, 0) is None and renewed_review_date(date(2026, 1, 1), approved, 0) == date(2026, 1, 1)


# ================================================================ configurazione per ente

@pytest.mark.asyncio
async def test_deadlines_policy_has_review_months_with_limits_and_audit(env):
    c = env["client"]
    got = (await c.get(POLICY, headers=ADMIN)).json()
    assert got["values"] == {"dsr_base_months": 1, "dsr_extension_months": 2, "processing_review_months": 12, "dpia_review_months": 12}
    for bad in ({"processing_review_months": 61}, {"dpia_review_months": -1}, {"processing_review_months": 1.5}):
        assert (await c.put(POLICY, json=bad, headers=ifm(0, ADMIN))).status_code in (400, 422), bad
    assert (await c.put(POLICY, json={"processing_review_months": 6}, headers=_h(TA, "nobody"))).status_code in (403, 428)
    saved = await c.put(POLICY, json={"processing_review_months": 6, "dpia_review_months": 0}, headers=ifm(0, ADMIN))
    assert saved.status_code == 200 and saved.json()["values"]["processing_review_months"] == 6 and saved.json()["values"]["dpia_review_months"] == 0
    assert saved.json()["values"]["dsr_base_months"] == 1                                # il resto resta com'era
    assert (await c.get(POLICY, headers=ADMIN_B)).json()["values"]["processing_review_months"] == 12      # gli altri enti non cambiano
    async with env["maker"]() as db:
        event = (await db.execute(select(AuditLog).where(AuditLog.event_type == "policy.deadlines.updated"))).scalars().one()
    assert event.new_values["deadlines"]["processing_review_months"] == 6 and event.old_values["deadlines"]["processing_review_months"] == 12


# ================================================================ trattamenti

async def _approve(env, pid, tenant=TA):
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        await svc.transition_status(tenant, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(tenant, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()


async def _processing(env, pid):
    async with env["maker"]() as db:
        return await db.get(ProcessingActivity, pid)


async def _set(env, model, row_id, **values):
    async with env["maker"]() as db:
        row = await db.get(model, row_id)
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


@pytest.mark.asyncio
async def test_approving_a_processing_sets_the_next_review_from_the_tenant_policy(env):
    c, pid = env["client"], env["ids"]["proc_a"]
    await _approve(env, pid)
    row = await _processing(env, pid)
    assert row.next_review_date == add_months_to_date(TODAY, 12) and row.last_review_date == TODAY
    # l'evento pubblicato porta la data calcolata, utile a chi reagisce alle scadenze
    events = [e for e in (await c.get(f"{API}/outbox", headers=ADMIN)).json() if e["event_type"] == "processing_activity.status.approved"]
    assert events[0]["payload"]["data"] == {"status": "APPROVED", "next_review_date": add_months_to_date(TODAY, 12).isoformat()}


@pytest.mark.asyncio
async def test_processing_review_follows_custom_months_manual_dates_and_the_off_switch(env):
    c, pid = env["client"], env["ids"]["proc_a"]
    assert (await c.put(POLICY, json={"processing_review_months": 6}, headers=ifm(0, ADMIN))).status_code == 200
    await _set(env, ProcessingActivity, pid, next_review_date=date(2030, 1, 1))                # data futura inserita a mano
    await _approve(env, pid)
    row = await _processing(env, pid)
    assert row.next_review_date == date(2030, 1, 1) and row.last_review_date is None            # non si sposta

    # ciclo scaduto: dopo una nuova approvazione si rinnova, con i mesi dell'ente
    await _set(env, ProcessingActivity, pid, status=ProcessingStatus.ACTIVE, next_review_date=date(2020, 1, 1))
    async with env["maker"]() as db:
        await ProcessingService(db).transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await ProcessingService(db).transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()
    row = await _processing(env, pid)
    assert row.next_review_date == add_months_to_date(TODAY, 6) and row.last_review_date == TODAY

    # calcolo disattivato: la data non si tocca
    version = (await c.get(POLICY, headers=ADMIN)).json()["row_version"]
    assert (await c.put(POLICY, json={"processing_review_months": 0}, headers=ifm(version, ADMIN))).status_code == 200
    other = env["ids"]["proc_b"]
    await _approve(env, other)
    assert (await _processing(env, other)).next_review_date is None


@pytest.mark.asyncio
async def test_other_tenants_keep_their_own_defaults(env):
    assert (await env["client"].put(POLICY, json={"processing_review_months": 3}, headers=ifm(0, ADMIN))).status_code == 200
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        await svc.transition_status(TB, env["ids"]["proc_x"], "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TB, env["ids"]["proc_x"], "seed", ProcessingStatus.APPROVED)
        await db.commit()
    assert (await _processing(env, env["ids"]["proc_x"])).next_review_date == add_months_to_date(TODAY, 12)


# ================================================================ DPIA

async def _dpia(env, who="mgr"):
    await env["grant"](TA, who, R.PRIVACY_MANAGER)
    r = await env["client"].post(f"{API}/dpias", json={"title": "DPIA", "processing_activity_id": env["ids"]["proc_a"], "controller_decision": "Approvata"}, headers=_h(TA, who))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _approve_dpia(env, dpia_id):
    async with env["maker"]() as db:
        await DpiaService(db).transition(TA, dpia_id, "seed", DpiaStatus.APPROVED)
        await db.commit()
        return await db.get(Dpia, dpia_id)


@pytest.mark.asyncio
async def test_approving_a_dpia_sets_the_next_review_and_a_review_cycle_renews_it(env):
    dpia_id = await _dpia(env)
    await _set(env, Dpia, dpia_id, status=DpiaStatus.AWAITING_DECISION)
    approved = await _approve_dpia(env, dpia_id)
    assert approved.next_review_date == add_months_to_date(approved.approved_at.date(), 12)

    # una revisione scaduta si chiude con una nuova approvazione, che rinnova la data
    await _set(env, Dpia, dpia_id, status=DpiaStatus.TO_REVIEW, next_review_date=date(2020, 1, 1))
    renewed = await _approve_dpia(env, dpia_id)
    assert renewed.next_review_date == add_months_to_date(renewed.approved_at.date(), 12)

    # data futura inserita a mano: resta
    await _set(env, Dpia, dpia_id, status=DpiaStatus.TO_REVIEW, next_review_date=date(2031, 5, 5))
    assert (await _approve_dpia(env, dpia_id)).next_review_date == date(2031, 5, 5)


@pytest.mark.asyncio
async def test_dpia_review_can_be_switched_off(env):
    assert (await env["client"].put(POLICY, json={"dpia_review_months": 0}, headers=ifm(0, ADMIN))).status_code == 200
    dpia_id = await _dpia(env)
    await _set(env, Dpia, dpia_id, status=DpiaStatus.AWAITING_DECISION)
    assert (await _approve_dpia(env, dpia_id)).next_review_date is None
