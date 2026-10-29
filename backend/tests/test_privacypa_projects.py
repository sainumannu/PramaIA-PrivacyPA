"""Privacy by design: nuovi progetti (§30)."""
import json
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models.privacypa_assessments import Dpia, DpiaStatus
from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_projects import Project
from backend.services.privacypa_compliance_service import add_months
from backend.tests.conftest import TA, TB, _h, ifm, version_of

API = "/api/privacypa"
PR, FINDINGS, OUTBOX = f"{API}/projects", f"{API}/findings", f"{API}/outbox"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


@pytest_asyncio.fixture
async def w(env):
    grant = env["grant"]
    for user, role, scope, unit in (
        ("mgr", R.PRIVACY_MANAGER, ScopeType.TENANT, None), ("dpo", R.DPO, ScopeType.TENANT, None),
        ("ict", R.ICT, ScopeType.TENANT, None), ("op", R.OPERATOR, ScopeType.TENANT, None),
        ("reader", R.READ_ONLY, ScopeType.TENANT, None), ("aud", R.AUDITOR, ScopeType.TENANT, None),
        ("ref-a", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-a"), ("ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b"),
    ):
        await grant(TA, user, role, scope, unit)
    await grant(TB, "b-mgr", R.PRIVACY_MANAGER)
    return {k: _h(TA, k) for k in ("mgr", "dpo", "ict", "op", "reader", "aud", "ref-a", "ref-b")} | {"b": _h(TB, "b-mgr")}


def _body(**override):
    return {"title": "Sportello digitale", "description": "Nuovo sportello online per i servizi demografici",
            "proposing_unit_id": "unit-a", "project_owner_id": "ref-a", **override}


async def _create(env, who, **override):
    r = await env["client"].post(PR, json=_body(**override), headers=who)
    assert r.status_code == 201, r.text
    return r.json()


async def _get(env, project_id, who=ADMIN):
    r = await env["client"].get(f"{PR}/{project_id}", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


async def _post(env, project_id, suffix, who, json_body=None):
    c = env["client"]
    version = (await _get(env, project_id))["row_version"]
    return await c.post(f"{PR}/{project_id}/{suffix}", json=json_body or {}, headers=ifm(version, who))


async def _review(env, project_id, who, outcome="APPROVED", notes=None, **extra):
    return await _post(env, project_id, "reviews", who, {"outcome": outcome, "notes": notes, **extra})


async def _to_status(env, w, project_id, target):
    """Porta un progetto senza caratteristiche di rischio fino allo stato indicato."""
    steps = [("submit", None), ("reviews", "mgr"), ("reviews", "ict"), ("reviews", "mgr")]
    for suffix, who in steps:
        current = (await _get(env, project_id))["status"]
        if current == target:
            return
        r = await _post(env, project_id, suffix, w[who] if who else w["ref-a"], {"outcome": "APPROVED"} if who else None)
        assert r.status_code == 200, (suffix, r.text)
    assert (await _get(env, project_id))["status"] == target


# ================================================================ creazione, valutazione, scope

@pytest.mark.asyncio
async def test_project_is_created_assessed_and_scoped(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    assert p["code"] == "PR-000001" and p["status"] == "PROPOSAL" and p["cycle"] == 1 and p["privacy_review_required"] is True
    assert p["dpia_screening_required"] is False and p["assessment"]["criteria"] == []
    assert (await _create(env, w["ref-a"], uses_cloud=True))["dpia_screening_required"] is False     # il cloud da solo non basta

    risky = await _create(env, w["ref-a"], uses_ai=True, minors=True)
    assert risky["dpia_screening_required"] is True and risky["assessment"]["dpo_review_required"] is True
    assert risky["assessment"]["criteria"] == ["uses_ai", "minors"]

    assert (await c.post(PR, json=_body(proposing_unit_id="unit-b"), headers=w["ref-a"])).status_code == 403     # altra struttura
    assert (await c.post(PR, json=_body(proposing_unit_id="unit-x"), headers=w["mgr"])).status_code == 400
    assert (await c.post(PR, json=_body(supplier_id="nessuno"), headers=w["mgr"])).status_code == 400
    assert (await c.post(PR, json=_body(title=" "), headers=w["mgr"])).status_code == 400
    assert (await c.post(PR, json=_body(project_owner_id=""), headers=w["mgr"])).status_code == 422
    for who in ("reader", "aud"):
        assert (await c.post(PR, json=_body(), headers=w[who])).status_code == 403
    assert (await c.post(PR, json=_body(), headers=w["op"])).status_code == 201

    # lettura: struttura del progetto, oppure responsabile/autore
    assert len((await c.get(PR, headers=w["ref-a"])).json()) == 4 and (await c.get(PR, headers=w["ref-b"])).json() == []
    assert len((await c.get(PR, headers=w["mgr"])).json()) == 4 and (await c.get(PR, headers=w["b"])).json() == []
    assert (await c.get(f"{PR}/{p['id']}", headers=w["ref-b"])).status_code == 403
    assert (await c.get(f"{PR}/{p['id']}", headers=w["b"])).status_code == 404
    assert len((await c.get(PR, params={"mine": "true"}, headers=w["ref-a"])).json()) == 4
    assert len((await c.get(PR, params={"status": "ICT_REVIEW"}, headers=w["mgr"])).json()) == 0


@pytest.mark.asyncio
async def test_update_is_versioned_recomputes_the_assessment_and_only_in_proposal(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    url = f"{PR}/{p['id']}"
    body = _body(special_data=True)
    assert (await c.patch(url, json=body, headers=w["ref-a"])).status_code == 428
    assert (await c.patch(url, json=body, headers=ifm(p["row_version"] + 3, w["ref-a"]))).status_code == 409
    assert (await c.patch(url, json=body, headers=ifm(p["row_version"], w["ref-b"]))).status_code == 403
    changed = await c.patch(url, json=body, headers=ifm(p["row_version"], w["ref-a"]))
    assert changed.status_code == 200 and changed.json()["dpia_screening_required"] is True
    back = await c.patch(url, json=_body(), headers=ifm(changed.json()["row_version"], w["ref-a"]))
    assert back.json()["dpia_screening_required"] is False

    assert (await _post(env, p["id"], "submit", w["ref-a"])).status_code == 200
    late = await c.patch(url, json=_body(title="Altro"), headers=ifm(await version_of(c, url), w["ref-a"]))
    assert late.status_code == 400                                                       # in revisione non si modifica


@pytest.mark.asyncio
async def test_submit_requires_a_description_and_only_pristine_proposals_are_deleted(env, w):
    c = env["client"]
    empty = await _create(env, w["ref-a"], description=None)
    assert (await _post(env, empty["id"], "submit", w["ref-a"])).status_code == 400
    assert (await c.delete(f"{PR}/{empty['id']}", headers=w["ref-a"])).status_code == 403      # cancellare è del governo
    assert (await c.delete(f"{PR}/{empty['id']}", headers=w["mgr"])).status_code == 204
    assert (await c.get(f"{PR}/{empty['id']}", headers=w["mgr"])).status_code == 404

    sent = await _create(env, w["ref-a"])
    await _post(env, sent["id"], "submit", w["ref-a"])
    assert (await c.delete(f"{PR}/{sent['id']}", headers=w["mgr"])).status_code == 400


# ================================================================ workflow

@pytest.mark.asyncio
async def test_project_without_risk_skips_dpo_and_dpia(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    assert (await _post(env, p["id"], "reviews", w["mgr"], {"outcome": "APPROVED"})).status_code == 400          # non è in revisione
    assert (await _post(env, p["id"], "submit", w["ref-b"])).status_code == 403                                  # altra struttura
    assert (await _post(env, p["id"], "submit", w["ref-a"])).status_code == 200

    # ogni fase richiede il permesso della fase
    assert (await _review(env, p["id"], w["ict"])).status_code == 403
    assert (await _review(env, p["id"], w["ref-b"])).status_code == 403
    assert (await _review(env, p["id"], w["ref-a"])).status_code == 403          # neppure il responsabile, che non ha il permesso
    ok = await _review(env, p["id"], w["mgr"])
    assert ok.status_code == 200 and ok.json()["status"] == "ICT_REVIEW"
    assert (await _review(env, p["id"], w["mgr"])).status_code == 403
    ict = await _review(env, p["id"], w["ict"])
    assert ict.status_code == 200 and ict.json()["status"] == "APPROVAL"       # niente DPO né DPIA

    detail = await _get(env, p["id"])
    assert [(r["stage"], r["outcome"], r["reviewer_user_id"]) for r in detail["reviews"]] == [("PRIVACY", "APPROVED", "mgr"), ("ICT", "APPROVED", "ict")]
    assert (await _post(env, p["id"], "advance", w["mgr"])).status_code == 400                                   # dall'approvazione avanza una revisione


@pytest.mark.asyncio
async def test_risky_project_goes_through_dpo_dpia_approval_go_live_and_closure(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"], uses_ai=True, profiles_people=True)
    await _post(env, p["id"], "submit", w["ref-a"])
    await _review(env, p["id"], w["mgr"])
    assert (await _review(env, p["id"], w["ict"])).json()["status"] == "DPO_REVIEW"

    # la revisione del DPO non si delega: né al governo, né a un amministratore
    for who in (w["mgr"], w["ict"], ADMIN):
        assert (await _review(env, p["id"], who)).status_code == 403
    assert (await _review(env, p["id"], w["dpo"])).json()["status"] == "DPIA"

    # DPIA: serve lo screening, creato dal progetto e prefissato dalle caratteristiche dichiarate
    assert (await _post(env, p["id"], "advance", w["mgr"])).status_code == 400
    assert (await _post(env, p["id"], "dpia", w["reader"])).status_code == 403
    created = await _post(env, p["id"], "dpia", w["ref-a"])
    assert created.status_code == 201, created.text
    dpia_id = created.json()["dpia_id"]
    assert (await _post(env, p["id"], "dpia", w["ref-a"])).status_code == 400
    dpia = (await c.get(f"{API}/dpias/{dpia_id}", headers=w["mgr"])).json()
    assert dpia["processing_activity_id"] and dpia["engine_suggestion"] == "PROCEED"
    answered = {a["criterion_code"] for a in dpia["screening"] if a["answer"] is True}
    assert answered == {"INNOVATIVE_TECH", "SCORING"}
    assert (await _post(env, p["id"], "advance", w["mgr"])).status_code == 400                                   # screening non concluso

    async with env["maker"]() as db:                                                                              # lo screening esclude la DPIA
        row = await db.get(Dpia, dpia_id)
        row.status = DpiaStatus.NOT_REQUIRED
        await db.commit()
    assert (await _post(env, p["id"], "advance", w["ref-a"])).status_code == 200
    assert (await _get(env, p["id"]))["status"] == "APPROVAL"

    # approvazione: il responsabile non decide sul proprio progetto
    async with env["maker"]() as db:
        proj = await db.get(Project, p["id"])
        proj.project_owner_id = "mgr"
        await db.commit()
    seg = await _review(env, p["id"], w["mgr"])
    assert seg.status_code == 403
    async with env["maker"]() as db:
        proj = await db.get(Project, p["id"])
        proj.project_owner_id = "ref-a"
        await db.commit()
    assert (await _review(env, p["id"], w["mgr"], go_live_date=(TODAY + timedelta(days=900)).isoformat())).status_code == 400
    assert (await _review(env, p["id"], w["mgr"], go_live_date=(TODAY + timedelta(days=10)).isoformat())).status_code == 200
    live = await _get(env, p["id"])
    assert live["status"] == "GO_LIVE" and live["go_live_date"] == (TODAY + timedelta(days=10)).isoformat()
    assert live["post_review_due_date"] == add_months(datetime.combine(TODAY + timedelta(days=10), datetime.min.time()), 6).date().isoformat()

    early = await _post(env, p["id"], "advance", w["ref-a"])
    assert early.status_code == 400                                                                               # il go-live è futuro
    async with env["maker"]() as db:
        proj = await db.get(Project, p["id"])
        proj.go_live_date = TODAY
        await db.commit()
    assert (await _post(env, p["id"], "advance", w["ref-a"])).status_code == 200
    assert (await _review(env, p["id"], w["mgr"], "REJECTED", "no")).status_code == 400                           # in esercizio non si respinge
    findings = await _review(env, p["id"], w["mgr"], "CHANGES_REQUESTED", "Aggiornare l'informativa")
    assert findings.status_code == 200 and findings.json()["status"] == "POST_REVIEW"                            # rilievo registrato, resta lì
    closed = await _review(env, p["id"], w["mgr"])
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED" and closed.json()["closed_at"]
    assert (await _post(env, p["id"], "cancel", w["mgr"], {"notes": "x"})).status_code == 400                    # già chiuso
    assert [r["stage"] for r in (await _get(env, p["id"]))["reviews"]] == ["PRIVACY", "ICT", "DPO", "APPROVAL", "POST", "POST"]


@pytest.mark.asyncio
async def test_changes_requested_reopens_the_proposal_and_rejection_is_final(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    await _post(env, p["id"], "submit", w["ref-a"])
    assert (await _review(env, p["id"], w["mgr"], "CHANGES_REQUESTED")).status_code == 400                        # serve la motivazione
    back = await _review(env, p["id"], w["mgr"], "CHANGES_REQUESTED", "Precisare la base giuridica")
    assert back.status_code == 200 and back.json()["status"] == "PROPOSAL" and back.json()["cycle"] == 2
    edited = await c.patch(f"{PR}/{p['id']}", json=_body(description="Descrizione più precisa"), headers=ifm(back.json()["row_version"], w["ref-a"]))
    assert edited.status_code == 200
    await _post(env, p["id"], "submit", w["ref-a"])
    await _review(env, p["id"], w["mgr"])
    rejected = await _review(env, p["id"], w["ict"], "REJECTED", "Sistema non compatibile con l'infrastruttura")
    assert rejected.json()["status"] == "REJECTED"
    history = (await _get(env, p["id"]))["reviews"]
    assert [(r["cycle"], r["stage"], r["outcome"]) for r in history] == [(1, "PRIVACY", "CHANGES_REQUESTED"), (2, "PRIVACY", "APPROVED"), (2, "ICT", "REJECTED")]
    assert (await _post(env, p["id"], "submit", w["ref-a"])).status_code == 400

    other = await _create(env, w["ref-a"])
    await _post(env, other["id"], "submit", w["ref-a"])
    assert (await _post(env, other["id"], "cancel", w["ref-a"], {"notes": ""})).status_code == 422
    assert (await _post(env, other["id"], "cancel", w["ref-b"], {"notes": "no"})).status_code == 403
    cancelled = await _post(env, other["id"], "cancel", w["ref-a"], {"notes": "Progetto sospeso dall'amministrazione"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_reviews_are_versioned_and_audit_and_outbox_carry_no_notes(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    await _post(env, p["id"], "submit", w["ref-a"])
    url = f"{PR}/{p['id']}/reviews"
    assert (await c.post(url, json={"outcome": "APPROVED"}, headers=w["mgr"])).status_code == 428
    assert (await c.post(url, json={"outcome": "APPROVED"}, headers=ifm(99, w["mgr"]))).status_code == 409
    secret = "Contiene il nome di una persona"
    assert (await _review(env, p["id"], w["mgr"], "CHANGES_REQUESTED", secret)).status_code == 200

    events = (await c.get(OUTBOX, headers=ADMIN)).json()
    types = {e["event_type"] for e in events}
    assert {"project.status.privacy_review", "project.review", "project.status.proposal"} <= types
    assert secret not in json.dumps(events)
    async with env["maker"]() as db:
        from backend.models.privacypa_audit import AuditLog

        logs = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "project"))).scalars().all()
    assert secret not in json.dumps([log.new_values for log in logs]) and len(logs) >= 4


# ================================================================ generazione degli elementi

async def _approved_project(env, w, **override):
    """Progetto senza rischio già approvato (fase APPROVAL), con un fornitore responsabile."""
    c = env["client"]
    party = (await c.post(f"{API}/parties", json={"legal_name": "Gestionale Srl", "party_type": "PROCESSOR", "tax_id": "01234567897", "processor_profile": {"appointment_end": "2099-12-31"}}, headers=w["mgr"])).json()
    p = await _create(env, w["ref-a"], supplier_id=party["id"], uses_cloud=True, **override)
    await _to_status(env, w, p["id"], "APPROVAL")
    return p, party


@pytest.mark.asyncio
async def test_generation_creates_draft_items_once_and_links_them(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    assert (await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["mgr"])).status_code == 400            # troppo presto
    p, party = await _approved_project(env, w)
    assert (await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["ref-a"])).status_code == 403
    assert (await c.post(f"{PR}/{p['id']}/generate", json={"targets": ["dpia"]}, headers=w["mgr"])).status_code == 400
    assert (await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["ref-b"])).status_code == 403

    r = await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["mgr"])
    assert r.status_code == 201, r.text
    made = r.json()["created"]
    assert set(made) >= {"processing", "asset", "contract", "agreement", "notice", "task:processing", "task:asset", "task:contract",
                         "task:agreement", "task:notice", "task:measures", "task:transfers"}
    assert "dpia" not in made and r.json()["existing"] == {}

    proc = (await c.get(f"{API}/processing-activities/{made['processing']}", headers=w["mgr"])).json()
    assert proc["status"] == "DRAFT" and proc["organizational_unit_id"] == "unit-a" and proc["owner_user_id"] == "ref-a"
    assert [a["asset_id"] for a in proc["assets"]] == [made["asset"]]
    assert [x["party_id"] for x in proc["processors"]] == [party["id"]]
    assert proc["processors"][0]["contract_id"] == made["contract"] and proc["processors"][0]["processor_agreement_id"] == made["agreement"]

    asset = (await c.get(f"{API}/assets/{made['asset']}", headers=w["mgr"])).json()
    assert asset["owner_unit_id"] == "unit-a" and asset["supplier_id"] == party["id"] and asset["cloud_service"] is True
    assert (await c.get(f"{API}/contracts/{made['contract']}", headers=w["mgr"])).json()["status"] == "DRAFT"
    notice = (await c.get(f"{API}/notices/{made['notice']}", headers=w["mgr"])).json()
    assert notice["status"] == "DRAFT" and notice["owner_unit_id"] == "unit-a"

    tasks = (await c.get(f"{API}/tasks", headers=w["mgr"])).json()
    mine = [t for t in tasks if t["source_type"] == "PROJECT" and t["source_id"] == p["id"]]
    assert len(mine) == 7 and {t["assigned_user_id"] for t in mine} == {"ref-a"} and {t["assigned_unit_id"] for t in mine} == {"unit-a"}

    # generare di nuovo non duplica né tocca nulla
    again = await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["mgr"])
    assert again.status_code == 201 and again.json()["created"] == {} and again.json()["existing"]["processing"] == made["processing"]
    assert len([t for t in (await c.get(f"{API}/tasks", headers=w["mgr"])).json() if t["source_id"] == p["id"]]) == 7
    kinds = [a["entity_type"] for a in (await _get(env, p["id"]))["artifacts"]]
    assert kinds.count("processing") == 1 and kinds.count("asset") == 1 and kinds.count("notice") == 1

    events = {e["event_type"] for e in (await c.get(OUTBOX, headers=ADMIN)).json()}
    assert "project.artifacts.generated" in events


@pytest.mark.asyncio
async def test_generation_without_supplier_and_with_a_target_filter(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    await _to_status(env, w, p["id"], "APPROVAL")
    only = await c.post(f"{PR}/{p['id']}/generate", json={"targets": ["asset"]}, headers=w["mgr"])
    assert only.status_code == 201, only.text
    assert {"processing", "asset"} <= set(only.json()["created"]) and "notice" not in only.json()["created"]      # l'asset richiede il trattamento
    rest = await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["mgr"])
    made = rest.json()["created"]
    assert "notice" in made and "contract" not in made and "agreement" not in made and "task:transfers" not in made
    assert set(rest.json()["existing"]) == {"processing", "asset"}
    assert {"task:processing", "task:asset", "task:notice", "task:measures"} <= set(made)


@pytest.mark.asyncio
async def test_generation_needs_the_permission_to_create_each_item(env, w):
    c = env["client"]
    p, _ = await _approved_project(env, w)
    # un ente può togliere la creazione dei sistemi a un ruolo: chi genera deve comunque poterli creare
    overrides = {"overrides": {"asset.create": ["OPERATOR"], "project.generate": ["PRIVACY_MANAGER", "PRIVACY_REFERENT"]}}
    put = await c.put(f"{API}/policy/permissions", json=overrides, headers=ifm(0, ADMIN))
    assert put.status_code == 200, put.text
    assert (await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["ref-a"])).status_code == 403
    assert (await c.post(f"{PR}/{p['id']}/generate", json={"targets": ["notice"]}, headers=w["ref-a"])).status_code == 201


@pytest.mark.asyncio
async def test_generation_from_the_dpia_stage_reuses_the_processing_created_for_the_dpia(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"], special_data=True)
    for who, outcome in (("mgr", None), ("ict", None), ("dpo", None)):
        if who == "mgr":
            await _post(env, p["id"], "submit", w["ref-a"])
        assert (await _review(env, p["id"], w[who])).status_code == 200
    dpia = (await _post(env, p["id"], "dpia", w["ref-a"])).json()
    made = (await c.post(f"{PR}/{p['id']}/generate", json={}, headers=w["mgr"]))
    assert made.status_code == 201, made.text
    dpia_row = (await c.get(f"{API}/dpias/{dpia['dpia_id']}", headers=w["mgr"])).json()
    assert made.json()["existing"]["processing"] == dpia_row["processing_activity_id"] and "processing" not in made.json()["created"]
    assert made.json()["existing"]["dpia"] == dpia["dpia_id"]


# ================================================================ regole

@pytest.mark.asyncio
async def test_project_rules_raise_findings_within_scope(env, w):
    c = env["client"]
    p = await _create(env, w["ref-a"])
    await _to_status(env, w, p["id"], "APPROVAL")
    assert (await c.get(FINDINGS, params={"code": "PROJECT_STALLED"}, headers=w["mgr"])).json() == []
    async with env["maker"]() as db:
        proj = await db.get(Project, p["id"])
        proj.status_changed_at = datetime.utcnow() - timedelta(days=45)
        await db.commit()
    stalled = (await c.get(FINDINGS, params={"code": "PROJECT_STALLED"}, headers=w["mgr"])).json()
    assert len(stalled) == 1 and stalled[0]["entity_type"] == "project" and stalled[0]["severity"] == "LOW"
    assert len((await c.get(FINDINGS, params={"code": "PROJECT_STALLED"}, headers=w["ref-a"])).json()) == 1
    assert (await c.get(FINDINGS, params={"code": "PROJECT_STALLED"}, headers=w["ref-b"])).json() == []
    assert (await _review(env, p["id"], w["mgr"], go_live_date=TODAY.isoformat())).status_code == 200            # decisa: non è più ferma
    assert (await c.get(FINDINGS, params={"code": "PROJECT_STALLED"}, headers=w["mgr"])).json() == []

    assert (await c.get(FINDINGS, params={"code": "PROJECT_POST_REVIEW_DUE"}, headers=w["mgr"])).json() == []
    async with env["maker"]() as db:
        proj = await db.get(Project, p["id"])
        proj.post_review_due_date = TODAY - timedelta(days=3)
        await db.commit()
    due = (await c.get(FINDINGS, params={"code": "PROJECT_POST_REVIEW_DUE"}, headers=w["mgr"])).json()
    assert len(due) == 1 and due[0]["severity"] == "MEDIUM" and due[0]["entity_code"] == p["code"]
    assert (await _post(env, p["id"], "advance", w["ref-a"])).status_code == 200                                # avvia la revisione post-implementazione
    assert (await c.get(FINDINGS, params={"code": "PROJECT_POST_REVIEW_DUE"}, headers=w["mgr"])).json() == []
