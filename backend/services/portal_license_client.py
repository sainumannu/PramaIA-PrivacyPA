"""
Client per sincronizzazione licenze con PramaIA Portal.

Gestisce:
- Autenticazione service-to-service con app_id + client_secret
- Ottenimento service token JWT
- Sync periodico licenza dal Portal
- Fallback su copia locale se Portal non raggiungibile
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

# Cache token service-to-service in memoria
_token_cache: dict[str, dict] = {}


class PortalLicenseClient:
    """
    Client per comunicare con PramaIA Portal per gestione licenze.
    
    Gestisce autenticazione service-to-service e download licenze.
    """
    
    def __init__(
        self,
        portal_url: str,
        app_id: str,
        client_secret: str,
        cache_file: Path,
        timeout: int = 10,
    ):
        """
        Args:
            portal_url: URL base del Portal (es. https://portal.example.com)
            app_id: ID applicativo registrato nel Portal
            client_secret: Secret per autenticazione service-to-service
            cache_file: Path del file cache locale per fallback offline
            timeout: Timeout richieste HTTP in secondi
        """
        self.portal_url = portal_url.rstrip("/")
        self.app_id = app_id
        self.client_secret = client_secret
        self.cache_file = cache_file
        self.timeout = timeout
        
        if not self.portal_url:
            raise ValueError("PORTAL_URL must be set")
        if not self.app_id:
            raise ValueError("APP_ID must be set")
        if not self.client_secret:
            raise ValueError("APP_CLIENT_SECRET must be set")
    
    def _get_service_token(self) -> str:
        """
        Ottiene service token JWT dal Portal per autenticazione app-to-portal.
        
        Usa cache in memoria per evitare richieste ripetute.
        """
        now = time.time()
        cached = _token_cache.get(self.app_id)
        
        # Verifica cache (con margine 60 sec prima scadenza)
        if cached and cached.get("expires_at", 0) > now + 60:
            return cached["token"]
        
        # Richiedi nuovo token
        try:
            resp = httpx.post(
                f"{self.portal_url}/api/service-auth/token",
                json={
                    "app_id": self.app_id,
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to get service token from Portal: {e}")
        
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("Portal did not return access_token")
        
        # Decodifica exp per cache (senza verifica firma, solo lato client)
        try:
            payload = pyjwt.decode(token, options={"verify_signature": False})
            expires_at = payload.get("exp", now + 1800)  # Default 30 min
        except Exception:
            expires_at = now + 1800
        
        _token_cache[self.app_id] = {
            "token": token,
            "expires_at": expires_at,
        }
        
        logger.info("Service token obtained from Portal")
        return token
    
    def get_license_status(self) -> dict:
        """
        Ottiene stato sintetico licenza dal Portal.
        
        Returns:
            Dict con: app_id, license_id, is_active, status, expires_at,
                     maintenance_until, payload_checksum, uploaded_at
        """
        token = self._get_service_token()
        
        try:
            resp = httpx.get(
                f"{self.portal_url}/api/licenses/apps/{self.app_id}/status",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to get license status from Portal: {e}")
        
        return resp.json()
    
    def get_license_payload(self) -> dict:
        """
        Scarica payload completo licenza dal Portal.
        
        Returns:
            Dict con: app_id, license_id, payload_checksum, uploaded_at,
                     expires_at, maintenance_until, payload (oggetto licenza completo)
        """
        token = self._get_service_token()
        
        try:
            resp = httpx.get(
                f"{self.portal_url}/api/licenses/apps/{self.app_id}/current",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to get license payload from Portal: {e}")
        
        return resp.json()
    
    def sync_license(self) -> dict:
        """
        Sincronizza licenza dal Portal:
        1. Controlla status remoto
        2. Se checksum cambiato, scarica payload completo
        3. Salva copia locale
        4. Se Portal non raggiungibile, usa copia locale
        
        Returns:
            Dict con payload completo licenza (da Portal o cache locale)
        
        Raises:
            RuntimeError: Se Portal non raggiungibile e nessuna copia locale valida
        """
        # Tentativo di sync con Portal
        try:
            status = self.get_license_status()
        except Exception as exc:
            logger.warning(f"Portal unreachable during sync: {exc}")
            logger.info("Falling back to local license cache")
            return self._load_local_or_fail()
        
        # Controlla se copia locale è già aggiornata
        local = self._load_local_metadata()
        remote_checksum = status.get("payload_checksum")
        
        if local and local.get("payload_checksum") == remote_checksum:
            logger.debug("Local license already up-to-date (checksum unchanged)")
            return local
        
        # Checksum cambiato o prima volta: scarica licenza aggiornata
        logger.info("License checksum changed - downloading updated license from Portal")
        
        try:
            full = self.get_license_payload()
        except Exception as exc:
            logger.error(f"Failed to download license from Portal: {exc}")
            # Se download fallisce ma abbiamo copia locale valida, usiamola
            if local:
                logger.warning("Using stale local cache (download failed)")
                return local
            raise RuntimeError(f"Cannot download license and no local cache available: {exc}")
        
        # Salva copia locale
        full["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(full, indent=2),
                encoding="utf-8",
            )
            logger.info(f"License saved to local cache: {self.cache_file}")
        except Exception as exc:
            logger.error(f"Failed to save license to local cache: {exc}")
            # Continua comunque con licenza in memoria
        
        return full
    
    def _load_local_or_fail(self) -> dict:
        """
        Carica copia locale come fallback.
        Fallisce se:
        - File non esiste
        - Licenza scaduta
        """
        if not self.cache_file.exists():
            raise RuntimeError(
                "Portal unreachable and no local license cache found. Application blocked."
            )
        
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"Failed to read local license cache: {e}")
        
        # Verifica scadenza
        expires_at_str = data.get("expires_at")
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if expires_at < datetime.now(timezone.utc):
                    raise RuntimeError(
                        "Portal unreachable and local license expired. Application blocked."
                    )
            except ValueError:
                logger.warning(f"Invalid expires_at format in local cache: {expires_at_str}")
        
        logger.info("Using local license cache (Portal unreachable)")
        return data
    
    def _load_local_metadata(self) -> Optional[dict]:
        """Carica metadata copia locale se esiste (senza validazione)."""
        if not self.cache_file.exists():
            return None
        
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load local license metadata: {e}")
            return None
