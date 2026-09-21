"""
PrivacyPA — Livelli di riservatezza (§134) ed export e stampa riservata (§135, §136).

`RESTRICTED` e `HIGHLY_RESTRICTED` hanno regole configurabili per ente (`TenantPolicy.confidentiality`):

- `roles`: ruoli che leggono gli elementi di quel livello (l'autore e gli amministratori li leggono sempre);
- `step_up`: per scaricare serve una autenticazione recente a più fattori (claim `amr`/`acr` e `auth_time` del token);
- `watermark`: `off`, `if_possible` (PDF, Word, Excel) o `required` (formato non supportato = nessun download);
- `max_downloads_per_hour`: tetto ai download per utente, contro l'esportazione massiva;
- `block_bulk_export`: l'export completo dell'ente non include il contenuto di questi elementi.

`step_up.admin_actions` estende il controllo alle azioni amministrative sensibili (§136). Il server verifica i claim
del token, ma è il Portal (o l'IdP) a dover emettere `amr`/`auth_time` dopo una vera autenticazione a più fattori.
"""
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import is_portal_global_or_tenant_admin
from backend.auth.privacypa_permissions import get_policy
from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.services.privacypa_identity_service import IdentityService

CONFIGURABLE_LEVELS = ("RESTRICTED", "HIGHLY_RESTRICTED")
WATERMARK_MODES = ("off", "if_possible", "required")
ALWAYS = (R.SUPER_ADMIN, R.TENANT_ADMIN)

DEFAULT_LEVELS: dict[str, dict] = {
    "RESTRICTED": {
        "roles": [r.value for r in (R.SUPER_ADMIN, R.TENANT_ADMIN, R.PRIVACY_MANAGER, R.DPO, R.MANAGER, R.PRIVACY_REFERENT, R.AUDITOR)],
        "step_up": False, "watermark": "off", "max_downloads_per_hour": None, "block_bulk_export": False,
    },
    "HIGHLY_RESTRICTED": {
        "roles": [r.value for r in (R.SUPER_ADMIN, R.TENANT_ADMIN, R.PRIVACY_MANAGER, R.DPO)],
        "step_up": False, "watermark": "if_possible", "max_downloads_per_hour": 10, "block_bulk_export": True,
    },
}
DEFAULT_STEP_UP = {"max_age_seconds": 900, "admin_actions": False}
_STRONG_FACTORS = {"mfa", "otp", "hwk", "swk", "fpt", "face", "retina", "sms", "tel"}


def effective_config(stored: dict | None) -> dict:
    """Configurazione effettiva: i default, con sopra ciò che l'ente ha salvato."""
    stored = stored or {}
    levels = {}
    for level, default in DEFAULT_LEVELS.items():
        levels[level] = {**default, **((stored.get("levels") or {}).get(level) or {})}
    return {"levels": levels, "step_up": {**DEFAULT_STEP_UP, **(stored.get("step_up") or {})}}


def validate_config(data: dict) -> dict:
    """Normalizza e valida la configurazione in ingresso; ValueError se non valida. Gli amministratori restano sempre inclusi."""
    valid_roles = {r.value for r in R}
    out: dict = {"levels": {}, "step_up": {}}
    for level, rule in (data.get("levels") or {}).items():
        if level not in CONFIGURABLE_LEVELS:
            raise ValueError(f"Livello non configurabile: {level}")
        roles = list(dict.fromkeys(rule.get("roles") or DEFAULT_LEVELS[level]["roles"]))
        if any(r not in valid_roles for r in roles):
            raise ValueError("Ruolo non valido")
        roles = [r.value for r in R if r.value in roles or r in ALWAYS]
        mode = rule.get("watermark", DEFAULT_LEVELS[level]["watermark"])
        if mode not in WATERMARK_MODES:
            raise ValueError("Modalità di filigrana non valida")
        limit = rule.get("max_downloads_per_hour", DEFAULT_LEVELS[level]["max_downloads_per_hour"])
        if limit is not None and not 1 <= int(limit) <= 1000:
            raise ValueError("Il tetto orario dei download va da 1 a 1000 (vuoto = nessun tetto)")
        out["levels"][level] = {
            "roles": roles, "step_up": bool(rule.get("step_up", False)), "watermark": mode,
            "max_downloads_per_hour": int(limit) if limit is not None else None,
            "block_bulk_export": bool(rule.get("block_bulk_export", DEFAULT_LEVELS[level]["block_bulk_export"])),
        }
    step = data.get("step_up") or {}
    max_age = int(step.get("max_age_seconds", DEFAULT_STEP_UP["max_age_seconds"]))
    if not 60 <= max_age <= 3600:
        raise ValueError("L'anzianità massima dell'autenticazione va da 60 a 3600 secondi")
    out["step_up"] = {"max_age_seconds": max_age, "admin_actions": bool(step.get("admin_actions", False))}
    return out


