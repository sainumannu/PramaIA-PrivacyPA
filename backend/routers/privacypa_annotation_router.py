"""
PrivacyPA — Tag (§78), campi custom (§77), note e commenti (§79).

Le annotazioni di un elemento seguono la sua visibilità: serve poterlo vedere (permesso del modulo + scope della struttura).
Assegnare tag richiede `tag.assign`, scrivere i campi custom il permesso di modifica del modulo dell'elemento, sulla sua
struttura. I commenti hanno una visibilità (TEAM, DPO_ONLY, PRIVATE, ALL_AUTHORIZED) e non sostituiscono le decisioni
formali, che hanno campi e workflow propri.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import get_accessible_unit_ids, is_portal_global_or_tenant_admin, require_tenant
from backend.auth.privacypa_permissions import (
    assert_permission,
    assert_permission_for_unit,
    require_module_view,
    require_permission,
    roles_for,
)
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_annotations import CommentVisibility, CustomFieldType
from backend.models.privacypa_identity import PrivacyRoleCode
from backend.services.privacypa_annotation_service import (
    CUSTOM_FIELD_TYPES,
    KINDS,
    CommentService,
    CustomFieldService,
    EntityRef,
    TagService,
    load_refs,
    resolve_entity,
)
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound
from backend.utils.concurrency import assert_version, etag_of, require_if_match


def _http(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(e, ComplianceNotFound) else status.HTTP_400_BAD_REQUEST, str(e))


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


def _found(entity, label: str):
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} non trovato")
    return entity


async def _entity(db: AsyncSession, tenant_id: str, user: TokenPayload, entity_type: str, entity_id: str, write_code: str | None = None) -> EntityRef:
    """Risolve l'elemento e verifica che l'utente lo veda; con `write_code` anche che possa scrivervi sulla sua struttura."""
    if entity_type not in KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tipo di elemento non supportato")
    ref = _found(await resolve_entity(db, tenant_id, entity_type, entity_id), "Elemento")
    await assert_permission(user, db, f"{ref.module}.view")
    if not ref.visible_to(await build_scope(db, tenant_id, user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    if write_code:
        code = write_code.replace("{module}", ref.module)
        await assert_permission(user, db, code)
        if ref.unit_id or not ref.shared:
            await assert_permission_for_unit(user, db, ref.unit_id, code)
    return ref


# ================================================================ tag

tag_router = APIRouter(prefix="/api/privacypa/tags", tags=["PrivacyPA - Tag"], dependencies=[Depends(require_module_view("tag"))])


class TagIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str | None = Field(None, max_length=7)
    description: str | None = Field(None, max_length=255)
    active: bool = True


class TagSetIn(BaseModel):
    tag_ids: list[str] = Field(default_factory=list, max_length=50)


@tag_router.get("")
async def list_tags(active: bool | None = None, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [t.to_dict(usage) for t, usage in await TagService(db).search(tenant_id, active)]


@tag_router.post("", status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("tag.create")), db: AsyncSession = Depends(get_db),
):
    try:
        tag = await TagService(db).create(tenant_id, _uid(user), body.name, body.color, body.description)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(tag)
    return tag.to_dict(0)


@tag_router.patch("/{tag_id}")
async def update_tag(
    tag_id: str, body: TagIn, response: Response, tenant_id: str = Depends(require_tenant), version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("tag.edit")), db: AsyncSession = Depends(get_db),
):
    svc = TagService(db)
    assert_version(_found(await svc.get(tenant_id, tag_id), "Tag"), version)
    try:
        tag = await svc.update(tenant_id, tag_id, _uid(user), body.name, body.color, body.description, body.active)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(tag)
    return tag.to_dict((await svc.usage(tenant_id)).get(tag.id, 0))


@tag_router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(require_permission("tag.delete")), db: AsyncSession = Depends(get_db)):
    try:
        await TagService(db).delete(tenant_id, tag_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@tag_router.get("/{tag_id}/entities")
async def entities_with_tag(tag_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Gli elementi con il tag, limitati a quelli che l'utente può vedere."""
    svc = TagService(db)
    _found(await svc.get(tenant_id, tag_id), "Tag")
    by_type: dict[str, list[str]] = {}
    for entity_type, entity_id in await svc.tagged(tenant_id, tag_id):
        by_type.setdefault(entity_type, []).append(entity_id)
    scope = await build_scope(db, tenant_id, user)
    out = []
    for entity_type, ids in sorted(by_type.items()):
        kind = KINDS.get(entity_type)
        if kind is None:
            continue
        try:
            await assert_permission(user, db, f"{kind.module}.view")
        except HTTPException:
            continue
        out += [r.brief() for r in await load_refs(db, tenant_id, entity_type, ids) if r.visible_to(scope)]
    return sorted(out, key=lambda r: (r["entity_type"], r["code"] or "", r["entity_id"]))


