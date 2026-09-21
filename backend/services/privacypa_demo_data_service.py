"""
PrivacyPA — Dati di esempio e reset per sezione (uso in Configurazione).

Il reset è un'operazione amministrativa sul tenant corrente: elimina i record
di dominio (e, a richiesta, i file documentali) senza toccare audit trail,
cataloghi globali, regole di conformità o assegnazioni di ruolo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_annotations import (
    Comment,
    CustomFieldDefinition,
    CustomFieldValue,
    EntityTag,
    Tag,
)
from backend.models.privacypa_approvals import Approval
from backend.models.privacypa_assessments import Dpia, DpiaScreeningAnswer, InternationalTransfer, TransferLink
from backend.models.privacypa_assets import Asset, ProcessingAsset
from backend.models.privacypa_compliance import (
    AuditFinding,
    CorrectiveAction,
    DataBreach,
    DataBreachLink,
    DataSubjectRequest,
    LegalReference,
    PrivacyAudit,
    ProcessingLegalReference,
)
from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog
from backend.models.privacypa_documents import Document, DocumentLink, DocumentVersion
from backend.models.privacypa_notices import DataFlow, DataFlowCategory, NoticeProcessingLink, PrivacyNotice, PrivacyNoticeVersion
from backend.models.privacypa_organization import Organization, OrganizationalUnit
from backend.models.privacypa_parties import (
    Contract,
    Party,
    ProcessingProcessor,
    ProcessingRecipient,
    ProcessorAgreement,
    ProcessorProfile,
    Subprocessor,
)
from backend.models.privacypa_people import (
    AnnualReport,
    Authorization,
    AuthorizationProfile,
    DpoOpinion,
    TrainingAssignment,
    TrainingCompletion,
    TrainingCourse,
    TrainingSession,
)
from backend.models.privacypa_processing import (
    ProcessingActivity,
    ProcessingActivityVersion,
    ProcessingDataCategory,
    ProcessingDataSubject,
    ProcessingLegalBasis,
    ProcessingPurpose,
)
from backend.models.privacypa_projects import Project, ProjectArtifact, ProjectReview
from backend.models.privacypa_retention import RetentionRule
from backend.models.privacypa_risk import MeasureImplementation, Risk, RiskAssessment, SecurityMeasure
from backend.models.privacypa_rules import ComplianceFinding
from backend.models.privacypa_tasks import PrivacyTask
from backend.models.privacypa_webhooks import WebhookDelivery, WebhookSubscription
from backend.services.privacypa_audit_service import AuditService
from backend.services import privacypa_document_service as document_storage


@dataclass(frozen=True)
class DemoSection:
    code: str
    label_it: str
    label_en: str
    entity_types: tuple[str, ...]
    has_documents: bool
    group: str


SECTIONS: tuple[DemoSection, ...] = (
    DemoSection("organization", "Organizzazione", "Organization", ("organizational_unit",), False, "entity"),
    DemoSection("processing", "Registro trattamenti", "Processing register", ("processing_activity",), True, "work"),
    DemoSection("catalogs", "Cataloghi dell'ente", "Tenant catalogs", (), False, "entity"),
    DemoSection("assets", "Sistemi e asset", "Systems and assets", ("asset",), True, "work"),
    DemoSection("parties", "Fornitori e responsabili", "Suppliers and processors", ("party",), True, "work"),
    DemoSection("supply", "Filiera (contratti e sub-responsabili)", "Supply chain", ("contract", "subprocessor"), True, "work"),
    DemoSection("documents", "Documenti", "Documents", ("document",), True, "work"),
    DemoSection("notices", "Informative", "Privacy notices", ("privacy_notice", "notice"), True, "work"),
    DemoSection("dataflows", "Flussi di dati", "Data flows", (), False, "work"),
    DemoSection("retention", "Conservazione", "Retention", (), False, "work"),
    DemoSection("tasks", "Task e scadenze", "Tasks", ("task",), True, "work"),
    DemoSection("dpia", "DPIA", "DPIA", ("dpia",), True, "work"),
    DemoSection("transfers", "Trasferimenti internazionali", "International transfers", ("transfer", "international_transfer"), True, "work"),
    DemoSection("risks", "Rischi e misure", "Risks and measures", ("risk",), True, "work"),
    DemoSection("legal_references", "Riferimenti normativi", "Legal references", (), False, "entity"),
    DemoSection("approvals", "Approvazioni", "Approvals", (), False, "work"),
    DemoSection("projects", "Progetti (privacy by design)", "Projects", ("project",), True, "work"),
    DemoSection("training", "Formazione", "Training", (), False, "work"),
    DemoSection("authorizations", "Autorizzati al trattamento", "Authorizations", (), False, "work"),
    DemoSection("breaches", "Data breach", "Data breaches", ("data_breach",), True, "work"),
    DemoSection("requests", "Richieste degli interessati", "Data subject requests", ("data_subject_request", "request"), True, "work"),
    DemoSection("audits", "Audit programmati", "Planned audits", ("audit",), True, "work"),
    DemoSection("actions", "Azioni correttive", "Corrective actions", ("corrective_action",), True, "work"),
    DemoSection("opinions", "Pareri del DPO", "DPO opinions", (), False, "work"),
    DemoSection("annual_reports", "Relazione annuale", "Annual report", (), False, "work"),
    DemoSection("tags", "Tag", "Tags", (), False, "entity"),
    DemoSection("custom_fields", "Campi custom", "Custom fields", (), False, "entity"),
    DemoSection("comments", "Commenti", "Comments", (), False, "entity"),
    DemoSection("findings", "Verifiche di conformità", "Compliance findings", (), False, "tools"),
    DemoSection("webhooks", "Webhook", "Webhooks", (), False, "entity"),
)

SECTION_BY_CODE = {s.code: s for s in SECTIONS}


class DemoDataError(ValueError):
    pass


async def _ids(session: AsyncSession, model, tenant_id: str, extra=None) -> list[str]:
    stmt = select(model.id).where(model.tenant_id == tenant_id)
    if extra is not None:
        stmt = extra(stmt)
    elif hasattr(model, "deleted_at"):
        pass
    return list((await session.execute(stmt)).scalars().all())


async def _count(session: AsyncSession, model, tenant_id: str, *, tenant_only: bool = True, include_deleted: bool = True) -> int:
    stmt = select(func.count()).select_from(model)
    if tenant_only and hasattr(model, "tenant_id"):
        stmt = stmt.where(model.tenant_id == tenant_id)
    if not include_deleted and hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar() or 0


async def _delete_ids(session: AsyncSession, model, column, ids: list[str]) -> int:
    if not ids:
        return 0
    result = await session.execute(delete(model).where(column.in_(ids)))
    return result.rowcount or 0


async def _delete_tenant(session: AsyncSession, model, tenant_id: str) -> int:
    if not hasattr(model, "tenant_id"):
        return 0
    result = await session.execute(delete(model).where(model.tenant_id == tenant_id))
    return result.rowcount or 0


def _unlink_files(keys: list[str]) -> int:
    removed = 0
    root = document_storage.STORAGE_ROOT.resolve()
    for key in keys:
        if not key or key == "pending":
            continue
        path = (document_storage.STORAGE_ROOT / key).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            path.unlink()
            removed += 1
            parent = path.parent
            for _ in range(3):
                if parent == root or not parent.exists():
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return removed


class DemoDataService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def inventory(self, tenant_id: str) -> list[dict]:
        self._tenant = tenant_id
        rows = []
        for section in SECTIONS:
            records = await self._record_count(tenant_id, section.code)
            documents = await self._document_count(tenant_id, section) if section.has_documents else 0
            rows.append({
                "code": section.code,
                "label_it": section.label_it,
                "label_en": section.label_en,
                "group": section.group,
                "records": records,
                "documents": documents,
                "has_documents": section.has_documents,
            })
        return rows

    async def _record_count(self, tenant_id: str, code: str) -> int:
        counts: dict[str, Callable] = {
            "organization": lambda: _count(self.db, OrganizationalUnit, tenant_id),
            "processing": lambda: _count(self.db, ProcessingActivity, tenant_id),
            "catalogs": self._catalog_count,
            "assets": lambda: _count(self.db, Asset, tenant_id),
            "parties": lambda: _count(self.db, Party, tenant_id),
            "supply": self._supply_count,
            "documents": lambda: _count(self.db, Document, tenant_id),
            "notices": lambda: _count(self.db, PrivacyNotice, tenant_id),
            "dataflows": lambda: _count(self.db, DataFlow, tenant_id),
            "retention": lambda: _count(self.db, RetentionRule, tenant_id),
            "tasks": lambda: _count(self.db, PrivacyTask, tenant_id),
            "dpia": lambda: _count(self.db, Dpia, tenant_id),
            "transfers": lambda: _count(self.db, InternationalTransfer, tenant_id),
            "risks": lambda: _count(self.db, Risk, tenant_id),
            "legal_references": lambda: _count(self.db, LegalReference, tenant_id),
            "approvals": lambda: _count(self.db, Approval, tenant_id),
            "projects": lambda: _count(self.db, Project, tenant_id),
            "training": lambda: _count(self.db, TrainingCourse, tenant_id),
            "authorizations": lambda: _count(self.db, Authorization, tenant_id),
            "breaches": lambda: _count(self.db, DataBreach, tenant_id),
            "requests": lambda: _count(self.db, DataSubjectRequest, tenant_id),
            "audits": lambda: _count(self.db, PrivacyAudit, tenant_id),
            "actions": lambda: _count(self.db, CorrectiveAction, tenant_id),
            "opinions": lambda: _count(self.db, DpoOpinion, tenant_id),
            "annual_reports": lambda: _count(self.db, AnnualReport, tenant_id),
            "tags": lambda: _count(self.db, Tag, tenant_id),
            "custom_fields": lambda: _count(self.db, CustomFieldDefinition, tenant_id),
            "comments": lambda: _count(self.db, Comment, tenant_id),
            "findings": lambda: _count(self.db, ComplianceFinding, tenant_id),
            "webhooks": lambda: _count(self.db, WebhookSubscription, tenant_id),
        }
        fn = counts.get(code)
        return await fn() if fn else 0

    async def _catalog_count(self) -> int:
        tenant_id = self._tenant
        subjects = (await self.db.execute(
            select(func.count()).select_from(DataSubjectCategoryCatalog).where(DataSubjectCategoryCatalog.tenant_id == tenant_id)
        )).scalar() or 0
        cats = (await self.db.execute(
            select(func.count()).select_from(DataCategoryCatalog).where(DataCategoryCatalog.tenant_id == tenant_id)
        )).scalar() or 0
        return subjects + cats

    async def _supply_count(self) -> int:
        return (await _count(self.db, Contract, self._tenant)) + (await _count(self.db, Subprocessor, self._tenant))

    async def _document_ids_for(self, tenant_id: str, section: DemoSection) -> list[str]:
        if section.code == "documents":
            return await _ids(self.db, Document, tenant_id)
        if not section.entity_types:
            return []
        stmt = select(DocumentLink.document_id).where(DocumentLink.entity_type.in_(section.entity_types))
        linked = list((await self.db.execute(stmt)).scalars().all())
        if not linked:
            return []
        found = (await self.db.execute(
            select(Document.id).where(Document.tenant_id == tenant_id, Document.id.in_(linked))
        )).scalars().all()
        return list(found)

    async def _document_count(self, tenant_id: str, section: DemoSection) -> int:
        return len(await self._document_ids_for(tenant_id, section))

    async def reset(self, tenant_id: str, actor: str, section_code: str, target: str) -> dict:
        self._tenant = tenant_id
        if section_code == "all":
            deleted = 0
            files = 0
            if target in ("documents", "both"):
                files = await self._reset_documents(tenant_id, list(SECTIONS))
            if target in ("data", "both"):
                for section in reversed(SECTIONS):
                    deleted += await self._reset_data(tenant_id, section.code)
            await self.audit.log(
                tenant_id, "demo_data", "reset", actor_user_id=actor,
                metadata={"section": "all", "target": target, "records": deleted, "files": files},
            )
            return {"section": "all", "target": target, "records": deleted, "files": files}

        section = SECTION_BY_CODE.get(section_code)
        if section is None:
            raise DemoDataError(f"Sezione sconosciuta: {section_code}")
        if target not in ("data", "documents", "both"):
            raise DemoDataError("target deve essere data, documents o both")
        deleted = 0
        files = 0
        if target in ("documents", "both"):
            if not section.has_documents:
                raise DemoDataError("Questa sezione non ha documenti associati")
            files = await self._reset_documents(tenant_id, [section])
        if target in ("data", "both"):
            deleted = await self._reset_data(tenant_id, section.code)
        await self.audit.log(
            tenant_id, "demo_data", "reset", actor_user_id=actor,
            metadata={"section": section_code, "target": target, "records": deleted, "files": files},
        )
        return {"section": section_code, "target": target, "records": deleted, "files": files}

    async def seed(self, tenant_id: str, actor: str) -> dict:
        from seed_demo_data import seed_for_tenant

        created = await seed_for_tenant(self.db, tenant_id, actor)
        await self.audit.log(
            tenant_id, "demo_data", "seed", actor_user_id=actor,
            metadata={"tenant_id": tenant_id},
        )
        return {"ok": True, "created": created}

    # ---------------------------------------------------------------- reset data

    async def _reset_data(self, tenant_id: str, code: str) -> int:
        self._tenant = tenant_id
        handlers = {
            "organization": self._reset_organization,
            "processing": self._reset_processing,
            "catalogs": self._reset_catalogs,
            "assets": self._reset_assets,
            "parties": self._reset_parties,
            "supply": self._reset_supply,
            "documents": self._reset_all_documents_data,
            "notices": self._reset_notices,
            "dataflows": self._reset_dataflows,
            "retention": lambda: _delete_tenant(self.db, RetentionRule, tenant_id),
            "tasks": lambda: _delete_tenant(self.db, PrivacyTask, tenant_id),
            "dpia": self._reset_dpia,
            "transfers": self._reset_transfers,
            "risks": self._reset_risks,
            "legal_references": self._reset_legal_refs,
            "approvals": lambda: _delete_tenant(self.db, Approval, tenant_id),
            "projects": self._reset_projects,
            "training": self._reset_training,
            "authorizations": self._reset_authorizations,
            "breaches": self._reset_breaches,
            "requests": lambda: _delete_tenant(self.db, DataSubjectRequest, tenant_id),
            "audits": self._reset_audits,
            "actions": lambda: _delete_tenant(self.db, CorrectiveAction, tenant_id),
            "opinions": lambda: _delete_tenant(self.db, DpoOpinion, tenant_id),
            "annual_reports": lambda: _delete_tenant(self.db, AnnualReport, tenant_id),
            "tags": self._reset_tags,
            "custom_fields": self._reset_custom_fields,
            "comments": lambda: _delete_tenant(self.db, Comment, tenant_id),
            "findings": lambda: _delete_tenant(self.db, ComplianceFinding, tenant_id),
            "webhooks": self._reset_webhooks,
        }
        fn = handlers.get(code)
        if fn is None:
            return 0
        n = await fn()
        await self.db.flush()
        return n

    async def _reset_organization(self) -> int:
        tenant_id = self._tenant
        await self.db.execute(
            update(OrganizationalUnit).where(OrganizationalUnit.tenant_id == tenant_id).values(parent_id=None)
        )
        n_units = await _delete_tenant(self.db, OrganizationalUnit, tenant_id)
        n_org = await _delete_tenant(self.db, Organization, tenant_id)
        return n_units + n_org

    async def _reset_processing(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, ProcessingActivity, tenant_id)
        n = 0
        n += await _delete_ids(self.db, ProcessingPurpose, ProcessingPurpose.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingLegalBasis, ProcessingLegalBasis.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingDataSubject, ProcessingDataSubject.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingDataCategory, ProcessingDataCategory.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingActivityVersion, ProcessingActivityVersion.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingAsset, ProcessingAsset.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingProcessor, ProcessingProcessor.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingRecipient, ProcessingRecipient.processing_activity_id, ids)
        n += await _delete_ids(self.db, ProcessingLegalReference, ProcessingLegalReference.processing_activity_id, ids)
        n += await _delete_tenant(self.db, ProcessingActivity, tenant_id)
        return n

    async def _reset_catalogs(self) -> int:
        tenant_id = self._tenant
        cat_ids = list((await self.db.execute(
            select(DataCategoryCatalog.id).where(DataCategoryCatalog.tenant_id == tenant_id)
        )).scalars().all())
        sub_ids = list((await self.db.execute(
            select(DataSubjectCategoryCatalog.id).where(DataSubjectCategoryCatalog.tenant_id == tenant_id)
        )).scalars().all())
        n = 0
        n += await _delete_ids(self.db, ProcessingDataCategory, ProcessingDataCategory.data_category_id, cat_ids)
        n += await _delete_ids(self.db, ProcessingDataSubject, ProcessingDataSubject.category_id, sub_ids)
        if cat_ids:
            await self.db.execute(
                update(DataCategoryCatalog).where(DataCategoryCatalog.id.in_(cat_ids)).values(parent_id=None)
            )
        n += (await self.db.execute(delete(DataCategoryCatalog).where(DataCategoryCatalog.tenant_id == tenant_id))).rowcount or 0
        n += (await self.db.execute(delete(DataSubjectCategoryCatalog).where(DataSubjectCategoryCatalog.tenant_id == tenant_id))).rowcount or 0
        return n

    async def _reset_assets(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, Asset, tenant_id)
        n = await _delete_ids(self.db, ProcessingAsset, ProcessingAsset.asset_id, ids)
        n += await _delete_tenant(self.db, Asset, tenant_id)
        return n

    async def _reset_parties(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, Party, tenant_id)
        n = 0
        n += await _delete_ids(self.db, ProcessorProfile, ProcessorProfile.party_id, ids)
        n += await _delete_ids(self.db, ProcessorAgreement, ProcessorAgreement.party_id, ids)
        n += await _delete_ids(self.db, ProcessingProcessor, ProcessingProcessor.party_id, ids)
        n += await _delete_ids(self.db, ProcessingRecipient, ProcessingRecipient.party_id, ids)
        n += await _delete_ids(self.db, Subprocessor, Subprocessor.processor_party_id, ids)
        n += await _delete_ids(self.db, Subprocessor, Subprocessor.subprocessor_party_id, ids)
        n += await _delete_ids(self.db, Contract, Contract.party_id, ids)
        n += await _delete_tenant(self.db, Party, tenant_id)
        return n

    async def _reset_supply(self) -> int:
        tenant_id = self._tenant
        contract_ids = await _ids(self.db, Contract, tenant_id)
        if contract_ids:
            await self.db.execute(
                update(ProcessingProcessor).where(ProcessingProcessor.contract_id.in_(contract_ids)).values(contract_id=None)
            )
            await self.db.execute(
                update(ProcessorAgreement).where(ProcessorAgreement.contract_id.in_(contract_ids)).values(contract_id=None)
            )
        n = await _delete_tenant(self.db, Subprocessor, tenant_id)
        n += await _delete_tenant(self.db, Contract, tenant_id)
        return n

    async def _reset_all_documents_data(self) -> int:
        return await self._reset_documents(self._tenant, [SECTION_BY_CODE["documents"]])

    async def _reset_notices(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, PrivacyNotice, tenant_id)
        await self.db.execute(
            update(PrivacyNotice).where(PrivacyNotice.tenant_id == tenant_id).values(current_version_id=None)
        )
        n = await _delete_ids(self.db, NoticeProcessingLink, NoticeProcessingLink.privacy_notice_id, ids)
        n += await _delete_ids(self.db, PrivacyNoticeVersion, PrivacyNoticeVersion.privacy_notice_id, ids)
        n += await _delete_tenant(self.db, PrivacyNotice, tenant_id)
        return n

    async def _reset_dataflows(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, DataFlow, tenant_id)
        n = await _delete_ids(self.db, DataFlowCategory, DataFlowCategory.data_flow_id, ids)
        n += await _delete_tenant(self.db, DataFlow, tenant_id)
        return n

    async def _reset_dpia(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, Dpia, tenant_id)
        n = await _delete_ids(self.db, DpiaScreeningAnswer, DpiaScreeningAnswer.dpia_id, ids)
        n += await _delete_tenant(self.db, Dpia, tenant_id)
        return n

    async def _reset_transfers(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, InternationalTransfer, tenant_id)
        n = await _delete_ids(self.db, TransferLink, TransferLink.transfer_id, ids)
        n += await _delete_tenant(self.db, InternationalTransfer, tenant_id)
        return n

    async def _reset_risks(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, RiskAssessment, tenant_id)
        n += await _delete_tenant(self.db, MeasureImplementation, tenant_id)
        n += await _delete_tenant(self.db, Risk, tenant_id)
        n += (await self.db.execute(
            delete(SecurityMeasure).where(SecurityMeasure.tenant_id == tenant_id)
        )).rowcount or 0
        return n

    async def _reset_legal_refs(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, LegalReference, tenant_id)
        n = await _delete_ids(self.db, ProcessingLegalReference, ProcessingLegalReference.legal_reference_id, ids)
        n += await _delete_tenant(self.db, LegalReference, tenant_id)
        return n

    async def _reset_projects(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, ProjectArtifact, tenant_id)
        n += await _delete_tenant(self.db, ProjectReview, tenant_id)
        n += await _delete_tenant(self.db, Project, tenant_id)
        return n

    async def _reset_training(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, TrainingCompletion, tenant_id)
        n += await _delete_tenant(self.db, TrainingAssignment, tenant_id)
        n += await _delete_tenant(self.db, TrainingSession, tenant_id)
        n += await _delete_tenant(self.db, TrainingCourse, tenant_id)
        return n

    async def _reset_authorizations(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, Authorization, tenant_id)
        n += await _delete_tenant(self.db, AuthorizationProfile, tenant_id)
        return n

    async def _reset_breaches(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, DataBreach, tenant_id)
        n = await _delete_ids(self.db, DataBreachLink, DataBreachLink.data_breach_id, ids)
        n += await _delete_tenant(self.db, DataBreach, tenant_id)
        return n

    async def _reset_audits(self) -> int:
        tenant_id = self._tenant
        ids = await _ids(self.db, PrivacyAudit, tenant_id)
        n = await _delete_ids(self.db, AuditFinding, AuditFinding.audit_id, ids)
        n += await _delete_tenant(self.db, PrivacyAudit, tenant_id)
        return n

    async def _reset_tags(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, EntityTag, tenant_id)
        n += await _delete_tenant(self.db, Tag, tenant_id)
        return n

    async def _reset_custom_fields(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, CustomFieldValue, tenant_id)
        n += await _delete_tenant(self.db, CustomFieldDefinition, tenant_id)
        return n

    async def _reset_webhooks(self) -> int:
        tenant_id = self._tenant
        n = await _delete_tenant(self.db, WebhookDelivery, tenant_id)
        n += await _delete_tenant(self.db, WebhookSubscription, tenant_id)
        return n

    async def _reset_documents(self, tenant_id: str, sections: list[DemoSection]) -> int:
        doc_ids: set[str] = set()
        for section in sections:
            doc_ids.update(await self._document_ids_for(tenant_id, section))
        ids = list(doc_ids)
        if not ids:
            return 0
        keys = list((await self.db.execute(
            select(DocumentVersion.storage_key).where(DocumentVersion.document_id.in_(ids))
        )).scalars().all())
        files = _unlink_files(keys)
        await self.db.execute(
            update(Document).where(Document.id.in_(ids)).values(current_version_id=None)
        )
        await self.db.execute(
            update(Contract).where(Contract.document_id.in_(ids)).values(document_id=None)
        )
        await self.db.execute(
            update(CorrectiveAction).where(CorrectiveAction.evidence_document_id.in_(ids)).values(evidence_document_id=None)
        )
        await self.db.execute(
            update(MeasureImplementation).where(MeasureImplementation.evidence_document_id.in_(ids)).values(evidence_document_id=None)
        )
        await self.db.execute(
            update(InternationalTransfer).where(InternationalTransfer.tia_document_id.in_(ids)).values(tia_document_id=None)
        )
        await _delete_ids(self.db, DocumentLink, DocumentLink.document_id, ids)
        await _delete_ids(self.db, DocumentVersion, DocumentVersion.document_id, ids)
        await _delete_ids(self.db, Document, Document.id, ids)
        await self.db.flush()
        return files + len(ids)
