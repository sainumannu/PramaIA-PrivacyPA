"""Ricerca globale, dashboard, notifiche in-app, audit e report art. 30."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import (
    get_accessible_unit_ids,
    is_portal_global_or_tenant_admin,
    require_tenant,
)
from backend.auth.privacypa_permissions import assert_permission, require_permission, roles_for
from backend.auth.privacypa_scope import build_scope
from backend.models.privacypa_rules import Severity
from backend.db.database import get_db
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_search_service import SearchService, viewable_modules
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_document_service import DocumentService
from backend.routers.privacypa_findings_router import refreshed_findings
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_processing_service import ProcessingService
from backend.services.privacypa_report_service import art30_to_csv, art30_to_docx, art30_to_pdf, art30_to_xlsx, collect_art30
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_task_service import TaskService
from backend.services.privacypa_risk_service import RiskService
from backend.services.privacypa_transfer_service import TransferService

router = APIRouter(prefix="/api/privacypa", tags=["PrivacyPA - Operazioni"])

_NOTIFIED_FINDINGS = {
    "PROCESSOR_NO_VALID_AGREEMENT": "Responsabile senza atto art. 28 valido:",
    "CONTRACT_EXPIRED": "Contratto scaduto",
    "CONTRACT_EXPIRING": "Contratto in scadenza",
    "SUBPROCESSOR_EXPIRED": "Autorizzazione sub-responsabile scaduta",
    "NOTICE_REVIEW_REQUIRED": "Informativa da verificare",
}
_FINDING_HREF = {"privacy_notice": "/informative", "processing_activity": "/registro", "contract": "/filiera", "subprocessor": "/filiera", "processor_agreement": "/fornitori"}


@router.get("/search")
async def global_search(
    q: str = Query(..., min_length=2),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ricerca su codice, titolo, descrizione e tag (§39). Ciò che l'utente non può vedere non compare né nei risultati né nei conteggi."""
    term = q.strip()
    scope = await build_scope(db, tenant_id, user)
    results, _ = await SearchService(db).find(tenant_id, user, scope, term)
    open_modules = await viewable_modules(db, tenant_id, user, {"document", "task", "transfer", "risk"})
    documents = scope.filter_documents(await DocumentService(db).list_documents(tenant_id, search=term)) if "document" in open_modules else []
    tasks = scope.filter_tasks(await TaskService(db).list_tasks(tenant_id, search=term)) if "task" in open_modules else []
    transfers = [t for t in await TransferService(db).list_transfers(tenant_id) if term.lower() in (t.title + t.code).lower()] if "transfer" in open_modules else []
    risks = [r for r in await RiskService(db).list_risks(tenant_id) if term.lower() in (r.title + r.code).lower()] if "risk" in open_modules else []
    results.update({
        "documents": [{"id": i.id, "code": i.code, "title": i.title} for i in documents],
        "tasks": [{"id": i.id, "code": i.code, "title": i.title, "computed_status": i.computed_status()} for i in tasks],
        "transfers": [{"id": i.id, "code": i.code, "title": i.title, "status": i.status.value} for i in transfers],
        "risks": [{"id": i.id, "code": i.code, "title": i.title} for i in risks],
    })
    return {"query": term, "total": sum(len(v) for v in results.values()), "counts": {k: len(v) for k, v in results.items() if v}, **results}


