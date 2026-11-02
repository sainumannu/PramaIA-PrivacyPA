"""Scheda per persona: a cosa era abilitata a una data e in virtù di quali autorizzazioni, con lo storico delle versioni."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete, update

from backend.models.privacypa_identity import PrivacyRoleCode as R, ScopeType
from backend.models.privacypa_people import AuthorizationSnapshot, TrainingCompletion
from backend.tests.conftest import TA, _h, ifm

API = "/api/privacypa"
AU = f"{API}/authorizations"
ADMIN = _h(TA, "root", "admin")
TODAY = date.today()


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


async def _setup(env):
    c = env["client"]
    asset = (await c.post(f"{API}/assets", json={"name": "Anagrafe", "owner_unit_id": "unit-a"}, headers=ADMIN)).json()
    course = (await c.post(f"{API}/training/courses", json={"title": "Privacy di base", "validity_months": 12}, headers=ADMIN)).json()
    profile = (await c.post(f"{AU}/profiles", json={
        "name": "Operatore anagrafe", "code": "OP_ANAG", "asset_id": asset["id"], "allowed_operations": ["READ", "UPDATE"],
        "required_training_course_ids": [course["id"]],
    }, headers=ADMIN)).json()
    async with env["maker"]() as db:
        db.add(TrainingCompletion(tenant_id=TA, course_id=course["id"], user_id="mario",
                                  completed_at=TODAY - timedelta(days=30), next_due_at=TODAY + timedelta(days=300)))
        await db.commit()
    return asset, course, profile


async def _grant(c, asset, profile, **over):
    body = {"user_id": "mario", "asset_id": asset["id"], "authorization_profile_id": profile["id"], "valid_from": ago(60),
            "operations": ["READ"], "authorized_by_name": "Mario Bianchi", "authorization_reference": "PROT-2026-0042",
            "authorization_date": ago(61), **over}
    r = await c.post(AU, json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


async def _backdate(env, auth_id: str, days: int):
    """Simula una registrazione fatta `days` giorni fa (la versione di creazione e il record)."""
    when = datetime.utcnow() - timedelta(days=days)
    async with env["maker"]() as db:
        await db.execute(update(AuthorizationSnapshot).where(AuthorizationSnapshot.authorization_id == auth_id)
                         .values(recorded_at=when))
        from backend.models.privacypa_people import Authorization
        await db.execute(update(Authorization).where(Authorization.id == auth_id).values(granted_at=when))
        await db.commit()


async def _person(c, user="mario", as_of=None, headers=ADMIN):
    r = await c.get(f"{AU}/person/{user}", params={"as_of": as_of} if as_of else None, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_the_person_view_rebuilds_what_was_valid_on_a_past_day(env):
    c = env["client"]
    asset, course, profile = await _setup(env)
    auth = await _grant(c, asset, profile)
    await _backdate(env, auth["id"], 60)

    r = await c.patch(f"{AU}/{auth['id']}", json={"operations": ["READ", "UPDATE"], "valid_to": (TODAY + timedelta(days=60)).isoformat()},
                      headers=ifm(auth["row_version"], ADMIN))
    assert r.status_code == 200, r.text

    today = await _person(c)
    (entry,) = today["entries"]
    assert today["as_of"] == TODAY.isoformat() and entry["status_at"] == "ACTIVE"
    assert entry["values"]["operations"] == ["READ", "UPDATE"] and not entry["changed_after"]
    assert entry["values"]["authorization_reference"] == "PROT-2026-0042" and entry["asset_name"] == "Anagrafe"
    assert entry["profile_name"] == "Operatore anagrafe"
    assert entry["training"] == [{"course_id": course["id"], "title": "Privacy di base", "state": "DONE",
                                  "completed_at": ago(30), "next_due_at": (TODAY + timedelta(days=300)).isoformat()}]

    past = (await _person(c, as_of=ago(40)))["entries"][0]
    assert past["values"]["operations"] == ["READ"] and past["values"]["valid_to"] is None     # i valori di allora
    assert past["changed_after"] and past["status_at"] == "ACTIVE"
    assert past["training"][0]["state"] == "MISSING"                                            # corso fatto dopo
    assert (await _person(c, as_of=ago(90)))["entries"][0]["status_at"] == "NOT_STARTED"

    r = await c.post(f"{AU}/{auth['id']}/revoke", json={"reason": "Cambio ufficio", "revocation_reference": "PROT-2026-0100"},
                     headers=ifm(r.json()["row_version"], ADMIN))
    assert r.status_code == 200, r.text
    revoked = (await _person(c))["entries"][0]
    assert revoked["status_at"] == "REVOKED" and revoked["values"]["revocation_reference"] == "PROT-2026-0100"
    assert (await _person(c, as_of=ago(1)))["entries"][0]["status_at"] == "ACTIVE"

    history = (await c.get(f"{AU}/{auth['id']}/history", headers=ADMIN)).json()
    assert [h["action"] for h in history] == ["create", "update", "revoke"]


@pytest.mark.asyncio
async def test_a_registration_with_retroactive_start_and_an_old_record_without_history(env):
    c = env["client"]
    asset, _, profile = await _setup(env)
    late = await _grant(c, asset, profile)                       # registrata oggi, valida da 60 giorni fa
    entry = (await _person(c, as_of=ago(30)))["entries"][0]
    assert entry["status_at"] == "ACTIVE" and entry["registered_after"] and entry["values"]["operations"] == ["READ"]

    # un'autorizzazione registrata prima dello storico: alla prima modifica si conservano anche i valori di prima
    async with env["maker"]() as db:
        await db.execute(delete(AuthorizationSnapshot).where(AuthorizationSnapshot.authorization_id == late["id"]))
        await db.commit()
    assert (await _person(c))["entries"][0]["tracked_since"] is None
    r = await c.patch(f"{AU}/{late['id']}", json={"operations": ["READ", "UPDATE"]}, headers=ifm(late["row_version"], ADMIN))
    assert r.status_code == 200, r.text
    history = (await c.get(f"{AU}/{late['id']}/history", headers=ADMIN)).json()
    assert [h["action"] for h in history] == ["baseline", "update"]
    assert history[0]["data"]["operations"] == ["READ"] and history[1]["data"]["operations"] == ["READ", "UPDATE"]
    assert (await _person(c))["entries"][0]["tracked_since"] == history[1]["recorded_at"]


@pytest.mark.asyncio
async def test_the_person_view_follows_the_visibility_of_the_processing(env):
    c = env["client"]
    asset, _, profile = await _setup(env)
    await _grant(c, asset, profile, processing_activity_id=env["ids"]["proc_a"])
    other = await _grant(c, asset, profile, processing_activity_id=env["ids"]["proc_b"])
    await env["grant"](TA, "ref-b", R.PRIVACY_REFERENT, ScopeType.UNIT, "unit-b")
    ref_b = _h(TA, "ref-b")
    entries = (await _person(c, headers=ref_b))["entries"]
    assert [e["authorization_id"] for e in entries] == [other["id"]]
    assert entries[0]["processing_name"].endswith("Trattamento B")
    mine = [e["authorization_id"] for e in (await _person(c))["entries"]]
    hidden = next(i for i in mine if i != other["id"])
    assert (await c.get(f"{AU}/{hidden}/history", headers=ref_b)).status_code in (403, 404)
    await env["grant"](TA, "op", R.OPERATOR)
    assert (await c.get(f"{AU}/person/mario", headers=_h(TA, "op"))).status_code == 403
