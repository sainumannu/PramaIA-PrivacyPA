"""Verifica delle firme dei token emessi da PramaIA Portal: HS256 e RS256.

MODULO COMUNE A TUTTE LE APP. Va copiato identico dall'AppTemplate
(backend/auth/portal_token_verifier.py) e non modificato nell'app: le regole dell'app
(ruoli, accesso, tenant) stanno in portal_jwt.py, che chiama verify_portal_token.
Versione del modulo: VERIFIER_VERSION. Riferimento: FIRMA_ASIMMETRICA.md nel repo del Portal.

Il Portal firma i token in uno di due modi:
- HS256 con PRAMAIA_JWT_SECRET (header senza `kid`), come sempre;
- RS256 con la sua chiave privata (header con `kid`); le chiavi pubbliche sono in
  {PORTAL_API_URL}/.well-known/jwks.json.

Regole di sicurezza:
- L'algoritmo non si legge mai dall'header. Dall'header si legge solo `kid`, per scegliere
  la chiave: con `kid` il token si verifica solo come RS256 con quella chiave pubblica,
  senza `kid` solo come HS256 con il segreto. Una chiave pubblica non può quindi diventare
  un segreto HMAC.
- Il JWKS si scarica solo quando arriva un token RS256 e resta in cache per JWKS_TTL_SECONDS.
  Un `kid` sconosciuto lo riscarica al massimo una volta ogni UNKNOWN_KID_MIN_INTERVAL_SECONDS.
- Se il Portal non risponde si continua con le chiavi in cache, senza svuotarla.
- Con PRAMAIA_JWT_REQUIRE_RS256=true l'app rifiuta i token HS256: il segreto condiviso
  non basta più per creare token che l'app accetti (fase 4 della migrazione).

Configurazione (variabili d'ambiente):
- PORTAL_API_URL, oppure PORTAL_URL: indirizzo del Portal, da cui si ricava il JWKS;
- PORTAL_JWKS_URL: URL esplicito del JWKS, se diverso (es. reverse proxy);
- PRAMAIA_JWT_SECRET: segreto per i token HS256, finché servono;
- PRAMAIA_JWT_REQUIRE_RS256: "true" per accettare solo token RS256.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable, Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

VERIFIER_VERSION = "2"

PORTAL_ISSUER = "pramaia-portal"
HMAC_ALGORITHM = "HS256"
ASYMMETRIC_ALGORITHM = "RS256"
JWKS_PATH = "/.well-known/jwks.json"
JWKS_TTL_SECONDS = 300
UNKNOWN_KID_MIN_INTERVAL_SECONDS = 60
# Dopo un download fallito, il prossimo tentativo non prima di così (il Portal è giù).
FAILED_FETCH_RETRY_SECONDS = 30
FETCH_TIMEOUT_SECONDS = 5

DEFAULT_REQUIRED_CLAIMS = ("sub", "exp", "iss")


class PortalTokenError(Exception):
    """Token non valido. `reason` è uno di:

    - "expired": scaduto;
    - "issuer": non emesso dal Portal;
    - "signature": firma HS256 non valida (il segreto potrebbe essere cambiato);
    - "unknown_key": firmato RS256 con una chiave che il Portal non pubblica;
    - "hs256_disabled": HS256 rifiutato da PRAMAIA_JWT_REQUIRE_RS256;
    - "not_configured": manca il segreto (HS256) o l'indirizzo del Portal (RS256);
    - "invalid": qualunque altro problema (formato, firma RS256, claim mancanti, aud...).
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def jwks_url() -> Optional[str]:
    explicit = (os.getenv("PORTAL_JWKS_URL") or "").strip()
    if explicit:
        return explicit
    base = (os.getenv("PORTAL_API_URL") or os.getenv("PORTAL_URL") or "").strip().rstrip("/")
    return f"{base}{JWKS_PATH}" if base else None


