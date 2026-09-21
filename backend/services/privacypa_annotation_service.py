"""
PrivacyPA — Tag (§78), campi custom (§77), note e commenti (§79).

`EntityRef` risolve un elemento qualsiasi (trattamento, DPIA, sistema, violazione...) a codice, titolo e struttura, per
verificarne l'esistenza nell'ente e la visibilità dello scope. Audit e outbox non portano mai il testo dei commenti né i
valori dei campi custom (possono contenere dati personali): solo chi e cosa è cambiato.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from backend.models.privacypa_annotations import (
    Comment,
    CommentVisibility,
    CustomFieldDefinition,
    CustomFieldType,
    CustomFieldValue,
    EntityTag,
    Tag,
)
from backend.models.privacypa_assessments import Dpia
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_compliance import CorrectiveAction, DataBreach, DataSubjectRequest, PrivacyAudit
from backend.models.privacypa_notices import PrivacyNotice
from backend.models.privacypa_parties import Contract, Party
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_projects import Project
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound, _Base

MAX_DEFINITIONS_PER_TYPE = 20
MAX_TAGS_PER_ENTITY = 20
MAX_COMMENT = 4000
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class EntityKind:
    model: type
    module: str                     # modulo dei permessi (`<modulo>.view` / `<modulo>.edit`)
    title: str | None = "title"     # attributo del titolo (None = non esporlo)
    unit: str | None = None         # attributo della struttura
    shared: bool = False            # registro condiviso a livello ente (nessuno scope per struttura)
    via_processing: bool = False    # la struttura è quella del trattamento collegato (DPIA)


KINDS: dict[str, EntityKind] = {
    "processing_activity": EntityKind(ProcessingActivity, "processing", unit="organizational_unit_id"),
    "dpia": EntityKind(Dpia, "dpia", via_processing=True),
    "asset": EntityKind(Asset, "asset", title="name", shared=True),
    "party": EntityKind(Party, "party", title="legal_name", shared=True),
    "contract": EntityKind(Contract, "party", shared=True),
    "privacy_notice": EntityKind(PrivacyNotice, "notice", unit="owner_unit_id"),
    "data_breach": EntityKind(DataBreach, "breach", unit="organizational_unit_id"),
    "data_subject_request": EntityKind(DataSubjectRequest, "dsr", title=None, unit="organizational_unit_id"),
    "corrective_action": EntityKind(CorrectiveAction, "action", unit="organizational_unit_id"),
    "audit": EntityKind(PrivacyAudit, "auditplan", unit="organizational_unit_id"),
    "project": EntityKind(Project, "project", unit="proposing_unit_id"),
}
# I campi custom non sostituiscono il modello core: solo dove serve e mai su dati sensibili (violazioni, richieste).
CUSTOM_FIELD_TYPES = ("processing_activity", "dpia", "asset", "party", "contract", "privacy_notice", "project")


@dataclass
class EntityRef:
    entity_type: str
    entity_id: str
    code: str | None
    title: str | None
    unit_id: str | None
    processing_id: str | None
    shared: bool
    module: str
    level: Any = None                # livello di riservatezza (§134), se il tipo lo ha
    personal: tuple = ()             # autore e responsabile: leggono il proprio elemento a prescindere dal livello

    def visible_to(self, scope) -> bool:
        """Visibilità dello scope organizzativo (per struttura, oppure registro condiviso) e del livello di riservatezza."""
        if self.level is not None and not scope.level_ok(self.level, *self.personal):
            return False
        if self.unit_id:
            return scope.unit_visible(self.unit_id)
        return self.shared or scope.unrestricted

    def brief(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id, "code": self.code, "title": self.title}


async def load_refs(db, tenant_id: str, entity_type: str, ids: list[str] | None = None) -> list[EntityRef]:
    kind = KINDS.get(entity_type)
    if kind is None:
        return []
    stmt = select(kind.model).where(kind.model.tenant_id == tenant_id)
    if hasattr(kind.model, "deleted_at"):
        stmt = stmt.where(kind.model.deleted_at.is_(None))
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(kind.model.id.in_(ids))
    return await refs_for_rows(db, entity_type, list((await db.execute(stmt)).scalars().all()))


async def refs_for_rows(db, entity_type: str, rows: list) -> list[EntityRef]:
    """Riferimenti (struttura, livello, autore) per righe già lette del tipo indicato."""
    kind = KINDS[entity_type]
    units: dict[str, str | None] = {}
    if kind.via_processing:
        pids = {r.processing_activity_id for r in rows if r.processing_activity_id}
        if pids:
            found = await db.execute(select(ProcessingActivity.id, ProcessingActivity.organizational_unit_id).where(ProcessingActivity.id.in_(pids)))
            units = {pid: unit for pid, unit in found.all()}
    refs = []
    for r in rows:
        if kind.via_processing:
            processing_id = r.processing_activity_id
            unit_id = units.get(processing_id) if processing_id else None
        else:
            processing_id = r.id if entity_type == "processing_activity" else None
            unit_id = getattr(r, kind.unit) if kind.unit else None
        refs.append(EntityRef(entity_type, r.id, getattr(r, "code", None), getattr(r, kind.title) if kind.title else None,
                              unit_id, processing_id, kind.shared, kind.module, getattr(r, "confidentiality_level", None),
                              tuple(x for x in (getattr(r, "created_by", None), getattr(r, "project_owner_id", None)) if x)))
    return refs


async def resolve_entity(db, tenant_id: str, entity_type: str, entity_id: str) -> EntityRef | None:
    refs = await load_refs(db, tenant_id, entity_type, [entity_id])
    return refs[0] if refs else None


# ================================================================ tag

class TagService(_Base):
    def _q(self, tenant_id: str):
        return select(Tag).where(Tag.tenant_id == tenant_id)

    async def get(self, tenant_id: str, tag_id: str) -> Tag | None:
        return (await self.db.execute(self._q(tenant_id).where(Tag.id == tag_id))).scalar_one_or_none()

    async def usage(self, tenant_id: str) -> dict[str, int]:
        rows = await self.db.execute(select(EntityTag.tag_id, func.count()).where(EntityTag.tenant_id == tenant_id).group_by(EntityTag.tag_id))
        return {tag_id: count for tag_id, count in rows.all()}

    async def search(self, tenant_id: str, active: bool | None = None) -> list[tuple[Tag, int]]:
        stmt = self._q(tenant_id)
        if active is not None:
            stmt = stmt.where(Tag.active == active)
        usage = await self.usage(tenant_id)
        return [(t, usage.get(t.id, 0)) for t in (await self.db.execute(stmt.order_by(Tag.name_key))).scalars().all()]

    @staticmethod
    def _clean(name: str, color: str | None, description: str | None) -> tuple[str, str | None, str | None]:
        name = " ".join((name or "").split())
        if not 1 <= len(name) <= 60:
            raise ComplianceError("Il nome del tag deve avere da 1 a 60 caratteri")
        color = (color or "").strip() or None
        if color and not _COLOR.match(color):
            raise ComplianceError("Il colore deve essere nel formato #RRGGBB")
        return name, color, (description or "").strip()[:255] or None

    async def create(self, tenant_id: str, actor: str, name: str, color: str | None = None, description: str | None = None) -> Tag:
        name, color, description = self._clean(name, color, description)
        if (await self.db.execute(self._q(tenant_id).where(Tag.name_key == name.casefold()))).first():
            raise ComplianceError("Esiste già un tag con questo nome")
        tag = Tag(tenant_id=tenant_id, name=name, name_key=name.casefold(), color=color, description=description, created_by=actor)
        self.db.add(tag)
        await self.db.flush()
        await self.audit.log(tenant_id, "tag", "create", entity_id=tag.id, actor_user_id=actor, new_values={"name": name})
        return tag

    async def update(self, tenant_id: str, tag_id: str, actor: str, name: str, color: str | None, description: str | None, active: bool) -> Tag:
        tag = await self.get(tenant_id, tag_id)
        if tag is None:
            raise ComplianceNotFound("Tag non trovato")
        name, color, description = self._clean(name, color, description)
        clash = (await self.db.execute(self._q(tenant_id).where(Tag.name_key == name.casefold(), Tag.id != tag_id))).first()
        if clash:
            raise ComplianceError("Esiste già un tag con questo nome")
        tag.name, tag.name_key, tag.color, tag.description, tag.active = name, name.casefold(), color, description, active
        tag.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "tag", "update", entity_id=tag.id, actor_user_id=actor, new_values={"name": name, "active": active})
        return tag

    async def delete(self, tenant_id: str, tag_id: str, actor: str) -> None:
        tag = await self.get(tenant_id, tag_id)
        if tag is None:
            raise ComplianceNotFound("Tag non trovato")
        if (await self.usage(tenant_id)).get(tag_id):
            raise ComplianceError("Il tag è assegnato a degli elementi: disattivarlo, oppure toglierlo prima")
        await self.db.delete(tag)
        await self.db.flush()
        await self.audit.log(tenant_id, "tag", "delete", entity_id=tag_id, actor_user_id=actor, old_values={"name": tag.name})

    async def of_entity(self, tenant_id: str, entity_type: str, entity_id: str) -> list[Tag]:
        rows = await self.db.execute(
            select(Tag).join(EntityTag, EntityTag.tag_id == Tag.id).where(
                EntityTag.tenant_id == tenant_id, EntityTag.entity_type == entity_type, EntityTag.entity_id == entity_id).order_by(Tag.name_key))
        return list(rows.scalars().all())

    async def set_for_entity(self, tenant_id: str, ref: EntityRef, actor: str, tag_ids: list[str]) -> list[Tag]:
        wanted = list(dict.fromkeys(tag_ids))
        if len(wanted) > MAX_TAGS_PER_ENTITY:
            raise ComplianceError(f"Al massimo {MAX_TAGS_PER_ENTITY} tag per elemento")
        found = {t.id: t for t in (await self.db.execute(self._q(tenant_id).where(Tag.id.in_(wanted)))).scalars().all()} if wanted else {}
        if set(wanted) - set(found):
            raise ComplianceError("Tag non trovato")
        current = {row.tag_id: row for row in (await self.db.execute(select(EntityTag).where(
            EntityTag.tenant_id == tenant_id, EntityTag.entity_type == ref.entity_type, EntityTag.entity_id == ref.entity_id))).scalars().all()}
        added = [t for t in wanted if t not in current]
        if any(not found[t].active for t in added):
            raise ComplianceError("Un tag disattivato non si assegna più")
        removed = [t for t in current if t not in wanted]
        for tag_id in removed:
            await self.db.delete(current[tag_id])
        for tag_id in added:
            self.db.add(EntityTag(tenant_id=tenant_id, tag_id=tag_id, entity_type=ref.entity_type, entity_id=ref.entity_id, assigned_by=actor))
        await self.db.flush()
        if added or removed:
            await self.audit.log(tenant_id, "tag", "assign", event_type="tag.assigned", entity_id=ref.entity_id, actor_user_id=actor,
                                 new_values={"entity_type": ref.entity_type, "added": len(added), "removed": len(removed)})
        return await self.of_entity(tenant_id, ref.entity_type, ref.entity_id)

    async def tagged(self, tenant_id: str, tag_id: str) -> list[tuple[str, str]]:
        rows = await self.db.execute(select(EntityTag.entity_type, EntityTag.entity_id).where(
            EntityTag.tenant_id == tenant_id, EntityTag.tag_id == tag_id))
        return [(t, i) for t, i in rows.all()]


# ================================================================ campi custom

def check_value(definition: CustomFieldDefinition, value: Any) -> Any:
    """Valida e normalizza un valore secondo il tipo del campo; None (o vuoto) significa «nessun valore»."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = definition.field_type
    label = definition.label
    if kind == CustomFieldType.TEXT:
        if not isinstance(value, str) or len(value.strip()) > 1000:
            raise ComplianceError(f"«{label}»: testo fino a 1000 caratteri")
        return value.strip()
    if kind == CustomFieldType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) >= 1e12:
            raise ComplianceError(f"«{label}»: numero non valido")
        return value
    if kind == CustomFieldType.BOOLEAN:
        if not isinstance(value, bool):
            raise ComplianceError(f"«{label}»: serve vero o falso")
        return value
    if kind == CustomFieldType.DATE:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise ComplianceError(f"«{label}»: data non valida (AAAA-MM-GG)") from None
    if value not in (definition.options or []):
        raise ComplianceError(f"«{label}»: scegliere uno dei valori previsti")
    return value


