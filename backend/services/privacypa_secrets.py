"""
PrivacyPA — Cifratura a riposo dei segreti applicativi (per ora: chiavi di firma dei webhook, §70).

La chiave si ricava da `PRIVACYPA_SECRETS_KEY` (consigliata: stringa casuale lunga, distinta dagli altri segreti) oppure, in
mancanza, da `PRAMAIA_JWT_SECRET`. Cambiare la chiave rende illeggibili i segreti già salvati: vanno rigenerati (rotazione).
Senza nessuna delle due variabili non si possono creare né usare segreti.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretsUnavailable(RuntimeError):
    """Chiave di cifratura non configurata o non più valida."""


def _fernet() -> Fernet:
    material = os.getenv("PRIVACYPA_SECRETS_KEY") or os.getenv("PRAMAIA_JWT_SECRET")
    if not material:
        raise SecretsUnavailable("Chiave dei segreti non configurata (PRIVACYPA_SECRETS_KEY)")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("privacypa-secrets-v1:" + material).encode()).digest()))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretsUnavailable("Il segreto non è leggibile con la chiave attuale: rigenerarlo") from None