class _JwksCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._keys: dict = {}
        self._loaded_at = 0.0
        self._last_attempt = 0.0
        self._last_forced = 0.0

    def reset(self) -> None:
        with self._lock:
            self._keys = {}
            self._loaded_at = self._last_attempt = self._last_forced = 0.0

    def _fetch(self, url: str) -> None:
        """Scarica il JWKS. Chiamato con il lock preso; in caso di errore tiene le chiavi."""
        self._last_attempt = time.monotonic()
        try:
            response = httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS)
            response.raise_for_status()
            keys = {}
            for jwk in response.json().get("keys", []):
                kid = jwk.get("kid")
                if not kid or jwk.get("kty") != "RSA" or jwk.get("alg", ASYMMETRIC_ALGORITHM) != ASYMMETRIC_ALGORITHM:
                    continue
                if jwk.get("use", "sig") != "sig":
                    continue
                keys[kid] = jwt.PyJWK(jwk, algorithm=ASYMMETRIC_ALGORITHM).key
            self._keys = keys
            self._loaded_at = time.monotonic()
        except Exception as e:  # rete, HTTP, JSON o chiave malformata
            logger.warning(
                "JWKS del Portal non disponibile (%s: %s): uso le %d chiavi in cache",
                type(e).__name__, e, len(self._keys),
            )

    def key_for(self, kid: str):
        url = jwks_url()
        if not url:
            raise PortalTokenError(
                "not_configured", "Token RS256 ma PORTAL_API_URL/PORTAL_URL non configurato"
            )
        with self._lock:
            now = time.monotonic()
            stale = now - self._loaded_at > JWKS_TTL_SECONDS
            if stale and now - self._last_attempt > FAILED_FETCH_RETRY_SECONDS:
                self._fetch(url)
            key = self._keys.get(kid)
            if key is None and now - self._last_forced > UNKNOWN_KID_MIN_INTERVAL_SECONDS:
                self._last_forced = now
                self._fetch(url)
                key = self._keys.get(kid)
            return key


_jwks = _JwksCache()


def reset_jwks_cache() -> None:
    """Solo per i test."""
    _jwks.reset()


def verify_portal_token(
    token: str,
    *,
    audience: Optional[str] = None,
    required_claims: Iterable[str] = DEFAULT_REQUIRED_CLAIMS,
    verify_aud: bool = True,
) -> dict:
    """Payload del token se firma, emittente e scadenza sono validi; PortalTokenError altrimenti.

    `audience` si passa come a jwt.decode: se il token ha `aud` e qui non è indicato,
    PyJWT lo rifiuta (stesso comportamento della verifica HS256 precedente).
    Con `verify_aud=False` `aud` non viene controllato: l'app lo verifica da sé.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise PortalTokenError("invalid", f"Invalid token: {e}")

    options = {"require": list(required_claims), "verify_aud": verify_aud}
    kid = header.get("kid")
    try:
        if kid:
            key = _jwks.key_for(str(kid))
            if key is None:
                raise PortalTokenError("unknown_key", "Token signed with a key not published by the Portal")
            return jwt.decode(
                token, key, algorithms=[ASYMMETRIC_ALGORITHM],
                issuer=PORTAL_ISSUER, audience=audience, options=options,
            )

        if _env_flag("PRAMAIA_JWT_REQUIRE_RS256"):
            raise PortalTokenError("hs256_disabled", "HS256 tokens are no longer accepted")
        secret = os.getenv("PRAMAIA_JWT_SECRET")
        if not secret:
            raise PortalTokenError("not_configured", "PRAMAIA_JWT_SECRET non configurato")
        try:
            return jwt.decode(
                token, secret, algorithms=[HMAC_ALGORITHM],
                issuer=PORTAL_ISSUER, audience=audience, options=options,
            )
        except jwt.InvalidSignatureError as e:
            raise PortalTokenError("signature", f"Invalid token: {e}")
    except jwt.ExpiredSignatureError:
        raise PortalTokenError("expired", "Token expired")
    except jwt.InvalidIssuerError:
        raise PortalTokenError("issuer", "Token not issued by PramaIA Portal")
    except jwt.PyJWTError as e:
        raise PortalTokenError("invalid", f"Invalid token: {e}")


def capabilities() -> dict:
    """Da esporre nell'endpoint di stato dell'app, così il Portal sa quali app sono pronte."""
    return {
        "verifier_version": VERIFIER_VERSION,
        "algorithms": [ASYMMETRIC_ALGORITHM] if _env_flag("PRAMAIA_JWT_REQUIRE_RS256")
        else [HMAC_ALGORITHM, ASYMMETRIC_ALGORITHM],
        "jwks_configured": bool(jwks_url()),
    }