class CustomFieldService(_Base):
    def _q(self, tenant_id: str):
        return select(CustomFieldDefinition).where(CustomFieldDefinition.tenant_id == tenant_id)

    async def get(self, tenant_id: str, definition_id: str) -> CustomFieldDefinition | None:
        return (await self.db.execute(self._q(tenant_id).where(CustomFieldDefinition.id == definition_id))).scalar_one_or_none()

    async def definitions(self, tenant_id: str, entity_type: str | None = None, active_only: bool = False) -> list[CustomFieldDefinition]:
        stmt = self._q(tenant_id)
        if entity_type:
            stmt = stmt.where(CustomFieldDefinition.entity_type == entity_type)
        if active_only:
            stmt = stmt.where(CustomFieldDefinition.active.is_(True))
        rows = await self.db.execute(stmt.order_by(CustomFieldDefinition.entity_type, CustomFieldDefinition.position, CustomFieldDefinition.key))
        return list(rows.scalars().all())

    @staticmethod
    def _clean_options(field_type: CustomFieldType, options: list[str] | None) -> list[str] | None:
        if field_type != CustomFieldType.SELECT:
            return None
        clean = list(dict.fromkeys((o or "").strip() for o in (options or []) if (o or "").strip()))
        if not 1 <= len(clean) <= 30 or any(len(o) > 100 for o in clean):
            raise ComplianceError("Un campo a scelta ha da 1 a 30 valori, ciascuno fino a 100 caratteri")
        return clean

    async def _used(self, definition_id: str) -> list[Any]:
        rows = await self.db.execute(select(CustomFieldValue.value).where(CustomFieldValue.definition_id == definition_id))
        return [v for (v,) in rows.all()]

    async def create_definition(
        self, tenant_id: str, actor: str, entity_type: str, key: str, label: str, field_type: CustomFieldType, required: bool = False,
        options: list[str] | None = None, position: int = 0,
    ) -> CustomFieldDefinition:
        if entity_type not in CUSTOM_FIELD_TYPES:
            raise ComplianceError("I campi custom non sono previsti per questo tipo di elemento")
        key, label = (key or "").strip(), (label or "").strip()
        if not _KEY.match(key):
            raise ComplianceError("La chiave usa minuscole, cifre e trattino basso (2-40 caratteri, inizia con una lettera)")
        if not 1 <= len(label) <= 100:
            raise ComplianceError("L'etichetta deve avere da 1 a 100 caratteri")
        existing = await self.definitions(tenant_id, entity_type)
        if len(existing) >= MAX_DEFINITIONS_PER_TYPE:
            raise ComplianceError(f"Al massimo {MAX_DEFINITIONS_PER_TYPE} campi custom per tipo di elemento: i campi custom non sostituiscono il modello")
        if any(d.key == key for d in existing):
            raise ComplianceError("Esiste già un campo con questa chiave")
        definition = CustomFieldDefinition(
            tenant_id=tenant_id, entity_type=entity_type, key=key, label=label, field_type=field_type, required=required,
            options=self._clean_options(field_type, options), position=position, created_by=actor,
        )
        self.db.add(definition)
        await self.db.flush()
        await self.audit.log(tenant_id, "custom_field", "create", entity_id=definition.id, actor_user_id=actor,
                             new_values={"entity_type": entity_type, "key": key, "type": field_type.value})
        return definition

    async def update_definition(
        self, tenant_id: str, definition_id: str, actor: str, label: str, required: bool, options: list[str] | None, active: bool, position: int,
    ) -> CustomFieldDefinition:
        definition = await self.get(tenant_id, definition_id)
        if definition is None:
            raise ComplianceNotFound("Campo non trovato")
        label = (label or "").strip()
        if not 1 <= len(label) <= 100:
            raise ComplianceError("L'etichetta deve avere da 1 a 100 caratteri")
        new_options = self._clean_options(definition.field_type, options)
        if new_options is not None:
            dropped = {v for v in await self._used(definition.id) if v not in new_options}
            if dropped:
                raise ComplianceError("Un valore già in uso non si può togliere dalle opzioni")
        definition.label, definition.required, definition.options = label, required, new_options
        definition.active, definition.position, definition.updated_at = active, position, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "custom_field", "update", entity_id=definition.id, actor_user_id=actor,
                             new_values={"key": definition.key, "active": active, "required": required})
        return definition

    async def delete_definition(self, tenant_id: str, definition_id: str, actor: str) -> None:
        definition = await self.get(tenant_id, definition_id)
        if definition is None:
            raise ComplianceNotFound("Campo non trovato")
        if await self._used(definition.id):
            raise ComplianceError("Il campo ha dei valori: disattivarlo invece di eliminarlo")
        await self.db.delete(definition)
        await self.db.flush()
        await self.audit.log(tenant_id, "custom_field", "delete", entity_id=definition_id, actor_user_id=actor, old_values={"key": definition.key})

    async def values(self, tenant_id: str, entity_type: str, entity_id: str) -> dict[str, Any]:
        rows = await self.db.execute(select(CustomFieldValue).where(
            CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.entity_type == entity_type, CustomFieldValue.entity_id == entity_id))
        return {v.definition_id: v.value for v in rows.scalars().all()}

    async def for_entity(self, tenant_id: str, entity_type: str, entity_id: str) -> list[dict]:
        """Campi attivi con il valore corrente. I campi disattivati con un valore restano visibili (sola lettura)."""
        current = await self.values(tenant_id, entity_type, entity_id)
        out = []
        for d in await self.definitions(tenant_id, entity_type):
            if d.active or current.get(d.id) is not None:
                out.append({**d.to_dict(), "value": current.get(d.id)})
        return out

    async def set_values(self, tenant_id: str, ref: EntityRef, actor: str, values: dict[str, Any]) -> list[dict]:
        if ref.entity_type not in CUSTOM_FIELD_TYPES:
            raise ComplianceError("I campi custom non sono previsti per questo tipo di elemento")
        definitions = {d.key: d for d in await self.definitions(tenant_id, ref.entity_type)}
        unknown = set(values) - set(definitions)
        if unknown:
            raise ComplianceError(f"Campi sconosciuti: {', '.join(sorted(unknown))}")
        if any(not definitions[k].active for k in values):
            raise ComplianceError("Un campo disattivato non si modifica")
        current = {row.definition_id: row for row in (await self.db.execute(select(CustomFieldValue).where(
            CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.entity_type == ref.entity_type,
            CustomFieldValue.entity_id == ref.entity_id))).scalars().all()}
        clean = {key: check_value(definitions[key], value) for key, value in values.items()}
        final = {d.id: (clean[d.key] if d.key in clean else (current[d.id].value if d.id in current else None)) for d in definitions.values()}
        missing = [d.label for d in definitions.values() if d.active and d.required and final[d.id] is None]
        if missing:
            raise ComplianceError(f"Campi obbligatori senza valore: {', '.join(missing)}")
        changed = []
        for key, value in clean.items():
            definition, row = definitions[key], current.get(definitions[key].id)
            if value is None:
                if row is not None:
                    await self.db.delete(row)
                    changed.append(key)
            elif row is None:
                self.db.add(CustomFieldValue(tenant_id=tenant_id, definition_id=definition.id, entity_type=ref.entity_type,
                                             entity_id=ref.entity_id, value=value, updated_by=actor))
                changed.append(key)
            elif row.value != value:
                row.value, row.updated_by, row.updated_at = value, actor, datetime.utcnow()
                changed.append(key)
        await self.db.flush()
        if changed:
            await self.audit.log(tenant_id, "custom_field", "values", event_type="custom_field.values.updated", entity_id=ref.entity_id,
                                 actor_user_id=actor, new_values={"entity_type": ref.entity_type, "keys": sorted(changed)})
        return await self.for_entity(tenant_id, ref.entity_type, ref.entity_id)


