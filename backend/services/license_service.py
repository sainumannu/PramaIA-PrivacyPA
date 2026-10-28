import os
import hashlib
import platform
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.license_log import LicenseLog
from backend.services.portal_license_client import PortalLicenseClient

logger = logging.getLogger(__name__)


LicenseCheckCode = Literal[
    "OK",
    "LICENSE_MISSING",
    "PAYLOAD_INVALID",
    "SIGNATURE_INVALID",
    "LICENSE_EXPIRED",
    "MAINTENANCE_EXPIRED",
    "ENVIRONMENT_MISMATCH",
    "VERSION_NOT_ALLOWED",
    "MODULE_NOT_LICENSED",
    "MAX_USERS_EXCEEDED",
    "MAX_INSTANCES_EXCEEDED",
]


@dataclass
class LicenseCheckResult:
    """Risultato strutturato della verifica licenza."""
    
    valid: bool
    code: LicenseCheckCode
    message: str
    license_id: Optional[str] = None
    expires_at: Optional[str] = None
    maintenance_until: Optional[str] = None
    allowed_modules: Optional[list[str]] = None
    payload_checksum: Optional[str] = None
    last_sync_at: Optional[str] = None
    sync_status: Optional[str] = None  # "ok", "portal_unreachable", "using_cache"
    
    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "code": self.code,
            "message": self.message,
            "license_id": self.license_id,
            "expires_at": self.expires_at,
            "maintenance_until": self.maintenance_until,
            "allowed_modules": self.allowed_modules,
            "payload_checksum": self.payload_checksum,
            "last_sync_at": self.last_sync_at,
            "sync_status": self.sync_status,
        }


