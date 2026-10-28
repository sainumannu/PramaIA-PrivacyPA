"""
PrivacyPA — Anagrafica delle persone dal Portal (richieste di accesso: «persona non risolvibile»).

Il Portal detiene le anagrafiche e non emette eventi sulle modifiche: si legge `GET /api/tenants/{tenant_id}/users` con il
token di servizio di PrivacyPA (senza `audience`), con una cache breve per tenant e una rilettura immediata quando arriva
un id sconosciuto. Una persona è risolvibile se esiste ed è attiva sia come utente (`is_active`) sia come membro del tenant
(`membership_active`). Portal non configurato o non raggiungibile: `DirectoryUnavailable`, il chiamante non blocca.

`people` aggiunge le strutture dell'organigramma del Portal (`GET /api/tenants/{tenant_id}/org/tree`) per scegliere una
persona nei moduli dell'interfaccia (es. l'incaricato di un'autorizzazione).
"""
import asyncio
import os
import time

import httpx

CACHE_SECONDS = 300
UNKNOWN_REFRESH_SECONDS = 30

ACTIVE, INACTIVE, NOT_FOUND = "ACTIVE", "INACTIVE", "NOT_FOUND"


class DirectoryUnavailable(RuntimeError):
    """Anagrafica del Portal non disponibile: la verifica della persona non si può fare."""


_users: dict[str, tuple[float, dict[str, dict]]] = {}
_org: dict[str, tuple[float, dict | None]] = {}
_token: dict = {"value": None, "expires_at": 0.0}
_lock = asyncio.Lock()


def _portal_url() -> str:
    return (os.getenv("PORTAL_API_URL") or os.getenv("PORTAL_URL") or "").rstrip("/")


async def _service_token(client: httpx.AsyncClient) -> str:
    if _token["value"] and _token["expires_at"] > time.time() + 60:
        return _token["value"]
    app_id, secret = os.getenv("APP_ID"), os.getenv("APP_CLIENT_SECRET")
    if not _portal_url() or not app_id or not secret:
        raise DirectoryUnavailable("Portal non configurato (PORTAL_URL, APP_ID, APP_CLIENT_SECRET)")
    response = await client.post(f"{_portal_url()}/api/service-auth/token", json={"app_id": app_id, "client_secret": secret}, timeout=5.0)
    response.raise_for_status()
    _token["value"] = response.json()["access_token"]
    _token["expires_at"] = time.time() + 15 * 60          # rinnovo prudente: il Portal non indica la durata
    return _token["value"]


async def _get(path: str):
    try:
        async with httpx.AsyncClient() as client:
            token = await _service_token(client)
            response = await client.get(f"{_portal_url()}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
            response.raise_for_status()
            return response.json()
    except DirectoryUnavailable:
        raise
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise DirectoryUnavailable(f"Dati del Portal non disponibili ({path}): {e}") from e


async def _fetch(tenant_id: str) -> dict[str, dict]:
    rows = await _get(f"/api/tenants/{tenant_id}/users") or []
    return {str(u["id"]): u for u in rows if isinstance(u, dict) and u.get("id")}


def _walk(root) -> tuple[list[dict], dict[str, dict]]:
    """Strutture (senza la radice, che è l'ente stesso) e membri dell'organigramma, con il percorso di ogni struttura."""
    nodes: list[dict] = []
    members: dict[str, dict] = {}

    def visit(node, path: tuple[str, ...], parent_id: str | None) -> None:
        if not isinstance(node, dict):
            return
        here = (*path, node.get("name") or "")
        is_root = not path
        # Il percorso non ripete la radice, che allungherebbe ogni voce; chi sta sulla radice ha il suo nome
        unit = " › ".join(p for p in here[1:] if p) or here[0]
        if not is_root and node.get("id"):
            nodes.append({"id": str(node["id"]), "code": node.get("node_id") or None, "name": node.get("name") or "",
                          "parent_id": parent_id, "path": unit})
        for member in node.get("members") or []:
            if isinstance(member, dict) and member.get("id"):
                members.setdefault(str(member["id"]), {"member": member, "units": []})["units"].append(unit)
        for child in node.get("children") or []:
            visit(child, here, None if is_root else str(node.get("id")))

    visit(root, (), None)
    return nodes, members


async def _fetch_org(tenant_id: str):
    """Radice dell'organigramma del tenant (None se non c'è)."""
    tree = await _get(f"/api/tenants/{tenant_id}/org/tree") or {}
    return tree.get("root") if isinstance(tree, dict) else None


async def _cached_org(tenant_id: str):
    """Da chiamare con `_lock` acquisito."""
    now = time.monotonic()
    fetched_at, root = _org.get(tenant_id, (0.0, None))
    if now - fetched_at > CACHE_SECONDS:
        root = await _fetch_org(tenant_id)
        _org[tenant_id] = (now, root)
    return root


async def member_status(tenant_id: str, user_id: str) -> str:
    """ACTIVE, INACTIVE (utente o membership disattivati) o NOT_FOUND; `DirectoryUnavailable` se non si può sapere."""
    async with _lock:
        fetched_at, users = _users.get(tenant_id, (0.0, {}))
        age = time.monotonic() - fetched_at
        if age > CACHE_SECONDS or (user_id not in users and age > UNKNOWN_REFRESH_SECONDS):
            users = await _fetch(tenant_id)
            _users[tenant_id] = (time.monotonic(), users)
    user = users.get(user_id)
    if user is None:
        return NOT_FOUND
    if user.get("is_active") is False or user.get("membership_active") is False:
        return INACTIVE
    return ACTIVE


async def people(tenant_id: str) -> list[dict]:
    """Utenti del tenant e membri dell'organigramma, con le strutture in cui compaiono, ordinati per struttura e nome.

    Comprende anche le persone disattivate (`active` falso): l'interfaccia non le propone, ma ne mostra il nome sulle
    autorizzazioni già registrate. `DirectoryUnavailable` se il Portal non risponde.
    """
    async with _lock:
        now = time.monotonic()
        fetched_at, users = _users.get(tenant_id, (0.0, {}))
        if now - fetched_at > CACHE_SECONDS:
            users = await _fetch(tenant_id)
            _users[tenant_id] = (now, users)
        _, org = _walk(await _cached_org(tenant_id))
    result = []
    for user_id in users.keys() | org.keys():
        user = users.get(user_id) or org[user_id]["member"]
        result.append({
            "id": user_id,
            "name": user.get("display_name") or user.get("email") or user_id,
            "email": user.get("email"),
            "active": user.get("is_active") is not False and user.get("membership_active") is not False,
            "units": org.get(user_id, {}).get("units", []),
        })
    # Prima chi sta nell'organigramma, per struttura; in fondo gli utenti del tenant che non vi compaiono
    return sorted(result, key=lambda p: (not p["units"], (p["units"] or [""])[0].lower(), p["name"].lower()))


async def org_units(tenant_id: str) -> list[dict]:
    """Strutture dell'organigramma del Portal: id, code, name, parent_id (None sotto l'ente), path; i padri prima dei figli."""
    async with _lock:
        nodes, _ = _walk(await _cached_org(tenant_id))
    return nodes
