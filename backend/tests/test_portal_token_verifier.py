"""Test del modulo comune backend/auth/portal_token_verifier.py (uguale in tutte le app).

Il JWKS del Portal è simulato sostituendo httpx.get: nessuna rete.
"""

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.auth import portal_token_verifier as verifier
from backend.auth.portal_token_verifier import PortalTokenError, verify_portal_token

SECRET = "segreto-di-test"


def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid: str) -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _claims(**extra) -> dict:
    now = int(time.time())
    return {"sub": "u1", "iss": "pramaia-portal", "iat": now, "exp": now + 600, **extra}


def _rs256(private_key, kid: str, **extra) -> str:
    return jwt.encode(_claims(**extra), private_key, algorithm="RS256", headers={"kid": kid})


def _hs256(**extra) -> str:
    return jwt.encode(_claims(**extra), SECRET, algorithm="HS256")


class _FakePortal:
    """Sostituisce httpx.get: serve `keys` come JWKS, o fallisce se `down`."""

    def __init__(self):
        self.keys: list = []
        self.down = False
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("Portal giù")
        return httpx.Response(200, json={"keys": self.keys}, request=httpx.Request("GET", url))


@pytest.fixture()
def portal(monkeypatch):
    fake = _FakePortal()
    monkeypatch.setattr(verifier.httpx, "get", fake.get)
    monkeypatch.setenv("PORTAL_API_URL", "http://portal.test")
    monkeypatch.setenv("PRAMAIA_JWT_SECRET", SECRET)
    monkeypatch.delenv("PORTAL_JWKS_URL", raising=False)
    monkeypatch.delenv("PRAMAIA_JWT_REQUIRE_RS256", raising=False)
    verifier.reset_jwks_cache()
    yield fake
    verifier.reset_jwks_cache()


def test_hs256_token_still_accepted_without_contacting_the_portal(portal):
    assert verify_portal_token(_hs256())["sub"] == "u1"
    assert portal.calls == 0


def test_rs256_token_verified_with_the_published_key(portal):
    key = _rsa()
    portal.keys = [_jwk(key, "k1")]
    assert verify_portal_token(_rs256(key, "k1", amr=["pwd"]))["amr"] == ["pwd"]
    # Seconda verifica: dalla cache
    verify_portal_token(_rs256(key, "k1"))
    assert portal.calls == 1


def test_rs256_signed_with_another_key_is_rejected(portal):
    portal.keys = [_jwk(_rsa(), "k1")]
    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(_rs256(_rsa(), "k1"))
    assert e.value.reason == "invalid"


def test_public_key_used_as_hmac_secret_is_rejected(portal):
    key = _rsa()
    portal.keys = [_jwk(key, "k1")]
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    payload = b64(json.dumps(_claims(sub="attaccante")).encode())
    signature = b64(hmac.new(pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    with pytest.raises(PortalTokenError):
        verify_portal_token(f"{header}.{payload}.{signature}")


def test_unknown_kid_reloads_the_jwks_at_most_once_a_minute(portal):
    old, new = _rsa(), _rsa()
    portal.keys = [_jwk(old, "old")]
    verify_portal_token(_rs256(old, "old"))

    # Rotazione: la chiave nuova compare nel JWKS, il token con il kid nuovo la fa ricaricare
    portal.keys = [_jwk(old, "old"), _jwk(new, "new")]
    assert verify_portal_token(_rs256(new, "new"))["sub"] == "u1"
    assert portal.calls == 2

    # Una raffica di kid inventati non genera una richiesta per token
    for _ in range(5):
        with pytest.raises(PortalTokenError) as e:
            verify_portal_token(_rs256(_rsa(), "inventato"))
        assert e.value.reason == "unknown_key"
    assert portal.calls == 2


def test_cached_keys_survive_a_portal_outage(portal, monkeypatch):
    key = _rsa()
    portal.keys = [_jwk(key, "k1")]
    verify_portal_token(_rs256(key, "k1"))

    portal.down = True
    monkeypatch.setattr(verifier, "JWKS_TTL_SECONDS", -1)  # cache scaduta: prova a riscaricare
    assert verify_portal_token(_rs256(key, "k1"))["sub"] == "u1"


def test_hs256_can_be_disabled(portal, monkeypatch):
    monkeypatch.setenv("PRAMAIA_JWT_REQUIRE_RS256", "true")
    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(_hs256())
    assert e.value.reason == "hs256_disabled"
    assert verifier.capabilities()["algorithms"] == ["RS256"]


def test_error_reasons(portal, monkeypatch):
    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(jwt.encode(_claims(), "altro-segreto", algorithm="HS256"))
    assert e.value.reason == "signature"

    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(jwt.encode(_claims(exp=int(time.time()) - 10), SECRET, algorithm="HS256"))
    assert e.value.reason == "expired"

    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(jwt.encode(_claims(iss="qualcun-altro"), SECRET, algorithm="HS256"))
    assert e.value.reason == "issuer"

    with pytest.raises(PortalTokenError) as e:
        verify_portal_token("non-un-token")
    assert e.value.reason == "invalid"

    monkeypatch.delenv("PORTAL_API_URL")
    monkeypatch.delenv("PORTAL_URL", raising=False)
    with pytest.raises(PortalTokenError) as e:
        verify_portal_token(_rs256(_rsa(), "k1"))
    assert e.value.reason == "not_configured"


def test_audience_is_checked_when_given(portal):
    token = _hs256(aud="pramaia-helpdesk")
    assert verify_portal_token(token, audience="pramaia-helpdesk")["aud"] == "pramaia-helpdesk"
    with pytest.raises(PortalTokenError):
        verify_portal_token(token, audience="pramaia-vault")
    # Senza audience il token con aud è rifiutato, salvo verify_aud=False (l'app lo controlla da sé)
    with pytest.raises(PortalTokenError):
        verify_portal_token(token)
    assert verify_portal_token(token, verify_aud=False)["aud"] == "pramaia-helpdesk"
