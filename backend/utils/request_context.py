"""Contesto per-richiesta (request id) condiviso da middleware, log e audit trail (§33.1, §95)."""
from contextvars import ContextVar
from dataclasses import dataclass

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Persona per conto della quale chiama un account di servizio (header X-On-Behalf-Of): l'audit la registra accanto al servizio.
on_behalf_of_var: ContextVar[str | None] = ContextVar("on_behalf_of", default=None)


@dataclass
class ActiveRole:
    """
    Ruolo con cui l'utente sceglie di agire (header X-Active-Role, selettore della sidebar): le sue assegnazioni si
    limitano a quel ruolo e gli amministratori del Portal perdono la scorciatoia. Un amministratore del Portal può
    scegliere anche un ruolo che non ha: lo esercita sull'intero ente (si toglie poteri, non ne acquista). Per gli
    altri un ruolo non assegnato non concede nulla. Il middleware crea l'oggetto, l'autenticazione lo lega al
    richiedente (`bind_active_role`): vale solo per le assegnazioni di quella persona.
    """
    code: str
    user_id: str | None = None
    portal_admin: bool = False


active_role_var: ContextVar[ActiveRole | None] = ContextVar("active_role", default=None)

# Gli account di servizio del Portal hanno `sub = "app:<app_id>"` (POST /api/service-auth/token).
SERVICE_ACCOUNT_PREFIX = "app:"


def get_request_id() -> str | None:
    return request_id_var.get()


def get_on_behalf_of() -> str | None:
    return on_behalf_of_var.get()


def get_active_role() -> str | None:
    active = active_role_var.get()
    return active.code if active else None


def active_role_binding() -> ActiveRole | None:
    return active_role_var.get()


def bind_active_role(user_id: str, portal_admin: bool) -> None:
    """Lega il ruolo attivo della richiesta al richiedente (la prima autenticazione della richiesta)."""
    active = active_role_var.get()
    if active is not None and active.user_id is None:
        active.user_id, active.portal_admin = user_id, portal_admin


def is_service_account(user_id: str | None) -> bool:
    return bool(user_id) and user_id.startswith(SERVICE_ACCOUNT_PREFIX)