@dataclass
class LevelAccess:
    """Chi è l'utente ai fini della riservatezza: identità, ruoli attivi (a qualsiasi scope) e regole dell'ente."""

    user_id: str
    roles: set[str]
    is_admin: bool
    config: dict = field(default_factory=lambda: effective_config(None))

    def rule(self, level) -> dict | None:
        return self.config["levels"].get(getattr(level, "value", level))

    def can_read(self, level, *personal) -> bool:
        """Livelli senza regole (INTERNAL e simili) sono aperti; altrimenti serve un ruolo ammesso, o essere l'autore/responsabile."""
        rule = self.rule(level)
        if rule is None or self.is_admin or self.user_id in personal:
            return True
        return bool(self.roles & set(rule["roles"]))

    def step_up_admin(self) -> dict | None:
        step = self.config["step_up"]
        return step if step["admin_actions"] else None


async def load_level_access(db: AsyncSession, tenant_id: str, user: TokenPayload) -> LevelAccess:
    uid = user.sub or user.email
    policy = await get_policy(db, tenant_id)
    assignments = await IdentityService(db).list_user_assignments(tenant_id, uid, active_only=True)
    return LevelAccess(
        user_id=uid, roles={a.role_code.value for a in assignments}, is_admin=is_portal_global_or_tenant_admin(user),
        config=effective_config(getattr(policy, "confidentiality", None) if policy else None),
    )


# ---------------------------------------------------------------- step-up (§135, §136)

def step_up_satisfied(user: TokenPayload, max_age_seconds: int, now: float | None = None) -> bool:
    """Autenticazione a più fattori recente: fattori forti in `amr` (o `acr` con «mfa») e `auth_time` (o `iat`) entro `max_age`."""
    raw = getattr(user, "raw", None) or {}
    amr = raw.get("amr") or []
    amr = [amr] if isinstance(amr, str) else [str(a).lower() for a in amr]
    strong = bool(_STRONG_FACTORS & set(amr)) or len(set(amr)) >= 2 or "mfa" in str(raw.get("acr") or "").lower()
    if not strong:
        return False
    try:
        moment = float(raw.get("auth_time") or raw.get("iat"))
    except (TypeError, ValueError):
        return False
    return 0 <= (now if now is not None else time.time()) - moment <= max_age_seconds


def assert_step_up(user: TokenPayload, max_age_seconds: int) -> None:
    """403 con intestazioni dedicate (non 401: l'interfaccia non deve terminare la sessione) se manca un'autenticazione forte recente."""
    if step_up_satisfied(user, max_age_seconds):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=(f"Serve una nuova autenticazione a più fattori (MFA) negli ultimi {max_age_seconds // 60} minuti: "
                "accedere di nuovo dal Portal con il secondo fattore e ripetere l'operazione."),
        headers={"X-Step-Up-Required": "true", "X-Step-Up-Max-Age": str(max_age_seconds)},
    )


async def require_admin_step_up(db: AsyncSession, tenant_id: str, user: TokenPayload) -> None:
    """Azioni amministrative sensibili (§136): richiedono step-up se l'ente lo ha attivato."""
    access = await load_level_access(db, tenant_id, user)
    step = access.step_up_admin()
    if step:
        assert_step_up(user, step["max_age_seconds"])
