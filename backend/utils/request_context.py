"""Contesto per-richiesta (request id) condiviso da middleware, log e audit trail (§33.1, §95)."""
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Persona per conto della quale chiama un account di servizio (header X-On-Behalf-Of): l'audit la registra accanto al servizio.
on_behalf_of_var: ContextVar[str | None] = ContextVar("on_behalf_of", default=None)
# Ruolo con cui l'utente sceglie di agire (header X-Active-Role, selettore della sidebar): le sue assegnazioni si
# limitano a quel ruolo e gli amministratori del Portal perdono la scorciatoia. Restringe soltanto: un ruolo che
# l'utente non ha non concede nulla. Vale per le letture dei ruoli del richiedente (le sole fatte in una richiesta).
active_role_var: ContextVar[str | None] = ContextVar("active_role", default=None)

# Gli account di servizio del Portal hanno `sub = "app:<app_id>"` (POST /api/service-auth/token).
SERVICE_ACCOUNT_PREFIX = "app:"


def get_request_id() -> str | None:
    return request_id_var.get()


def get_on_behalf_of() -> str | None:
    return on_behalf_of_var.get()


def get_active_role() -> str | None:
    return active_role_var.get()


def is_service_account(user_id: str | None) -> bool:
    return bool(user_id) and user_id.startswith(SERVICE_ACCOUNT_PREFIX)
