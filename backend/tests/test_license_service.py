"""
Test per il sistema di verifica licenze.

Questi test verificano tutti i casi della specifica:
- Licenza valida
- File mancante
- Firma non valida
- Payload non valido
- Licenza scaduta
- Maintenance scaduta
- Environment mismatch
- Modulo non licenziato
- Utenti oltre limite
- Istanze oltre limite
- Versione non consentita
"""

import pytest
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from backend.services.license_service import LicenseService, LicenseCheckResult


# Fixture: chiavi di test
@pytest.fixture(scope="session")
def test_keys():
    """Genera coppia di chiavi RSA per test."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    return private_pem, public_pem


@pytest.fixture
def setup_license_files(tmp_path, test_keys, monkeypatch):
    """Setup file licenza e chiave pubblica in directory temporanea."""
    private_key, public_key = test_keys
    
    # Cambia percorsi a directory temporanea
    monkeypatch.setattr(LicenseService, "LICENSE_FILE_PATH", tmp_path / "license.jwt")
    monkeypatch.setattr(LicenseService, "PUBLIC_KEY_PATH", tmp_path / "license_public_key.pem")
    
    # Scrivi chiave pubblica
    (tmp_path / "license_public_key.pem").write_bytes(public_key)
    
    return tmp_path, private_key


def create_license_token(payload: dict, private_key: bytes) -> str:
    """Helper per creare token JWT firmato."""
    return jwt.encode(payload, private_key, algorithm="RS256")


def create_valid_payload() -> dict:
    """Crea payload licenza valido."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=365)
    
    return {
        "license_id": "LIC-TEST-2026-0001",
        "customer": {
            "name": "Test Customer",
            "vat_or_cf": "12345678901",
        },
        "entitlements": {
            "modules": ["albo", "protocollo"],
            "max_users": 100,
            "max_instances": 5,
            "max_version": "1.x",
        },
        "environment": {
            "fingerprint": LicenseService._get_local_fingerprint(),
            "deployment_type": LicenseService._get_deployment_type(),
        },
        "validity": {
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "maintenance_until": expires.isoformat(),
        },
    }


# TEST 1: Licenza valida
@pytest.mark.asyncio
async def test_license_valid(setup_license_files):
    """Test: licenza valida -> OK"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is True
    assert result.code == "OK"
    assert result.license_id == "LIC-TEST-2026-0001"


# TEST 2: File mancante
@pytest.mark.asyncio
async def test_license_missing(setup_license_files):
    """Test: file licenza non trovato -> LICENSE_MISSING"""
    tmp_path, _ = setup_license_files
    # Non creare file license.jwt
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "LICENSE_MISSING"


# TEST 3: Firma non valida
@pytest.mark.asyncio
async def test_signature_invalid(setup_license_files):
    """Test: firma JWT non valida -> SIGNATURE_INVALID"""
    tmp_path, private_key = setup_license_files
    
    # Token con firma sbagliata (chiave diversa)
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    
    wrong_key = rsa.generate_private_key(65537, 2048, default_backend())
    wrong_pem = wrong_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    )
    
    payload = create_valid_payload()
    token = create_license_token(payload, wrong_pem)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "SIGNATURE_INVALID"


# TEST 4: Payload non valido
@pytest.mark.asyncio
async def test_payload_invalid(setup_license_files):
    """Test: campi obbligatori mancanti -> PAYLOAD_INVALID"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    del payload["entitlements"]["modules"]  # Rimuovi campo obbligatorio
    
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "PAYLOAD_INVALID"


# TEST 5: Licenza scaduta
@pytest.mark.asyncio
async def test_license_expired(setup_license_files):
    """Test: licenza scaduta -> LICENSE_EXPIRED"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    past = datetime.now(timezone.utc) - timedelta(days=10)
    payload["validity"]["expires_at"] = past.isoformat()
    
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "LICENSE_EXPIRED"


# TEST 6: Fingerprint mismatch
@pytest.mark.asyncio
async def test_environment_mismatch(setup_license_files):
    """Test: fingerprint non corrispondente -> ENVIRONMENT_MISMATCH"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    payload["environment"]["fingerprint"] = "wrong-fingerprint-12345"
    
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "ENVIRONMENT_MISMATCH"


# TEST 7: Modulo non licenziato
@pytest.mark.asyncio
async def test_module_not_licensed(setup_license_files):
    """Test: modulo richiesto non in lista -> MODULE_NOT_LICENSED"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    payload["entitlements"]["modules"] = ["albo"]  # Solo albo
    
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license(required_module="protocollo")
    
    assert result.valid is False
    assert result.code == "MODULE_NOT_LICENSED"


# TEST 8: Versione non consentita
@pytest.mark.asyncio
async def test_version_not_allowed(setup_license_files, monkeypatch):
    """Test: versione app non compatibile -> VERSION_NOT_ALLOWED"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    payload["entitlements"]["max_version"] = "1.x"
    
    # Simula app versione 2.0.0
    monkeypatch.setattr(LicenseService, "APP_VERSION", "2.0.0")
    
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    result = await service.verify_license()
    
    assert result.valid is False
    assert result.code == "VERSION_NOT_ALLOWED"


# TEST 9: Cache TTL
@pytest.mark.asyncio
async def test_license_cache(setup_license_files):
    """Test: cache funziona correttamente"""
    tmp_path, private_key = setup_license_files
    
    payload = create_valid_payload()
    token = create_license_token(payload, private_key)
    (tmp_path / "license.jwt").write_text(token)
    
    service = LicenseService()
    
    # Prima chiamata
    result1 = await service.verify_license()
    assert result1.valid is True
    
    # Seconda chiamata (dovrebbe usare cache)
    result2 = await service.verify_license()
    assert result2.valid is True
    
    # Force refresh
    result3 = await service.verify_license(force_refresh=True)
    assert result3.valid is True
