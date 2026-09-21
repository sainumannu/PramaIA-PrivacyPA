#!/usr/bin/env python3
"""
Script per generare una licenza locale di emergenza (fallback).

ATTENZIONE: Questo script è solo per testing/emergency fallback.
In produzione, le licenze devono essere gestite tramite Portal.

Prerequisiti:
    pip install pyjwt cryptography

Usage:
    python generate_emergency_license.py
"""

import jwt
import os
from datetime import datetime, timedelta
from pathlib import Path


def generate_rsa_keys(private_key_path: Path, public_key_path: Path):
    """Genera coppia di chiavi RSA se non già esistenti."""
    if private_key_path.exists() and public_key_path.exists():
        print(f"✓ Chiavi già esistenti: {private_key_path}, {public_key_path}")
        return
    
    print("Generazione coppia chiavi RSA...")
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    
    # Genera chiave privata
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Serializza chiave privata
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_key_path.write_bytes(private_pem)
    print(f"✓ Chiave privata salvata: {private_key_path}")
    
    # Serializza chiave pubblica
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_path.write_bytes(public_pem)
    print(f"✓ Chiave pubblica salvata: {public_key_path}")


def create_emergency_license(
    customer_name: str,
    customer_id: str,
    modules: list[str],
    max_users: int,
    max_instances: int,
    validity_days: int,
    private_key_path: Path,
    output_path: Path,
):
    """Crea token licenza firmato RS256."""
    
    now = datetime.utcnow()
    expires_at = now + timedelta(days=validity_days)
    maintenance_until = expires_at  # Stessa scadenza per semplicità
    
    payload = {
        "license_id": f"EMERGENCY-{customer_id}-{now.strftime('%Y%m%d')}",
        "customer": {
            "customer_id": customer_id,
            "name": customer_name,
        },
        "entitlements": {
            "modules": modules,
            "max_users": max_users,
            "max_instances": max_instances,
            "max_version": "*",  # Accetta qualsiasi versione
        },
        "validity": {
            "issued_at": now.isoformat() + "Z",
            "expires_at": expires_at.isoformat() + "Z",
            "maintenance_until": maintenance_until.isoformat() + "Z",
        },
        "deployment": {
            "allowed_types": ["on_prem", "cloud"],
            "allowed_versions": ["*"],
        },
        # Nessun environment.fingerprint → accetta qualsiasi macchina
    }
    
    # Carica chiave privata
    private_key = private_key_path.read_bytes()
    
    # Genera token JWT firmato
    token = jwt.encode(payload, private_key, algorithm="RS256")
    
    # Salva token
    output_path.write_text(token, encoding="utf-8")
    
    print(f"\n✅ Licenza generata: {output_path}")
    print(f"   License ID: {payload['license_id']}")
    print(f"   Cliente: {customer_name} ({customer_id})")
    print(f"   Moduli: {', '.join(modules)}")
    print(f"   Max utenti: {max_users}")
    print(f"   Max istanze: {max_instances}")
    print(f"   Scadenza: {expires_at.strftime('%Y-%m-%d')}")
    print(f"\n⚠️  ATTENZIONE: Questo è un fallback di emergenza!")
    print(f"   Ripristinare connessione Portal al più presto.")


def main():
    """Entry point."""
    print("=" * 60)
    print("GENERATORE LICENZA LOCALE DI EMERGENZA")
    print("=" * 60)
    print()
    
    # Parametri licenza (configurabili)
    CUSTOMER_NAME = input("Nome cliente (default: Test Emergency): ").strip() or "Test Emergency"
    CUSTOMER_ID = input("ID cliente (default: EMERGENCY001): ").strip() or "EMERGENCY001"
    
    modules_input = input("Moduli (separati da virgola, default: base,albo): ").strip()
    MODULES = [m.strip() for m in modules_input.split(",")] if modules_input else ["base", "albo"]
    
    max_users_input = input("Max utenti (default: 100): ").strip()
    MAX_USERS = int(max_users_input) if max_users_input else 100
    
    max_instances_input = input("Max istanze (default: 5): ").strip()
    MAX_INSTANCES = int(max_instances_input) if max_instances_input else 5
    
    validity_days_input = input("Giorni validità (default: 365): ").strip()
    VALIDITY_DAYS = int(validity_days_input) if validity_days_input else 365
    
    print()
    
    # Paths
    PRIVATE_KEY_PATH = Path("license_private_key.pem")
    PUBLIC_KEY_PATH = Path("license_public_key.pem")
    LICENSE_FILE_PATH = Path("license.jwt")
    
    # Genera chiavi se necessario
    generate_rsa_keys(PRIVATE_KEY_PATH, PUBLIC_KEY_PATH)
    print()
    
    # Genera licenza
    create_emergency_license(
        customer_name=CUSTOMER_NAME,
        customer_id=CUSTOMER_ID,
        modules=MODULES,
        max_users=MAX_USERS,
        max_instances=MAX_INSTANCES,
        validity_days=VALIDITY_DAYS,
        private_key_path=PRIVATE_KEY_PATH,
        output_path=LICENSE_FILE_PATH,
    )
    
    print()
    print("=" * 60)
    print("DEPLOYMENT")
    print("=" * 60)
    print(f"1. Copia {LICENSE_FILE_PATH} nella root dell'app")
    print(f"2. Copia {PUBLIC_KEY_PATH} nella root dell'app")
    print(f"3. ⚠️  NON COMMITTARE {PRIVATE_KEY_PATH} (conservare in vault sicuro)")
    print(f"4. Verifica che Portal sia inaccessibile (altrimenti il file sarà ignorato)")
    print()
    print("Per testare:")
    print("  GET /api/license/status")
    print('  → sync_status dovrebbe essere "local_file"')
    print()


if __name__ == "__main__":
    main()