# ================================================================ definizioni dei campi custom

cf_router = APIRouter(prefix="/api/privacypa/custom-fields", tags=["PrivacyPA - Campi custom"], dependencies=[Depends(require_module_view("customfield"))])


class DefinitionIn(BaseModel):
    entity_type: str = Field(..., max_length=40)
    key: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=100)
    field_type: CustomFieldType
    required: bool = False
    options: list[str] | None = Field(None, max_length=30)
    active: bool = True
    position: int = Field(0, ge=0, le=1000)


@cf_router.get("/types")
async def custom_field_types(user: TokenPayload = Depends(get_current_user)):
    return list(CUSTOM_FIELD_TYPES)


@cf_router.get("")
async def list_definitions(entity_type: str | None = None, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [d.to_dict() for d in await CustomFieldService(db).definitions(tenant_id, entity_type)]


@cf_router.post("", status_code=status.HTTP_201_CREATED)
async def create_definition(
    body: DefinitionIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("customfield.create")), db: AsyncSession = Depends(get_db),
):
    try:
        definition = await CustomFieldService(db).create_definition(
            tenant_id, _uid(user), body.entity_type, body.key, body.label, body.field_type, body.required, body.options, body.position)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(definition)
    return definition.to_dict()


@cf_router.patch("/{definition_id}")
async def update_definition(
    definition_id: str, body: DefinitionIn, response: Response, tenant_id: str = Depends(require_tenant), version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("customfield.edit")), db: AsyncSession = Depends(get_db),
):
    """Tipo di elemento, chiave e tipo del campo non cambiano: se il corpo li indica devono coincidere."""
    svc = CustomFieldService(db)
    current = _found(await svc.get(tenant_id, definition_id), "Campo")
    assert_version(current, version)
    if (body.entity_type, body.key, body.field_type) != (current.entity_type, current.key, current.field_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tipo di elemento, chiave e tipo del campo non si modificano")
    try:
        definition = await svc.update_definition(tenant_id, definition_id, _uid(user), body.label, body.required, body.options, body.active, body.position)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(definition)
    return definition.to_dict()


@cf_router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_definition(definition_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(require_permission("customfield.delete")), db: AsyncSession = Depends(get_db)):
    try:
        await CustomFieldService(db).delete_definition(tenant_id, definition_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


# ================================================================ annotazioni di un elemento

ent_router = APIRouter(prefix="/api/privacypa/entities", tags=["PrivacyPA - Annotazioni degli elementi"])


class CustomValuesIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CommentIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    visibility: CommentVisibility = CommentVisibility.TEAM


async def _comment_reader(db: AsyncSession, tenant_id: str, user: TokenPayload, ref: EntityRef):
    """Funzione che dice se l'utente può leggere un commento dell'elemento (calcola una sola volta ruoli e strutture)."""
    uid = _uid(user)
    dpo_units = await get_accessible_unit_ids(db, tenant_id, uid, False, roles=(PrivacyRoleCode.DPO,))      # gli amministratori non sono DPO
    team_units = await get_accessible_unit_ids(
        db, tenant_id, uid, is_portal_global_or_tenant_admin(user), roles=await roles_for(db, tenant_id, f"{ref.module}.edit"))

    def covers(units: set[str] | None) -> bool:
        if units is None:
            return True
        return ref.unit_id in units if ref.unit_id else bool(ref.shared and units)

    def can_read(comment) -> bool:
        if comment.visibility == CommentVisibility.ALL_AUTHORIZED or comment.author_id == uid:
            return True
        if comment.visibility == CommentVisibility.PRIVATE:
            return False
        return covers(dpo_units if comment.visibility == CommentVisibility.DPO_ONLY else team_units)

    return can_read


def _comment_view(comment, uid: str, moderator: bool) -> dict:
    return {**comment.to_dict(), "own": comment.author_id == uid, "can_delete": comment.author_id == uid or moderator}


@ent_router.get("/{entity_type}/{entity_id}/annotations")
async def get_annotations(entity_type: str, entity_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ref = await _entity(db, tenant_id, user, entity_type, entity_id)
    can_read = await _comment_reader(db, tenant_id, user, ref)
    moderator = True
    try:
        await assert_permission(user, db, "comment.moderate")
    except HTTPException:
        moderator = False
    return {
        "entity": ref.brief(),
        "tags": [t.to_dict() for t in await TagService(db).of_entity(tenant_id, entity_type, entity_id)],
        "custom_fields_supported": entity_type in CUSTOM_FIELD_TYPES,
        "custom_fields": await CustomFieldService(db).for_entity(tenant_id, entity_type, entity_id) if entity_type in CUSTOM_FIELD_TYPES else [],
        "comments": [_comment_view(c, _uid(user), moderator) for c in await CommentService(db).of_entity(tenant_id, entity_type, entity_id) if can_read(c)],
    }


@ent_router.put("/{entity_type}/{entity_id}/tags")
async def set_entity_tags(
    entity_type: str, entity_id: str, body: TagSetIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    ref = await _entity(db, tenant_id, user, entity_type, entity_id, "tag.assign")
    try:
        tags = await TagService(db).set_for_entity(tenant_id, ref, _uid(user), body.tag_ids)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    return [t.to_dict() for t in tags]


@ent_router.put("/{entity_type}/{entity_id}/custom-fields")
async def set_custom_values(
    entity_type: str, entity_id: str, body: CustomValuesIn, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if entity_type not in CUSTOM_FIELD_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "I campi custom non sono previsti per questo tipo di elemento")
    ref = await _entity(db, tenant_id, user, entity_type, entity_id, "{module}.edit")
    try:
        return await CustomFieldService(db).set_values(tenant_id, ref, _uid(user), body.values)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


@ent_router.post("/{entity_type}/{entity_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    entity_type: str, entity_id: str, body: CommentIn, response: Response, tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("comment.create")), db: AsyncSession = Depends(get_db),
):
    ref = await _entity(db, tenant_id, user, entity_type, entity_id)
    try:
        comment = await CommentService(db).create(tenant_id, ref, _uid(user), body.body, body.visibility)
    except ComplianceError as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(comment)
    return _comment_view(comment, _uid(user), False)


# ================================================================ modifica ed eliminazione dei commenti

cm_router = APIRouter(prefix="/api/privacypa/comments", tags=["PrivacyPA - Commenti"])


async def _own_visible_comment(db: AsyncSession, tenant_id: str, user: TokenPayload, comment_id: str):
    svc = CommentService(db)
    comment = _found(await svc.get(tenant_id, comment_id), "Commento")
    ref = await _entity(db, tenant_id, user, comment.entity_type, comment.entity_id)
    if not (await _comment_reader(db, tenant_id, user, ref))(comment):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commento non trovato")              # non rivela l'esistenza di ciò che non si può leggere
    return svc, comment


@cm_router.patch("/{comment_id}")
async def update_comment(
    comment_id: str, body: CommentIn, response: Response, tenant_id: str = Depends(require_tenant), version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("comment.create")), db: AsyncSession = Depends(get_db),
):
    svc, comment = await _own_visible_comment(db, tenant_id, user, comment_id)
    if comment.author_id != _uid(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo l'autore modifica il proprio commento")
    assert_version(comment, version)
    try:
        comment = await svc.update(tenant_id, comment_id, _uid(user), body.body, body.visibility)
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)
    response.headers["ETag"] = etag_of(comment)
    return _comment_view(comment, _uid(user), False)


@cm_router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(comment_id: str, tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """L'autore elimina il proprio commento; chi modera (governo) elimina quelli che può leggere."""
    svc, comment = await _own_visible_comment(db, tenant_id, user, comment_id)
    if comment.author_id != _uid(user):
        await assert_permission(user, db, "comment.moderate")
    try:
        await svc.delete(tenant_id, comment_id, _uid(user))
    except (ComplianceError, ComplianceNotFound) as e:
        raise _http(e)


router = APIRouter()
router.include_router(tag_router)
router.include_router(cf_router)
router.include_router(ent_router)
router.include_router(cm_router)