@router.get("/dashboard")
async def dashboard(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    proc = await ProcessingService(db).summary(tenant_id, accessible_unit_ids=scope.unit_ids)
    task_box = await TaskService(db).overdue_and_upcoming(tenant_id)
    no_owner = await AssetService(db).list_without_owner(tenant_id)
    findings = await refreshed_findings(db, tenant_id, scope)
    agr_review = await PartyService(db).agreements_to_review(tenant_id)
    expiring_docs = scope.filter_documents(await DocumentService(db).expiring(tenant_id))
    return {
        "processing": proc,
        "tasks_overdue": len(scope.filter_tasks(task_box["overdue"])),
        "tasks_due_7": len(scope.filter_tasks(task_box["due_7"])),
        "tasks_due_30": len(scope.filter_tasks(task_box["due_30"])),
        "assets_without_owner": len(no_owner),
        "agreements_to_review": len(agr_review),
        "documents_expiring": len(expiring_docs),
        "dpias_review_due": len(scope.filter_dpias(await DpiaService(db).review_due(tenant_id))),
        "tia_pending": len(await TransferService(db).tia_pending(tenant_id)),
        "transfers_review_due": len(await TransferService(db).review_due(tenant_id)),
        "high_risks": len(await RiskService(db).high_residual(tenant_id)),
        "findings_high": sum(1 for f in findings if f.severity == Severity.HIGH),
        "notices_to_review": sum(1 for f in findings if f.rule_code == "NOTICE_REVIEW_REQUIRED"),
    }


@router.get("/notifications")
async def notifications(
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Notifiche in-app derivate da scadenze (senza dati personali nel testo)."""
    items = []
    scope = await build_scope(db, tenant_id, user)
    task_box = await TaskService(db).overdue_and_upcoming(tenant_id)
    for t in scope.filter_tasks(task_box["overdue"]):
        items.append({"kind": "task_overdue", "title": f"Task scaduto {t.code}", "entity_type": "task", "entity_id": t.id, "href": "/task"})
    for t in scope.filter_tasks(task_box["due_7"]):
        items.append({"kind": "task_due", "title": f"Task in scadenza {t.code}", "entity_type": "task", "entity_id": t.id, "href": "/task"})
    for d in scope.filter_documents(await DocumentService(db).expiring(tenant_id)):
        items.append({"kind": "document_expiring", "title": f"Documento in scadenza {d.code}", "entity_type": "document", "entity_id": d.id, "href": "/documenti"})
    for a in await PartyService(db).agreements_to_review(tenant_id):
        items.append({"kind": "agreement_review", "title": "Atto art. 28 da verificare", "entity_type": "processor_agreement", "entity_id": a.id, "href": "/fornitori"})
    for d in scope.filter_dpias(await DpiaService(db).review_due(tenant_id)):
        items.append({"kind": "dpia_review", "title": f"DPIA da revisionare {d.code}", "entity_type": "dpia", "entity_id": d.id, "href": "/dpia"})
    for t in await TransferService(db).tia_pending(tenant_id):
        items.append({"kind": "tia_pending", "title": f"TIA da completare {t.code}", "entity_type": "transfer", "entity_id": t.id, "href": "/trasferimenti"})
    for t in await TransferService(db).review_due(tenant_id):
        items.append({"kind": "transfer_review", "title": f"Trasferimento da revisionare {t.code}", "entity_type": "transfer", "entity_id": t.id, "href": "/trasferimenti"})
    # Verifiche di filiera e conservazione; il testo non riporta nomi di soggetti. Gli atti art. 28 sono già sopra.
    for f in await refreshed_findings(db, tenant_id, scope):
        if f.rule_code in _NOTIFIED_FINDINGS:
            items.append({"kind": f.rule_code.lower(), "title": f"{_NOTIFIED_FINDINGS[f.rule_code]} {f.entity_code or ''}".strip(),
                          "entity_type": f.entity_type, "entity_id": f.entity_id, "href": _FINDING_HREF[f.entity_type]})
    return {"generated_at": datetime.utcnow().isoformat(), "count": len(items), "items": items}


@router.get("/audit")
async def list_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    entity_type: str | None = None,
    entity_id: str | None = None,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    # L'audit contiene valori vecchi/nuovi di ogni entità e non ha una struttura di
    # riferimento: lo leggono solo ruoli di controllo con visibilità sull'intero ente (§33.2).
    accessible = await get_accessible_unit_ids(
        db,
        tenant_id,
        user.sub or user.email,
        is_portal_global_or_tenant_admin(user),
        roles=await roles_for(db, tenant_id, "audit.view"),
    )
    if accessible is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="L'audit trail è consultabile solo con visibilità sull'intero ente",
        )
    rows = await AuditService(db).list_recent(
        tenant_id, limit=limit, offset=offset, entity_type=entity_type, entity_id=entity_id
    )
    return [r.to_dict() for r in rows]


@router.get("/reports/art30")
async def report_art30(
    format: str = Query("json", alias="format"),
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await assert_permission(user, db, "processing.export")
    accessible = await get_accessible_unit_ids(
        db, tenant_id, user.sub or user.email, is_portal_global_or_tenant_admin(user)
    )
    payload = await collect_art30(db, tenant_id, accessible, user.email or user.sub)
    fmt = (format or "json").lower()
    await AuditService(db).log(
        tenant_id, "report", "export", event_type="report.art30.export",
        actor_user_id=user.sub or user.email,
        metadata={"format": fmt, "records": payload["count"], "scope_limited": accessible is not None},
    )
    if fmt == "csv":
        return Response(
            content=art30_to_csv(payload),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="registro-art30.csv"'},
        )
    if fmt == "xlsx":
        return Response(
            content=art30_to_xlsx(payload),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="registro-art30.xlsx"'},
        )
    if fmt == "docx":
        return Response(
            content=art30_to_docx(payload),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="registro-art30.docx"'},
        )
    if fmt == "pdf":
        return Response(
            content=art30_to_pdf(payload),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="registro-art30.pdf"'},
        )
    return payload