# ================================================================ commenti

class CommentService(_Base):
    def _q(self, tenant_id: str):
        return select(Comment).where(Comment.tenant_id == tenant_id, Comment.deleted_at.is_(None))

    async def get(self, tenant_id: str, comment_id: str) -> Comment | None:
        return (await self.db.execute(self._q(tenant_id).where(Comment.id == comment_id))).scalar_one_or_none()

    async def of_entity(self, tenant_id: str, entity_type: str, entity_id: str) -> list[Comment]:
        rows = await self.db.execute(self._q(tenant_id).where(Comment.entity_type == entity_type, Comment.entity_id == entity_id)
                                     .order_by(Comment.created_at, Comment.id))
        return list(rows.scalars().all())

    @staticmethod
    def _body(body: str) -> str:
        body = (body or "").strip()
        if not body:
            raise ComplianceError("Il commento è vuoto")
        if len(body) > MAX_COMMENT:
            raise ComplianceError(f"Il commento supera {MAX_COMMENT} caratteri")
        return body

    async def create(self, tenant_id: str, ref: EntityRef, actor: str, body: str, visibility: CommentVisibility) -> Comment:
        comment = Comment(tenant_id=tenant_id, entity_type=ref.entity_type, entity_id=ref.entity_id, author_id=actor,
                          body=self._body(body), visibility=visibility)
        self.db.add(comment)
        await self.db.flush()
        await self.audit.log(tenant_id, "comment", "create", entity_id=comment.id, actor_user_id=actor,
                             new_values={"entity_type": ref.entity_type, "entity_id": ref.entity_id, "visibility": visibility.value})
        return comment

    async def update(self, tenant_id: str, comment_id: str, actor: str, body: str, visibility: CommentVisibility) -> Comment:
        comment = await self.get(tenant_id, comment_id)
        if comment is None:
            raise ComplianceNotFound("Commento non trovato")
        if comment.author_id != actor:
            raise ComplianceError("Solo l'autore modifica il proprio commento")
        comment.body, comment.visibility, comment.edited_at = self._body(body), visibility, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "comment", "update", entity_id=comment.id, actor_user_id=actor, new_values={"visibility": visibility.value})
        return comment

    async def delete(self, tenant_id: str, comment_id: str, actor: str) -> None:
        comment = await self.get(tenant_id, comment_id)
        if comment is None:
            raise ComplianceNotFound("Commento non trovato")
        comment.deleted_at, comment.deleted_by = datetime.utcnow(), actor
        await self.db.flush()
        await self.audit.log(tenant_id, "comment", "delete", entity_id=comment.id, actor_user_id=actor,
                             old_values={"entity_type": comment.entity_type, "author": comment.author_id})
