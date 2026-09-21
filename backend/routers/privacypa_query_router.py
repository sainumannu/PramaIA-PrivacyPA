"""Query builder (§40): schema dei campi, esecuzione ed esportazione dei risultati."""
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import assert_permission
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_organization import Organization
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_query_service import (
    ENTITIES, MAX_CONDITIONS, MAX_PAGE, OPERATORS, QueryError, QueryService, describe, to_csv, to_xlsx,
)
from backend.services.privacypa_search_service import viewable_modules

router = APIRouter(prefix="/api/privacypa/queries", tags=["PrivacyPA - Query builder"])


class Condition(BaseModel):
    field: str = Field(max_length=60)
    operator: str = Field(max_length=20)
    value: Any = None


class QueryIn(BaseModel):
    entity: str = Field(max_length=40)
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    columns: list[str] | None = Field(None, max_length=40)
    sort: str | None = Field(None, max_length=60)
    direction: Literal["asc", "desc"] = "asc"
    limit: int = Field(50, ge=1, le=MAX_PAGE)
    offset: int = Field(0, ge=0)


class ExportIn(QueryIn):
    format: Literal["csv", "xlsx"] = "csv"


def _unprocessable(err: QueryError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(err))


async def _authorized_scope(body: QueryIn, tenant_id: str, user: TokenPayload, db: AsyncSession):
    entity = ENTITIES.get(body.entity)
    if entity is None:
        raise _unprocessable(QueryError(f"Tipo di elemento non interrogabile: {body.entity}"))
    await assert_permission(user, db, f"{entity.module}.view")
    return await build_scope(db, tenant_id, user)


@router.get("/schema")
async def schema(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tipi di elemento interrogabili dall'utente (solo moduli che può consultare), campi e operatori."""
    allowed = await viewable_modules(db, tenant_id, user, {e.module for e in ENTITIES.values()})
    try:
        await assert_permission(user, db, "query.export")
        can_export = True
    except HTTPException:
        can_export = False
    return {
        "operators": list(OPERATORS), "can_export": can_export,
        "entities": [describe(k) for k, e in ENTITIES.items() if e.module in allowed],
    }


@router.post("/run")
async def run_query(
    body: QueryIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await _authorized_scope(body, tenant_id, user, db)
    try:
        return await QueryService(db).run(
            tenant_id, scope, body.entity, body.match, [c.model_dump() for c in body.conditions],
            columns=body.columns, sort=body.sort, direction=body.direction, offset=body.offset, limit=body.limit,
        )
    except QueryError as err:
        raise _unprocessable(err)


@router.post("/export")
async def export_query(
    body: ExportIn,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Esporta tutti i risultati visibili (CSV o Excel): permesso dedicato `query.export`, registrato in audit."""
    await assert_permission(user, db, "query.export")
    scope = await _authorized_scope(body, tenant_id, user, db)
    conditions = [c.model_dump() for c in body.conditions]
    try:
        result = await QueryService(db).export_rows(
            tenant_id, scope, body.entity, body.match, conditions, columns=body.columns, sort=body.sort, direction=body.direction)
    except QueryError as err:
        raise _unprocessable(err)
    actor = user.sub or user.email
    # L'audit riporta campi e operatori, non i valori cercati (possono essere testo libero).
    await AuditService(db).log(
        tenant_id, "query", "export", event_type="query.export", actor_user_id=actor,
        metadata={
            "entity": body.entity, "format": body.format, "rows": len(result["rows"]), "excluded_by_confidentiality": result["excluded"],
            "match": body.match, "conditions": [{"field": c["field"], "operator": c["operator"]} for c in conditions],
        },
    )
    name = f"interrogazione-{body.entity}"
    headers = {"X-Excluded-Rows": str(result["excluded"])}
    if body.format == "xlsx":
        org = (await db.execute(select(Organization.name).where(Organization.tenant_id == tenant_id))).scalar_one_or_none()
        info = [
            ("Ente", org or tenant_id), ("Estratto il", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")), ("Utente", user.email or actor),
            ("Tipo di elemento", body.entity), ("Condizioni", body.match),
            *[(f"  {i + 1}", f"{c['field']} {c['operator']} {c.get('value') if c.get('value') is not None else ''}".strip())
              for i, c in enumerate(conditions)],
            ("Righe", str(len(result["rows"]))), ("Escluse per riservatezza", str(result["excluded"])),
        ]
        return Response(
            content=to_xlsx(result["columns"], result["rows"], info),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={**headers, "Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    return Response(
        content=to_csv(result["columns"], result["rows"]), media_type="text/csv; charset=utf-8",
        headers={**headers, "Content-Disposition": f'attachment; filename="{name}.csv"'},
    )