class LicenseService:
    """
    Service per verifica licenza con integrazione Portal centrale.
    
    Modello operativo:
    - Sync periodico con Portal per ottenere licenza aggiornata
    - Salvataggio copia locale come fallback offline
    - Verifica payload secondo specifica tecnica
    - Cache in memoria per performance
    - NO fallback silenziosi
    """
    
    # Configurazione
    APP_VERSION = "1.0.0"  # TODO: leggere da config o __version__
    LICENSE_CACHE_FILE = Path("license_cache.json")
    LICENSE_FILE_PATH = Path("license.jwt")  # Fallback locale (vecchio modello)
    PUBLIC_KEY_PATH = Path("license_public_key.pem")  # Per verifica firma locale
    CACHE_TTL_SECONDS = 300  # 5 minuti cache in memoria
    
    # Cache in memoria, condivisa da tutte le istanze (ogni richiesta crea un LicenseService nuovo)
    _cached_result: Optional[LicenseCheckResult] = None
    _cache_timestamp: Optional[datetime] = None
    _cached_payload: Optional[dict] = None

    @classmethod
    def clear_cache(cls) -> None:
        """Svuota la cache condivisa (test, cambio di licenza)."""
        cls._cached_result = None
        cls._cache_timestamp = None
        cls._cached_payload = None
    
    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self.portal_client: Optional[PortalLicenseClient] = None
        self._init_portal_client()
    
    def _init_portal_client(self) -> None:
        """Inizializza client Portal se configurato."""
        portal_url = os.getenv("PORTAL_URL")
        app_id = os.getenv("APP_ID")
        client_secret = os.getenv("APP_CLIENT_SECRET")
        
        # Client Portal è opzionale per sviluppo locale
        if portal_url and app_id and client_secret:
            try:
                self.portal_client = PortalLicenseClient(
                    portal_url=portal_url,
                    app_id=app_id,
                    client_secret=client_secret,
                    cache_file=self.LICENSE_CACHE_FILE,
                )
                logger.info("Portal license client initialized")
            except ValueError as e:
                logger.warning(f"Failed to initialize Portal client: {e}")
        else:
            logger.warning(
                "Portal integration not configured (PORTAL_URL, APP_ID, or APP_CLIENT_SECRET missing)"
            )
    
    @classmethod
    def _get_local_fingerprint(cls) -> str:
        """
        Genera fingerprint dell'ambiente locale.
        Combina hostname, MAC address principale, OS.
        """
        import uuid
        hostname = platform.node()
        mac = hex(uuid.getnode())
        os_info = platform.system()
        raw = f"{hostname}|{mac}|{os_info}"
        return hashlib.sha256(raw.encode()).hexdigest()
    
    @classmethod
    def _get_deployment_type(cls) -> str:
        """Ritorna il tipo di deployment."""
        return os.getenv("DEPLOYMENT_TYPE", "on_prem")
    
    @classmethod
    def _load_local_license_file(cls) -> Optional[dict]:
        """
        Carica licenza da file locale (vecchio modello).
        Fallback quando Portal non disponibile e nessuna cache.
        
        Returns:
            Payload licenza decodificato o None se file non esiste
        """
        if not cls.LICENSE_FILE_PATH.exists():
            logger.debug(f"Local license file not found: {cls.LICENSE_FILE_PATH}")
            return None
        
        logger.info("Attempting to load local license file (fallback mode)")
        
        try:
            # Carica file
            raw_license = cls.LICENSE_FILE_PATH.read_text(encoding="utf-8").strip()
            
            # Verifica se abbiamo chiave pubblica per verifica firma
            if cls.PUBLIC_KEY_PATH.exists():
                logger.info("Verifying local license signature with public key")
                public_key = cls.PUBLIC_KEY_PATH.read_text(encoding="utf-8")
                
                # Verifica firma JWT
                import jwt as pyjwt
                payload = pyjwt.decode(
                    raw_license,
                    public_key,
                    algorithms=["RS256"],
                    options={"require": ["license_id", "customer", "entitlements", "validity"]},
                )
                logger.info("Local license signature verified successfully")
                return payload
            else:
                # Nessuna chiave pubblica → decodifica senza verifica firma
                logger.warning("Public key not found - decoding license without signature verification")
                import jwt as pyjwt
                payload = pyjwt.decode(
                    raw_license,
                    options={"verify_signature": False},
                )
                return payload
                
        except Exception as e:
            logger.error(f"Failed to load local license file: {e}")
            return None
    
    async def sync_from_portal(self) -> tuple[dict, str]:
        """
        Sincronizza licenza dal Portal.
        
        Returns:
            Tuple (license_data, sync_status)
            sync_status: "ok", "portal_unreachable", "using_cache"
        
        Raises:
            RuntimeError: Se Portal non raggiungibile e no cache valida
        """
        if not self.portal_client:
            raise RuntimeError(
                "Portal client not configured. Set PORTAL_URL, APP_ID, and APP_CLIENT_SECRET."
            )
        
        try:
            license_data = self.portal_client.sync_license()
            return license_data, "ok"
        except RuntimeError as e:
            # Sync fallito, controlla se ha usato cache locale
            if "local license cache" in str(e).lower():
                # Il client ha già caricato la cache locale
                raise
            else:
                # Verifica se c'è cache locale disponibile
                if self.LICENSE_CACHE_FILE.exists():
                    logger.warning(f"Portal sync failed, using local cache: {e}")
                    import json
                    license_data = json.loads(
                        self.LICENSE_CACHE_FILE.read_text(encoding="utf-8")
                    )
                    return license_data, "using_cache"
                else:
                    # Ultimo fallback: prova file licenza locale
                    logger.warning("No cache file found, trying local license file as last fallback")
                    local_payload = self._load_local_license_file()
                    if local_payload:
                        # Wrappa in formato compatibile con cache
                        license_data = {
                            "license_id": local_payload.get("license_id"),
                            "expires_at": local_payload.get("validity", {}).get("expires_at"),
                            "maintenance_until": local_payload.get("validity", {}).get("maintenance_until"),
                            "payload": local_payload,
                            "last_sync_at": None,
                        }
                        logger.info(f"Using local license file: {local_payload.get('license_id')}")
                        return license_data, "local_file"
                    else:
                        raise RuntimeError(f"Portal unreachable, no cache, and no valid local license file: {e}")
    
    @classmethod
    def _validate_required_fields(cls, payload: dict) -> None:
        """Valida che tutti i campi obbligatori siano presenti nel payload."""
        required = ["license_id", "customer", "entitlements", "validity"]
        for field in required:
            if field not in payload:
                raise ValueError(f"Missing required field: {field}")
        
        # Controlli nested
        if "modules" not in payload["entitlements"]:
            raise ValueError("Missing entitlements.modules")
        if "max_users" not in payload["entitlements"]:
            raise ValueError("Missing entitlements.max_users")
        if "max_instances" not in payload["entitlements"]:
            raise ValueError("Missing entitlements.max_instances")
        if "max_version" not in payload["entitlements"]:
            raise ValueError("Missing entitlements.max_version")
        
        if "expires_at" not in payload["validity"]:
            raise ValueError("Missing validity.expires_at")
        if "issued_at" not in payload["validity"]:
            raise ValueError("Missing validity.issued_at")
    
    @classmethod
    def _validate_dates(cls, validity: dict) -> None:
        """Valida date di validità e manutenzione."""
        now = datetime.now(timezone.utc)
        
        issued_at = datetime.fromisoformat(validity["issued_at"].replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(validity["expires_at"].replace("Z", "+00:00"))
        
        if issued_at > now:
            raise ValueError(f"License not yet valid (issued_at: {issued_at})")
        
        if expires_at < now:
            raise ValueError(f"License expired on {expires_at}")
        
        # Maintenance_until è opzionale ma se presente deve essere valido
        if "maintenance_until" in validity:
            maintenance_until = datetime.fromisoformat(
                validity["maintenance_until"].replace("Z", "+00:00")
            )
            if maintenance_until < now:
                raise ValueError(f"Maintenance expired on {maintenance_until}")
    
    @classmethod
    def _validate_environment(cls, env: dict, local_fp: str, local_dt: str) -> None:
        """Valida fingerprint e deployment type."""
        if "fingerprint" in env and env["fingerprint"] != local_fp:
            raise ValueError(
                f"Environment fingerprint mismatch: expected {env['fingerprint']}, got {local_fp}"
            )
        
        if "deployment_type" in env and env["deployment_type"] != local_dt:
            raise ValueError(
                f"Deployment type mismatch: expected {env['deployment_type']}, got {local_dt}"
            )
    
    @classmethod
    def _validate_version(cls, max_version: str, app_version: str) -> None:
        """
        Valida che la versione app sia compatibile con max_version.
        Esempio: max_version="2.x" consente "2.0.0", "2.1.5" ma non "3.0.0"
        """
        # Logica semplificata: confronta major version se max_version contiene "x"
        if "x" in max_version.lower():
            max_major = max_version.split(".")[0]
            app_major = app_version.split(".")[0]
            if app_major != max_major:
                raise ValueError(
                    f"App version {app_version} not allowed (max: {max_version})"
                )
        elif max_version != app_version:
            raise ValueError(
                f"App version {app_version} not allowed (max: {max_version})"
            )
    
    @classmethod
    def _ensure_module_enabled(cls, required_module: str, allowed_modules: list[str]) -> None:
        """Verifica che il modulo richiesto sia nella lista dei moduli licenziati."""
        if required_module not in allowed_modules and "*" not in allowed_modules:
            raise ValueError(
                f"Module '{required_module}' not licensed (allowed: {allowed_modules})"
            )
    
    @classmethod
    def _ensure_max_users(cls, max_users: int, active_users: int) -> None:
        """Verifica che gli utenti attivi non superino il limite."""
        if active_users > max_users:
            raise ValueError(
                f"Active users ({active_users}) exceed license limit ({max_users})"
            )
    
    @classmethod
    def _ensure_max_instances(cls, max_instances: int, running_instances: int) -> None:
        """Verifica che le istanze attive non superino il limite."""
        if running_instances > max_instances:
            raise ValueError(
                f"Running instances ({running_instances}) exceed license limit ({max_instances})"
            )
    
    @classmethod
    async def _get_active_users_count(cls) -> int:
        """
        Conta utenti attivi.
        TODO: implementare logica reale basata su DB o cache.
        """
        # Placeholder: in produzione, query al DB o cache
        return 0
    
    @classmethod
    async def _get_running_instances_count(cls) -> int:
        """
        Conta istanze in esecuzione.
        TODO: implementare logica reale (es. lock file, registry, heartbeat).
        """
        # Placeholder: in produzione, meccanismo di instance tracking
        return 1
    
    async def verify_license(
        self,
        required_module: Optional[str] = None,
        force_refresh: bool = False,
        sync_from_portal: bool = False,
    ) -> LicenseCheckResult:
        """
        Verifica completa della licenza.
        
        Args:
            required_module: modulo richiesto (opzionale)
            force_refresh: ignora cache in memoria e riverifica payload
            sync_from_portal: forza sync con Portal prima della verifica
        
        Returns:
            LicenseCheckResult con esito dettagliato
        """
        cls = type(self)
        cache_fresh = bool(
            cls._cache_timestamp
            and (datetime.now(timezone.utc) - cls._cache_timestamp).total_seconds() < self.CACHE_TTL_SECONDS
        )

        # Cache check (solo se non force_refresh e non sync). Il risultato in cache è sempre valido,
        # ma un modulo richiesto va comunque confrontato con quelli concessi.
        if not force_refresh and not sync_from_portal and cache_fresh and cls._cached_result:
            modules = cls._cached_result.allowed_modules or []
            if required_module is None or required_module in modules or "*" in modules:
                logger.debug("Using cached license check result")
                return cls._cached_result
        
        # Sync dal Portal se richiesto, se non c'è payload in cache o se la cache è scaduta
        # (una licenza rinnovata o revocata nel Portal arriva entro il TTL)
        sync_status = None
        if sync_from_portal or not cls._cached_payload or not cache_fresh:
            # Se Portal configurato, prova sync
            if self.portal_client:
                try:
                    license_data, sync_status = await self.sync_from_portal()
                    payload = license_data.get("payload")
                    if not payload:
                        # Formato diretto (senza wrapping "payload")
                        payload = license_data
                    
                    payload_checksum = license_data.get("payload_checksum")
                    last_sync_at = license_data.get("last_sync_at")
                except RuntimeError as e:
                    logger.error(f"License sync failed: {e}")
                    result = LicenseCheckResult(
                        valid=False,
                        code="LICENSE_MISSING",
                        message=str(e),
                        sync_status="portal_unreachable",
                    )
                    await self._log_check(result, None, "portal_unreachable")
                    return result
            else:
                # Portal non configurato → usa solo file locale
                logger.info("Portal not configured, using local license file only")
                local_payload = self._load_local_license_file()
                if not local_payload:
                    result = LicenseCheckResult(
                        valid=False,
                        code="LICENSE_MISSING",
                        message="Portal not configured and no local license file found",
                        sync_status="no_portal",
                    )
                    await self._log_check(result, None, "no_portal")
                    return result
                
                payload = local_payload
                payload_checksum = None
                last_sync_at = None
                sync_status = "local_file"
        else:
            # Usa payload già in cache
            payload = cls._cached_payload
            payload_checksum = None
            last_sync_at = None
            sync_status = "cached"
        
        # Verifica payload
        result: LicenseCheckResult
        
        try:
            # 1. Valida campi richiesti
            self._validate_required_fields(payload)
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="PAYLOAD_INVALID",
                message=str(e),
                license_id=payload.get("license_id"),
                sync_status=sync_status,
            )
            await self._log_check(result, self._get_local_fingerprint(), sync_status)
            return result
        
        license_id = payload["license_id"]
        local_fingerprint = self._get_local_fingerprint()
        
        try:
            # 2. Valida date
            self._validate_dates(payload["validity"])
        except ValueError as e:
            if "expired on" in str(e).lower():
                if "maintenance" in str(e).lower():
                    code = "MAINTENANCE_EXPIRED"
                else:
                    code = "LICENSE_EXPIRED"
            else:
                code = "PAYLOAD_INVALID"
            
            result = LicenseCheckResult(
                valid=False,
                code=code,
                message=str(e),
                license_id=license_id,
                expires_at=payload["validity"].get("expires_at"),
                maintenance_until=payload["validity"].get("maintenance_until"),
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        try:
            # 3. Valida ambiente (se presente)
            if "environment" in payload:
                self._validate_environment(
                    payload["environment"],
                    local_fingerprint,
                    self._get_deployment_type(),
                )
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="ENVIRONMENT_MISMATCH",
                message=str(e),
                license_id=license_id,
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        try:
            # 4. Valida versione
            self._validate_version(
                payload["entitlements"]["max_version"],
                self.APP_VERSION,
            )
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="VERSION_NOT_ALLOWED",
                message=str(e),
                license_id=license_id,
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        allowed_modules = payload["entitlements"]["modules"]
        
        try:
            # 5. Verifica modulo specifico se richiesto
            if required_module is not None:
                self._ensure_module_enabled(required_module, allowed_modules)
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="MODULE_NOT_LICENSED",
                message=str(e),
                license_id=license_id,
                allowed_modules=allowed_modules,
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        try:
            # 6. Verifica limiti utenti
            active_users = await self._get_active_users_count()
            self._ensure_max_users(payload["entitlements"]["max_users"], active_users)
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="MAX_USERS_EXCEEDED",
                message=str(e),
                license_id=license_id,
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        try:
            # 7. Verifica limiti istanze
            running_instances = await self._get_running_instances_count()
            self._ensure_max_instances(
                payload["entitlements"]["max_instances"],
                running_instances,
            )
        except ValueError as e:
            result = LicenseCheckResult(
                valid=False,
                code="MAX_INSTANCES_EXCEEDED",
                message=str(e),
                license_id=license_id,
                sync_status=sync_status,
            )
            await self._log_check(result, local_fingerprint, sync_status)
            return result
        
        # Tutto OK
        result = LicenseCheckResult(
            valid=True,
            code="OK",
            message="License valid",
            license_id=license_id,
            expires_at=payload["validity"].get("expires_at"),
            maintenance_until=payload["validity"].get("maintenance_until"),
            allowed_modules=allowed_modules,
            payload_checksum=payload_checksum,
            last_sync_at=last_sync_at,
            sync_status=sync_status or "ok",
        )
        
        # Update cache
        cls._cached_result = result
        cls._cache_timestamp = datetime.now(timezone.utc)
        cls._cached_payload = payload
        
        await self._log_check(result, local_fingerprint, sync_status)
        return result
    
    async def _log_check(
        self,
        result: LicenseCheckResult,
        fingerprint: Optional[str],
        sync_status: Optional[str],
    ) -> None:
        """Salva log della verifica per audit."""
        if not self.db:
            return
        
        message = f"{result.message} (sync: {sync_status or 'n/a'})"
        
        log_entry = LicenseLog(
            license_id=result.license_id,
            check_code=result.code,
            message=message,
            fingerprint=fingerprint,
        )
        self.db.add(log_entry)
        try:
            await self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to save license audit log: {e}")
            # Non fallire la verifica per errori di log
            await self.db.rollback()
