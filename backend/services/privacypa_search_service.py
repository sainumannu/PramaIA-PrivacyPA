"""
PrivacyPA — Ricerca globale (§39).

Cerca in codice, titolo, descrizione e tag dei tipi di elemento che hanno un modulo (`annotation_service.KINDS`). I
record non autorizzati non compaiono né nei risultati né nei conteggi: il filtro (permesso di visualizzazione del modulo,
struttura, livello di riservatezza) si applica prima di contare, non dopo.

`viewable_modules` e `escape_like` servono anche al query builder.
"""
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import exists, or_, select

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import is_portal_global_or_tenant_admin
from backend.auth.privacypa_permissions import effective_roles, get_policy
from backend.models.privacypa_annotations import EntityTag, Tag
from backend.services.privacypa_annotation_service import KINDS, refs_for_rows
from backend.services.privacypa_identity_service import IdentityService

MIN_TERM = 2


def escape_like(term: str) -> str:
    """Neutralizza i caratteri jolly dell'utente (`%`, `_`) in un LIKE con escape `\\`."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def viewable_modules(db, tenant_id: str, user: TokenPayload, modules: Iterable[str]) -> set[str]:
    """Moduli di cui l'utente ha `<modulo>.view` (a qualsiasi scope): il ruolo può valere solo su alcune strutture."""
    modules = set(modules)
    if is_portal_global_or_tenant_admin(user):
        return modules
    policy = await get_policy(db, tenant_id)
    overrides = policy.permission_overrides if policy else None
    assignments = await IdentityService(db).list_user_assignments(tenant_id, user.sub or user.email, active_only=True)
    mine = {a.role_code for a in assignments}
    return {m for m in modules if mine & set(effective_roles(f"{m}.view", overrides))}


@dataclass(frozen=True)
class SearchDomain:
    key: str                          # chiave nella risposta (compatibile con i risultati storici)
    columns: tuple[str, ...]          # attributi in cui cercare
    shown: tuple[str, ...]            # attributi restituiti


DOMAINS: dict[str, SearchDomain] = {
    "processing_activity": SearchDomain("processing_activities", ("code", "title", "description"), ("code", "title", "status")),
    "dpia": SearchDomain("dpias", ("code", "title", "description"), ("code", "title", "status")),
    "asset": SearchDomain("assets", ("code", "name", "description"), ("code", "name")),
    "party": SearchDomain("parties", ("code", "legal_name"), ("code", "legal_name", "party_type")),
    "contract": SearchDomain("contracts", ("code", "title"), ("code", "title", "status")),
    "privacy_notice": SearchDomain("notices", ("code", "title"), ("code", "title", "status")),
    "data_breach": SearchDomain("data_breaches", ("code", "title", "description"), ("code", "title", "status")),
    "project": SearchDomain("projects", ("code", "title", "description"), ("code", "title", "status")),
    "corrective_action": SearchDomain("corrective_actions", ("code", "title", "description"), ("code", "title", "status")),
    "audit": SearchDomain("audits", ("code", "title", "summary"), ("code", "title", "status")),
}
# Le richieste degli interessati non hanno titolo e portano dati del richiedente: non si cercano qui.


def _shown(row, attrs: tuple[str, ...]) -> dict:
    out = {"id": row.id}
    for attr in attrs:
        value = getattr(row, attr)
        out[attr] = getattr(value, "value", value)
    return out


class SearchService:
    def __init__(self, db):
        self.db = db

    async def find(self, tenant_id: str, user: TokenPayload, scope, term: str) -> tuple[dict[str, list[dict]], set[str]]:
        """Risultati per dominio (solo moduli visibili all'utente) e insieme dei moduli consultabili."""
        like = f"%{escape_like(term.strip())}%"
        allowed = await viewable_modules(self.db, tenant_id, user, {k.module for k in KINDS.values()})
        found: dict[str, list[dict]] = {}
        for entity_type, domain in DOMAINS.items():
            kind = KINDS[entity_type]
            model = kind.model
            if kind.module not in allowed:
                found[domain.key] = []
                continue
            tagged = exists(select(EntityTag.id).join(Tag, Tag.id == EntityTag.tag_id).where(
                EntityTag.entity_type == entity_type, EntityTag.entity_id == model.id, EntityTag.tenant_id == tenant_id,
                Tag.name.ilike(like, escape="\\")))
            stmt = select(model).where(
                model.tenant_id == tenant_id,
                or_(*[getattr(model, c).ilike(like, escape="\\") for c in domain.columns], tagged),
            )
            if hasattr(model, "deleted_at"):
                stmt = stmt.where(model.deleted_at.is_(None))
            if entity_type == "processing_activity":
                stmt = stmt.where(model.is_current.is_(True))
            rows = list((await self.db.execute(stmt.order_by(model.code))).scalars().all())
            refs = {r.entity_id: r for r in await refs_for_rows(self.db, entity_type, rows)}
            found[domain.key] = [_shown(r, domain.shown) for r in rows if refs[r.id].visible_to(scope)]
        return found, allowed
