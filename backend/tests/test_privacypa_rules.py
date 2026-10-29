"""Motore di regole (§38, §155): ciclo di vita dei finding, configurazione per ente, spiegazione e nuove regole."""
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from backend.models.privacypa_assets import Asset, AssetLifecycle
from backend.models.privacypa_catalog import DataCategoryCatalog, GdprDataClass
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_processing import ProcessingActivity, ProcessingStatus
from backend.models.privacypa_risk import RiskAssessment, RiskLevel
from backend.models.privacypa_rules import ComplianceFinding, FindingStatus, Severity
from backend.models.privacypa_tasks import PrivacyTask, TaskStatus
from backend.services.privacypa_processing_service import ProcessingService
from backend.tests.conftest import TA, TB, _h, ifm

API = "/api/privacypa"
FINDINGS = f"{API}/findings"
RULES = f"{API}/compliance/rules"
TODAY = date.today()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("op", R.OPERATOR, ScopeType.TENANT, None), ("reader", R.READ_ONLY, ScopeType.TENANT, None),
        ("op-a", R.OPERATOR, ScopeType.UNIT, "unit-a"), ("op-b", R.OPERATOR, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "op", "reader", "op-a", "op-b")} | {"b": _h(TB, "b-mgr")}


async def _approve(env, pid, **fields):
    async with env["maker"]() as db:
        svc = ProcessingService(db)
        if fields:
            await svc.update_activity(TA, pid, "seed", title="Trattamento", description="Descrizione",
                                      legal_basis_codes=["GDPR_6_1_E"], data_subject_ids=["subject-1"],
                                      data_category_ids=fields.pop("data_category_ids", ["category-1"]),
                                      organizational_unit_id=fields.pop("organizational_unit_id", None) or None, **fields)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.IN_REVIEW)
        await svc.transition_status(TA, pid, "seed", ProcessingStatus.APPROVED)
        await db.commit()


