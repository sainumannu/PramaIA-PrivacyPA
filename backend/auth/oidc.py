"""
Validazione di token OpenID Connect (§9, §55): access/ID token firmati con chiave asimmetrica dall'IdP dell'ente.

Attivo con `OIDC_ISSUER`. Le chiavi si leggono dal JWKS (`OIDC_JWKS_URL`, o dalla discovery dell'emittente) con
cache. Si accettano solo algoritmi asimmetrici (mai `none` né HS*: evita la confusione tra chiave pubblica e
segreto condiviso); emittente e, se configurato, audience sono verificati. Il tenant si legge da un claim.

Questo modulo valida i token presentati alle API; il flusso interattivo di login (authorization code + PKCE)
non è incluso: l'interfaccia continua ad autenticarsi tramite il Portal.
"""
import json
import os
import urllib.request
from typing import Any

import jwt
from fastapi import HTTPException, status

ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
_jwks_clients: dict[str, Any] = {}


def oidc_enabled() -> bool:
    return bool(os.getenv("OIDC_ISSUER"))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def token_algorithm(token: str) -> str | None:
    try:
        return jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        return None


def is_oidc_token(token: str) -> bool:
    return oidc_enabled() and token_algorithm(token) in ALLOWED_ALGORITHMS


def _jwks_uri() -> str:
    explicit = os.getenv("OIDC_JWKS_URL")
    if explicit:
        return explicit
    issuer = os.environ["OIDC_ISSUER"].rstrip("/")
    with urllib.request.urlopen(f"{issuer}/.well-known/openid-configuration", timeout=5) as response:  # noqa: S310
        return json.load(response)["jwks_uri"]


def _client():
    """Client JWKS con cache (sostituibile nei test)."""
    uri = _jwks_uri()
    if uri not in _jwks_clients:
        _jwks_clients[uri] = jwt.PyJWKClient(uri, cache_keys=True, lifespan=3600)
    return _jwks_clients[uri]


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return value.replace(",", " ").split()
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def decode_oidc_token(token: str):
    from backend.auth.portal_jwt import TokenPayload

    algorithm = token_algorithm(token)
    if algorithm not in ALLOWED_ALGORITHMS:
        raise _unauthorized("Algoritmo di firma non ammesso")
    audience = os.getenv("OIDC_AUDIENCE") or None
    try:
        key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=[algorithm], issuer=os.environ["OIDC_ISSUER"], audience=audience, leeway=30,
            options={"require": ["exp", "iss", "sub"], "verify_aud": audience is not None},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token scaduto")
    except jwt.PyJWTError as e:
        raise _unauthorized(f"Token non valido: {e}")
    except OSError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "IdP non raggiungibile: impossibile leggere le chiavi")

    tenant = claims.get(os.getenv("OIDC_TENANT_CLAIM", "tenant_id")) or os.getenv("OIDC_DEFAULT_TENANT")
    if not tenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Il token non indica l'ente di appartenenza")
    roles = _list(claims.get(os.getenv("OIDC_ROLES_CLAIM", "roles")))
    admin_roles = set(_list(os.getenv("OIDC_ADMIN_ROLES", "")))
    return TokenPayload({
        "sub": claims["sub"],
        "email": claims.get("email", ""),
        "display_name": claims.get("name", ""),
        "roles": roles,
        "tenant_id": str(tenant),
        # Amministratore dell'ente solo se l'ente lo dichiara esplicitamente tramite OIDC_ADMIN_ROLES.
        "tenant_role": "admin" if admin_roles and admin_roles & set(roles) else None,
        "preferred_language": claims.get("locale"),
        "oidc": True,
    })
