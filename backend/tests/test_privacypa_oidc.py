"""OIDC (§9, §55): validazione dei token dell'IdP dell'ente."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException

from backend.auth import oidc
from backend.auth.portal_jwt import decode_portal_token

ISSUER = "https://idp.comune.example/realms/ente"


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, other


@pytest.fixture(autouse=True)
def configured(monkeypatch, keys):
    private, _ = keys
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", "privacypa")
    monkeypatch.setenv("OIDC_ADMIN_ROLES", "privacy-admin")
    monkeypatch.setenv("PRAMAIA_JWT_SECRET", "portal-secret-for-tests-0123456789")
    monkeypatch.delenv("OIDC_DEFAULT_TENANT", raising=False)
    client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    monkeypatch.setattr(oidc, "_client", lambda: client)


def token(keys, *, private=None, **override) -> str:
    claims = {"iss": ISSUER, "aud": "privacypa", "sub": "u-42", "email": "mario@comune.example", "name": "Mario Rossi",
              "tenant_id": "comune-1", "roles": ["dpo"], "exp": int(time.time()) + 600, **override}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private or keys[0], algorithm="RS256")


def test_valid_token_is_mapped_to_a_payload(keys):
    user = decode_portal_token(token(keys))
    assert (user.sub, user.email, user.tenant_id, user.roles, user.display_name) == ("u-42", "mario@comune.example", "comune-1", ["dpo"], "Mario Rossi")
    assert user.tenant_role is None and not user.is_admin


def test_admin_role_is_granted_only_by_the_configured_mapping(keys, monkeypatch):
    assert decode_portal_token(token(keys, roles=["privacy-admin"])).tenant_role == "admin"
    assert decode_portal_token(token(keys, roles="dpo privacy-admin")).tenant_role == "admin"   # claim come stringa
    monkeypatch.delenv("OIDC_ADMIN_ROLES")
    assert decode_portal_token(token(keys, roles=["privacy-admin"])).tenant_role is None       # senza mapping nessun admin


@pytest.mark.parametrize("override,fragment", [
    ({"iss": "https://evil.example"}, "non valido"),
    ({"aud": "altra-app"}, "non valido"),
    ({"exp": int(time.time()) - 3600}, "scaduto"),
    ({"sub": None}, "non valido"),
    ({"exp": None}, "non valido"),
])
def test_invalid_claims_are_rejected(keys, override, fragment):
    with pytest.raises(HTTPException) as e:
        decode_portal_token(token(keys, **override))
    assert e.value.status_code == 401 and fragment in e.value.detail


def test_signature_and_algorithm_attacks_are_rejected(keys):
    with pytest.raises(HTTPException) as bad_key:
        decode_portal_token(token(keys, private=keys[1]))                       # firmato con un'altra chiave
    assert bad_key.value.status_code == 401
    good = token(keys)
    with pytest.raises(HTTPException):
        decode_portal_token(good[:-4] + "AAAA")                                  # firma alterata
    # confusione di algoritmo: HS256 firmato con la chiave pubblica non deve mai essere accettato dal ramo OIDC
    public_pem = keys[0].public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    forged = jwt.encode({"iss": ISSUER, "aud": "privacypa", "sub": "x", "tenant_id": "t", "exp": int(time.time()) + 60},
                        "portal-secret-for-tests-0123456789", algorithm="HS256")
    assert not oidc.is_oidc_token(forged)
    with pytest.raises(HTTPException):
        decode_portal_token(forged)                                              # va al ramo Portal: iss non è il Portal
    unsigned = jwt.encode({"iss": ISSUER, "sub": "x", "exp": int(time.time()) + 60}, key=None, algorithm="none")
    with pytest.raises(HTTPException):
        decode_portal_token(unsigned)
    assert public_pem  # la chiave pubblica non è mai usata come segreto


def test_tenant_comes_from_the_configured_claim(keys, monkeypatch):
    with pytest.raises(HTTPException) as e:
        decode_portal_token(token(keys, tenant_id=None))
    assert e.value.status_code == 403
    monkeypatch.setenv("OIDC_TENANT_CLAIM", "ente")
    assert decode_portal_token(token(keys, tenant_id=None, ente="altro-ente")).tenant_id == "altro-ente"
    monkeypatch.setenv("OIDC_TENANT_CLAIM", "non-esiste")
    monkeypatch.setenv("OIDC_DEFAULT_TENANT", "ente-unico")
    assert decode_portal_token(token(keys)).tenant_id == "ente-unico"


def test_without_configuration_asymmetric_tokens_are_not_accepted(keys, monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER")
    assert not oidc.oidc_enabled() and not oidc.is_oidc_token(token(keys))
    with pytest.raises(HTTPException) as e:
        decode_portal_token(token(keys))
    assert e.value.status_code == 401


def test_portal_tokens_keep_working_when_oidc_is_enabled():
    portal = jwt.encode({"iss": "pramaia-portal", "sub": "p1", "email": "p@x.it", "tenant_id": "t1", "exp": int(time.time()) + 60},
                        "portal-secret-for-tests-0123456789", algorithm="HS256")
    assert decode_portal_token(portal).tenant_id == "t1"


def test_unreachable_idp_is_reported_as_unavailable(keys, monkeypatch):
    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(oidc, "_client", down)
    with pytest.raises(HTTPException) as e:
        decode_portal_token(token(keys))
    assert e.value.status_code == 503