async def _list(env, who, **params):
    r = await env["client"].get(FINDINGS, params=params, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def _evaluate(env, who):
    r = await env["client"].post(f"{FINDINGS}/evaluate", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def _by_code(env, who, code, **params):
    return [f for f in await _list(env, who, code=code, **params)]


async def _set_status(env, who, finding, target, notes=None):
    return await env["client"].post(
        f"{FINDINGS}/{finding['id']}/status", json={"status": target, "notes": notes}, headers=ifm(finding["row_version"], who)
    )


# ---------------------------------------------------------------- ciclo di vita

@pytest.mark.asyncio
async def test_evaluation_opens_is_idempotent_and_resolves_automatically(env, w):
    pid = env["ids"]["proc_a"]
    await _approve(env, pid)
    first = await _evaluate(env, w["mgr"])
    assert first["opened"] >= 1 and first["evaluated_rules"] == 34
    again = await _evaluate(env, w["mgr"])
    assert again["opened"] == 0 and again["resolved"] == 0

    (found,) = await _by_code(env, w["mgr"], "RETENTION_MISSING", refresh="false")
    assert found["status"] == "OPEN" and found["entity_id"] == pid and found["rule_version"] == 1

    # la condizione viene meno: il finding si chiude da solo
    async with env["maker"]() as db:
        from backend.models.privacypa_retention import DisposalMethod, RetentionRule, RetentionUnit
        db.add(RetentionRule(tenant_id=TA, processing_activity_id=pid, retention_unit=RetentionUnit.PERMANENT,
                             retention_criterion="Archivio storico", disposal_method=DisposalMethod.ARCHIVAL))
        await db.commit()
    assert (await _evaluate(env, w["mgr"]))["resolved"] >= 1
    assert await _by_code(env, w["mgr"], "RETENTION_MISSING") == []
    (closed,) = await _by_code(env, w["mgr"], "RETENTION_MISSING", status="RESOLVED")
    assert closed["id"] == found["id"] and closed["resolution_notes"] == "Condizione non più rilevata" and closed["resolved_at"]
    assert closed["status_changed_by"] == "system"

    # se il problema si ripresenta è una nuova segnalazione, quella chiusa resta nello storico
    async with env["maker"]() as db:
        await db.execute(RetentionRule.__table__.delete())
        await db.commit()
    (reopened,) = await _by_code(env, w["mgr"], "RETENTION_MISSING")
    assert reopened["id"] != found["id"]
    assert len(await _by_code(env, w["mgr"], "RETENTION_MISSING", status="ALL")) == 2


@pytest.mark.asyncio
async def test_acknowledge_accept_and_reopen(env, w):
    await _approve(env, env["ids"]["proc_a"])
    (f,) = await _by_code(env, w["mgr"], "RETENTION_MISSING")

    acked = await _set_status(env, w["op"], f, "ACKNOWLEDGED")
    assert acked.status_code == 200 and acked.json()["status"] == "ACKNOWLEDGED" and acked.json()["status_changed_by"] == "op"
    assert len(await _by_code(env, w["mgr"], "RETENTION_MISSING")) == 1  # presa in carico: resta visibile

    no_notes = await _set_status(env, w["dpo"], acked.json(), "ACCEPTED")
    assert no_notes.status_code == 400 and "motivazione" in no_notes.json()["detail"]
    accepted = await _set_status(env, w["dpo"], acked.json(), "ACCEPTED", "Conservazione definita nel manuale di gestione")
    assert accepted.status_code == 200 and accepted.json()["resolution_notes"].startswith("Conservazione definita")
    # accettata: non è più tra quelle da trattare e non viene riaperta finché la condizione persiste
    assert await _by_code(env, w["mgr"], "RETENTION_MISSING") == []
    assert (await _evaluate(env, w["mgr"]))["opened"] == 0
    assert len(await _by_code(env, w["mgr"], "RETENTION_MISSING", status="ACCEPTED")) == 1

    reopened = await _set_status(env, w["dpo"], accepted.json(), "OPEN")
    assert reopened.status_code == 200 and reopened.json()["status"] == "OPEN"
    assert (await _set_status(env, w["dpo"], reopened.json(), "RESOLVED")).status_code == 400  # solo automatico
    assert (await _set_status(env, w["dpo"], reopened.json(), "OPEN")).status_code == 400


@pytest.mark.asyncio
async def test_status_change_uses_if_match_and_is_audited(env, w):
    await _approve(env, env["ids"]["proc_a"])
    (f,) = await _by_code(env, w["mgr"], "RETENTION_MISSING")
    url = f"{FINDINGS}/{f['id']}/status"
    assert (await env["client"].post(url, json={"status": "ACKNOWLEDGED"}, headers=w["mgr"])).status_code == 428
    assert (await env["client"].post(url, json={"status": "ACKNOWLEDGED"}, headers=ifm(99, w["mgr"]))).status_code == 409
    assert (await _set_status(env, w["mgr"], f, "ACKNOWLEDGED")).status_code == 200
    events = (await env["client"].get(f"{API}/audit", params={"entity_type": "compliance_finding"}, headers=w["mgr"])).json()
    assert {"finding.opened", "finding.acknowledged"} <= {e["event_type"] for e in events}


@pytest.mark.asyncio
async def test_permissions_and_scope_on_status_changes(env, w):
    ids = env["ids"]
    await _approve(env, ids["proc_a"])
    (f,) = await _by_code(env, w["mgr"], "RETENTION_MISSING")

    assert (await _set_status(env, w["reader"], f, "ACKNOWLEDGED")).status_code == 403
    assert (await _set_status(env, w["op-b"], f, "ACKNOWLEDGED")).status_code == 403   # struttura diversa
    assert (await _set_status(env, w["op"], f, "ACCEPTED", "ok")).status_code == 403    # accettare è decisione di governo
    assert (await _set_status(env, w["op-a"], f, "ACKNOWLEDGED")).status_code == 200
    assert (await env["client"].post(f"{FINDINGS}/{f['id']}/status", json={"status": "OPEN"}, headers=ifm(1, w["b"]))).status_code == 404

    # una segnalazione sull'anagrafica condivisa richiede visibilità sull'intero ente
    party = await env["client"].post(f"{API}/parties", json={"legal_name": "Cloud", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])
    await env["client"].post(f"{API}/parties/agreements", json={"party_id": party.json()["id"], "status": "SIGNED", "expiry_date": iso(-5)}, headers=w["mgr"])
    (shared,) = await _by_code(env, w["mgr"], "AGREEMENT_EXPIRED")
    assert (await _set_status(env, w["op-a"], shared, "ACKNOWLEDGED")).status_code == 403
    assert (await _set_status(env, w["op"], shared, "ACKNOWLEDGED")).status_code == 200


@pytest.mark.asyncio
async def test_findings_are_scoped_by_processing_and_isolated_by_tenant(env, w):
    ids = env["ids"]
    for pid in (ids["proc_a"], ids["proc_b"]):
        await _approve(env, pid)
    assert {f["entity_id"] for f in await _by_code(env, w["mgr"], "RETENTION_MISSING")} == {ids["proc_a"], ids["proc_b"]}
    assert {f["entity_id"] for f in await _by_code(env, w["op-a"], "RETENTION_MISSING")} == {ids["proc_a"]}
    assert await _list(env, w["b"]) == []
    other = (await _by_code(env, w["mgr"], "RETENTION_MISSING"))[0]
    assert (await env["client"].get(f"{FINDINGS}/{other['id']}", headers=w["b"])).status_code == 404
    hidden = next(f for f in await _by_code(env, w["mgr"], "RETENTION_MISSING") if f["entity_id"] == ids["proc_b"])
    assert (await env["client"].get(f"{FINDINGS}/{hidden['id']}", headers=w["op-a"])).status_code == 403


@pytest.mark.asyncio
async def test_active_key_prevents_duplicate_open_findings(env, w):
    await _approve(env, env["ids"]["proc_a"])
    (f,) = await _by_code(env, w["mgr"], "RETENTION_MISSING")
    async with env["maker"]() as db:
        original = await db.get(ComplianceFinding, f["id"])
        db.add(ComplianceFinding(
            tenant_id=TA, rule_id=original.rule_id, rule_code=original.rule_code, rule_version=1, severity=Severity.MEDIUM,
            condition="x", entity_type="processing_activity", entity_id=original.entity_id, fingerprint=original.fingerprint,
            active_key=original.active_key, message="dup",
        ))
        with pytest.raises(IntegrityError):
            await db.commit()


# ---------------------------------------------------------------- spiegazione e versione della regola (§155)

@pytest.mark.asyncio
async def test_why_and_rule_version_history(env, w):
    await _approve(env, env["ids"]["proc_a"])
    pid = env["ids"]["proc_a"]
    party = (await env["client"].post(f"{API}/parties", json={"legal_name": "Cloud", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])).json()
    await env["client"].post(f"{API}/parties/agreements", json={"party_id": party["id"], "status": "SIGNED", "expiry_date": iso(30)}, headers=w["mgr"])
    (f,) = await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING")
    assert f["condition"] == "Atto art. 28 firmato che scade entro 60 giorni." and f["rule_version"] == 1

    detail = (await env["client"].get(f"{FINDINGS}/{f['id']}", headers=w["reader"])).json()
    why = detail["why"]
    assert why["rule_name"] == "Atto art. 28 in scadenza" and why["params"] == {"horizon_days": 60} and why["rule_changed_since"] is False
    assert [h["event_type"] for h in detail["history"]] == ["finding.opened"]

    rule = next(r for r in (await env["client"].get(RULES, headers=w["reader"])).json() if r["code"] == "AGREEMENT_EXPIRING")
    changed = await env["client"].put(f"{RULES}/AGREEMENT_EXPIRING", json={"params": {"horizon_days": 90}}, headers=ifm(rule["version"], w["dpo"]))
    assert changed.status_code == 200 and changed.json()["version"] == 2

    # il finding storico conserva versione e condizione originarie
    detail = (await env["client"].get(f"{FINDINGS}/{f['id']}", headers=w["reader"])).json()
    assert detail["rule_version"] == 1 and detail["condition"].endswith("entro 60 giorni.")
    assert detail["why"]["rule_changed_since"] is True and detail["why"]["current_rule_version"] == 2
    assert detail["why"]["current_condition"].endswith("entro 90 giorni.")


# ---------------------------------------------------------------- configurazione delle regole

@pytest.mark.asyncio
async def test_rule_listing_and_update_rules(env, w):
    c = env["client"]
    rules = (await c.get(RULES, headers=w["reader"])).json()
    assert len(rules) == 34 and {r["version"] for r in rules} == {1} and all(r["enabled"] for r in rules)
    expiring = next(r for r in rules if r["code"] == "AGREEMENT_EXPIRING")
    assert expiring["default_params"] == {"horizon_days": 60} and expiring["default_severity"] == "MEDIUM"

    url, v = f"{RULES}/AGREEMENT_EXPIRING", expiring["version"]
    assert (await c.put(url, json={"enabled": False}, headers=w["dpo"])).status_code == 428
    assert (await c.put(url, json={"enabled": False}, headers=ifm(5, w["dpo"]))).status_code == 409
    assert (await c.put(url, json={"enabled": False}, headers=ifm(v, w["op"]))).status_code == 403
    assert (await c.put(url, json={"params": {"nope": 5}}, headers=ifm(v, w["dpo"]))).status_code == 400
    assert (await c.put(url, json={"params": {"horizon_days": 0}}, headers=ifm(v, w["dpo"]))).status_code == 400
    assert (await c.put(f"{RULES}/UNKNOWN", json={"enabled": False}, headers=ifm(1, w["dpo"]))).status_code == 404
    ok = await c.put(url, json={"severity": "HIGH", "params": {"horizon_days": 15}}, headers=ifm(v, w["dpo"]))
    assert ok.status_code == 200 and ok.json()["severity"] == "HIGH" and ok.json()["params"] == {"horizon_days": 15}
    events = (await c.get(f"{API}/audit", params={"entity_type": "compliance_rule"}, headers=w["mgr"])).json()
    assert [e["event_type"] for e in events] == ["rule.updated"]

    # le regole sono per ente
    assert next(r for r in (await c.get(RULES, headers=w["b"])).json() if r["code"] == "AGREEMENT_EXPIRING")["version"] == 1


@pytest.mark.asyncio
async def test_rule_parameters_severity_and_disabling_drive_findings(env, w):
    c = env["client"]
    party = (await c.post(f"{API}/parties", json={"legal_name": "Cloud", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])).json()
    await c.post(f"{API}/parties/agreements", json={"party_id": party["id"], "status": "SIGNED", "expiry_date": iso(30)}, headers=w["mgr"])
    async def current():
        return next(r for r in (await c.get(RULES, headers=w["mgr"])).json() if r["code"] == "AGREEMENT_EXPIRING")

    assert len(await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING")) == 1
    r = await current()
    await c.put(f"{RULES}/AGREEMENT_EXPIRING", json={"params": {"horizon_days": 10}}, headers=ifm(r["version"], w["dpo"]))
    assert await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING") == []  # fuori orizzonte: si chiude
    r = await current()
    await c.put(f"{RULES}/AGREEMENT_EXPIRING", json={"params": {"horizon_days": 45}, "severity": "HIGH"}, headers=ifm(r["version"], w["dpo"]))
    (again,) = await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING")
    assert again["severity"] == "HIGH" and again["rule_version"] == 3

    r = await current()
    await c.put(f"{RULES}/AGREEMENT_EXPIRING", json={"enabled": False}, headers=ifm(r["version"], w["dpo"]))
    assert await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING") == []
    closed = await _by_code(env, w["mgr"], "AGREEMENT_EXPIRING", status="RESOLVED")
    assert closed and closed[0]["resolution_notes"] == "Regola disabilitata"
    assert (await _evaluate(env, w["mgr"]))["evaluated_rules"] == 33


# ---------------------------------------------------------------- nuove regole

@pytest.mark.asyncio
async def test_risk_assessment_review_for_special_categories(env, w):
    pid = env["ids"]["proc_a"]
    async with env["maker"]() as db:
        special = DataCategoryCatalog(tenant_id=None, code="SAL", name="Salute", gdpr_class=GdprDataClass.SPECIAL_ART9)
        db.add(special)
        await db.flush()
        special_id = special.id
        await db.commit()
    await _approve(env, pid, data_category_ids=[special_id], organizational_unit_id="unit-a")
    (f,) = await _by_code(env, w["mgr"], "RISK_ASSESSMENT_REVIEW")
    assert f["entity_id"] == pid

    async with env["maker"]() as db:
        db.add(RiskAssessment(
            tenant_id=TA, code="VR-000001", entity_type="processing_activity", entity_id=pid, risk_id="risk-1",
            likelihood=2, impact=2, inherent_level=RiskLevel.MEDIUM, inherent_score=4, assessment_rationale="Valutato",
        ))
        await db.commit()
    assert await _by_code(env, w["mgr"], "RISK_ASSESSMENT_REVIEW") == []


@pytest.mark.asyncio
async def test_owner_and_review_due_rules(env, w):
    pid = env["ids"]["proc_a"]
    await _approve(env, pid, organizational_unit_id="unit-a")
    async with env["maker"]() as db:
        (await db.get(ProcessingActivity, pid)).next_review_date = TODAY - timedelta(days=3)      # l'approvazione ne calcola una nuova (§156): la data scade dopo
        db.add(Asset(tenant_id=TA, code="AS-000001", name="Sistema senza owner"))
        db.add(Asset(tenant_id=TA, code="AS-000002", name="Sistema con owner", owner_unit_id="unit-a"))
        await db.commit()

    (review,) = await _by_code(env, w["mgr"], "PROCESSING_REVIEW_DUE")
    assert review["entity_id"] == pid and review["due_date"] == iso(-3)
    assert [f["entity_code"] for f in await _by_code(env, w["mgr"], "ASSET_OWNER_MISSING")] == ["AS-000001"]
    assert [f["entity_id"] for f in await _by_code(env, w["mgr"], "PROCESSING_OWNER_MISSING")] == [pid]


@pytest.mark.asyncio
async def test_dismissed_asset_creates_the_checklist_once_and_closes_when_done(env, w):
    async with env["maker"]() as db:
        db.add(Asset(tenant_id=TA, code="AS-000009", name="Vecchio gestionale", owner_unit_id="unit-a",
                     lifecycle_status=AssetLifecycle.DISMISSED))
        await db.commit()

    (f,) = await _by_code(env, w["mgr"], "ASSET_DISMISSED_CHECKLIST")
    assert f["entity_code"] == "AS-000009" and f["params"]["open_tasks"] == 5
    await _evaluate(env, w["mgr"])
    tasks = (await env["client"].get(f"{API}/tasks", headers=w["mgr"])).json()
    checklist = [t for t in tasks if t["source_type"] == "ASSET"]
    assert len(checklist) == 5 and {t["assigned_unit_id"] for t in checklist} == {"unit-a"}  # creata una sola volta

    async with env["maker"]() as db:
        from sqlalchemy import update
        await db.execute(update(PrivacyTask).where(PrivacyTask.source_type == "ASSET").values(status=TaskStatus.COMPLETED))
        await db.commit()
    assert await _by_code(env, w["mgr"], "ASSET_DISMISSED_CHECKLIST") == []


@pytest.mark.asyncio
async def test_evaluate_endpoint_permissions_and_refresh_flag(env, w):
    assert (await env["client"].post(f"{FINDINGS}/evaluate", headers=w["reader"])).status_code == 403
    assert (await env["client"].post(f"{FINDINGS}/evaluate", headers=_h(TA, "stranger"))).status_code == 403
    await _approve(env, env["ids"]["proc_a"])
    assert await _by_code(env, w["mgr"], "RETENTION_MISSING", refresh="false") == []  # nulla di persistito finché non si valuta
    assert len(await _by_code(env, w["mgr"], "RETENTION_MISSING")) == 1
    assert (await _evaluate(env, w["op"]))["opened"] == 0
