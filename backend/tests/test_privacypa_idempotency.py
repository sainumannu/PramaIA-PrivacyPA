"""Idempotency-Key (§131): i retry con la stessa chiave non rieseguono la richiesta."""
import base64
import json
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base
from backend.middleware.idempotency import IdempotencyMiddleware
from backend.models.privacypa_idempotency import IdempotencyRecord


def token(tenant="t1", sub="u1") -> dict:
    seg = base64.urlsafe_b64encode(json.dumps({"tenant_id": tenant, "sub": sub}).encode()).decode().rstrip("=")
    return {"Authorization": f"Bearer x.{seg}.y"}


@pytest_asyncio.fixture
async def rig():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app, state = FastAPI(), {"created": 0}

    @app.post("/items")
    async def create(body: dict):
        if body.get("fail"):
            raise HTTPException(400, "non valido")
        state["created"] += 1
        return {"n": state["created"], "name": body.get("name")}

    @app.get("/items")
    async def read():
        return {"n": state["created"]}

    app.add_middleware(IdempotencyMiddleware, session_factory=maker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, state, maker
    await engine.dispose()


def _h(key, **kw):
    return {**token(**kw), "Idempotency-Key": key}


@pytest.mark.asyncio
async def test_retry_returns_the_first_response_without_running_again(rig):
    c, state, _ = rig
    first = await c.post("/items", json={"name": "a"}, headers=_h("key-0000001"))
    again = await c.post("/items", json={"name": "a"}, headers=_h("key-0000001"))
    assert first.status_code == again.status_code == 200 and again.json() == first.json() == {"n": 1, "name": "a"}
    assert again.headers["idempotent-replay"] == "true" and "idempotent-replay" not in first.headers
    assert state["created"] == 1


@pytest.mark.asyncio
async def test_same_key_with_a_different_request_is_rejected(rig):
    c, state, _ = rig
    await c.post("/items", json={"name": "a"}, headers=_h("key-0000002"))
    other = await c.post("/items", json={"name": "b"}, headers=_h("key-0000002"))
    assert other.status_code == 422 and "diversa" in other.json()["detail"]
    assert state["created"] == 1


@pytest.mark.asyncio
async def test_keys_are_scoped_by_user_and_tenant(rig):
    c, state, _ = rig
    await c.post("/items", json={"name": "a"}, headers=_h("key-0000003"))
    other_user = await c.post("/items", json={"name": "a"}, headers=_h("key-0000003", sub="u2"))
    other_tenant = await c.post("/items", json={"name": "a"}, headers=_h("key-0000003", tenant="t2"))
    assert other_user.json()["n"] == 2 and other_tenant.json()["n"] == 3 and state["created"] == 3


@pytest.mark.asyncio
async def test_errors_are_not_frozen_and_the_client_can_retry(rig):
    c, state, maker = rig
    failed = await c.post("/items", json={"fail": True}, headers=_h("key-0000004"))
    assert failed.status_code == 400
    async with maker() as db:
        assert (await db.execute(select(IdempotencyRecord))).first() is None
    ok = await c.post("/items", json={"name": "corretto"}, headers=_h("key-0000004"))
    assert ok.status_code == 200 and state["created"] == 1


@pytest.mark.asyncio
async def test_pass_through_cases(rig):
    c, state, maker = rig
    await c.post("/items", json={"name": "a"}, headers=token())                           # senza chiave: nessuna memorizzazione
    await c.post("/items", json={"name": "a"}, headers=token())
    assert state["created"] == 2
    assert (await c.get("/items", headers=_h("key-0000005"))).status_code == 200            # solo POST
    no_auth = await c.post("/items", json={}, headers={"Idempotency-Key": "key-0000006"})   # senza identità: passa (l'autenticazione decide)
    assert no_auth.status_code == 200
    async with maker() as db:
        assert (await db.execute(select(IdempotencyRecord))).first() is None
    bad = await c.post("/items", json={}, headers=_h("corta"))
    assert bad.status_code == 400 and "Idempotency-Key" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_in_progress_and_expired_records(rig):
    c, state, maker = rig
    await c.post("/items", json={"name": "a"}, headers=_h("key-0000007"))
    async with maker() as db:                                                             # simula una richiesta ancora in corso
        await db.execute(update(IdempotencyRecord).values(response_status=None, response_body=None))
        await db.commit()
    busy = await c.post("/items", json={"name": "a"}, headers=_h("key-0000007"))
    assert busy.status_code == 409 and state["created"] == 1
    async with maker() as db:                                                             # scaduta: la chiave si può riusare
        await db.execute(update(IdempotencyRecord).values(expires_at=datetime.utcnow() - timedelta(hours=1)))
        await db.commit()
    fresh = await c.post("/items", json={"name": "a"}, headers=_h("key-0000007"))
    assert fresh.status_code == 200 and fresh.json()["n"] == 2
