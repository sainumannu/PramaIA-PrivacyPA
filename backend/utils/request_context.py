"""Contesto per-richiesta (request id) condiviso da middleware, log e audit trail (§33.1, §95)."""
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()
