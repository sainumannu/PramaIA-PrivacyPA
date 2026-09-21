"""
PrivacyPA — Registro dei trattamenti: CRUD, transizioni di stato, codifica TR-000001.
"""
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_processing import (
    ALLOWED_STATUS_TRANSITIONS,
    ProcessingActivity,
    ProcessingActivityVersion,
    ProcessingDataCategory,
    ProcessingDataSubject,
    ProcessingLegalBasis,
    ProcessingPurpose,
    ProcessingStatus,
)
from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_refs import ensure_catalog_usable, ensure_exists
from backend.services.privacypa_deadlines import get_deadlines, renewed_review_date


_EDITABLE_FIELDS = {
    "title",
    "description",
    "organizational_unit_id",
    "owner_user_id",
    "privacy_referent_user_id",
    "purpose_summary",
    "source_of_data",
    "collection_method",
    "mandatory_provision",
    "automated_decision",
    "profiling",
    "start_date",
    "last_review_date",
    "next_review_date",
    "confidentiality_level",
}


# Impostabili solo alla creazione (provenienza dell'import), mai da una modifica utente.
_CREATE_ONLY_FIELDS = {"source", "source_reference", "legacy_notes"}


class MissingRequiredFields(ValueError):
    """Il trattamento non ha i dati minimi per l'invio in revisione (AC-REG-002)."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Impossibile inviare in revisione, dati mancanti: " + ", ".join(missing))


def missing_review_fields(activity: ProcessingActivity) -> list[str]:
    """Campi obbligatori assenti per passare in IN_REVIEW (AC-REG-002)."""
    missing = []
    if not (activity.title or "").strip():
        missing.append("titolo")
    if not activity.organizational_unit_id:
        missing.append("struttura")
    has_purpose = (
        (activity.description or "").strip()
        or (activity.purpose_summary or "").strip()
        or any((p.description or "").strip() for p in activity.purposes)
    )
    if not has_purpose:
        missing.append("descrizione/finalità")
    if not activity.legal_bases:
        missing.append("almeno una base giuridica")
    if not activity.data_subjects:
        missing.append("almeno una categoria di interessati")
    if not activity.data_categories:
        missing.append("almeno una categoria di dati")
    return missing


class ProcessingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def next_code(self, tenant_id: str) -> str:
        result = await self.db.execute(
            select(func.max(ProcessingActivity.code)).where(ProcessingActivity.tenant_id == tenant_id)
        )
        current = result.scalar_one_or_none()
        if not current or not current.startswith("TR-"):
            return "TR-000001"
        try:
            n = int(current.split("-", 1)[1])
        except ValueError:
            n = 0
        return f"TR-{n + 1:06d}"

    def _base_query(self, tenant_id: str):
        return select(ProcessingActivity).where(
            ProcessingActivity.tenant_id == tenant_id,
            ProcessingActivity.deleted_at.is_(None),
            ProcessingActivity.is_current.is_(True),
        )

    async def _check_references(self, tenant_id: str, fields: dict, data_subject_ids, data_category_ids) -> None:
        """Struttura, interessati e categorie devono essere dell'ente (o globali): un id di un altro ente vale come inesistente (§141)."""
        await ensure_exists(self.db, tenant_id, "unit", fields.get("organizational_unit_id"))
        await ensure_catalog_usable(self.db, tenant_id, DataSubjectCategoryCatalog, data_subject_ids)
        await ensure_catalog_usable(self.db, tenant_id, DataCategoryCatalog, data_category_ids)

    async def list_activities(
        self,
        tenant_id: str,
        *,
        status: ProcessingStatus | None = None,
        organizational_unit_id: str | None = None,
        search: str | None = None,
        accessible_unit_ids: set[str] | None = None,
    ) -> list[ProcessingActivity]:
        stmt = self._base_query(tenant_id)
        if status:
            stmt = stmt.where(ProcessingActivity.status == status)
        if organizational_unit_id:
            stmt = stmt.where(ProcessingActivity.organizational_unit_id == organizational_unit_id)
        if accessible_unit_ids is not None:
            if not accessible_unit_ids:
                return []
            stmt = stmt.where(ProcessingActivity.organizational_unit_id.in_(accessible_unit_ids))
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                ProcessingActivity.title.ilike(like) | ProcessingActivity.code.ilike(like)
            )
        stmt = stmt.order_by(ProcessingActivity.code.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_activity(self, tenant_id: str, activity_id: str) -> ProcessingActivity | None:
        result = await self.db.execute(
            self._base_query(tenant_id)
            .where(ProcessingActivity.id == activity_id)
            .options(
                selectinload(ProcessingActivity.purposes),
                selectinload(ProcessingActivity.legal_bases),
                selectinload(ProcessingActivity.data_subjects),
                selectinload(ProcessingActivity.data_categories),
            )
            # Le relazioni vengono sostituite con DELETE/INSERT: ricarica sempre dal DB
            # per non restituire finalità/basi/categorie ormai stantie nella stessa sessione.
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def create_activity(
        self,
        tenant_id: str,
        actor_user_id: str,
        title: str,
        **fields,
    ) -> ProcessingActivity:
        purposes = fields.pop("purposes", None)
        legal_basis_codes = fields.pop("legal_basis_codes", None)
        data_subject_ids = fields.pop("data_subject_ids", None)
        data_category_ids = fields.pop("data_category_ids", None)
        await self._check_references(tenant_id, fields, data_subject_ids, data_category_ids)

        activity = ProcessingActivity(
            tenant_id=tenant_id,
            code=await self.next_code(tenant_id),
            title=title.strip(),
            status=ProcessingStatus.DRAFT,
            created_by=actor_user_id,
            updated_by=actor_user_id,
            **{k: v for k, v in fields.items() if k in _EDITABLE_FIELDS | _CREATE_ONLY_FIELDS},
        )
        self.db.add(activity)
        await self.db.flush()
        await self._replace_relations(
            activity,
            purposes=purposes,
            legal_basis_codes=legal_basis_codes,
            data_subject_ids=data_subject_ids,
            data_category_ids=data_category_ids,
        )
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="processing_activity",
            entity_id=activity.id,
            action="create",
            actor_user_id=actor_user_id,
            new_values=activity.to_dict(),
        )
        return await self.get_activity(tenant_id, activity.id)

    async def update_activity(
        self,
        tenant_id: str,
        activity_id: str,
        actor_user_id: str,
        **fields,
    ) -> ProcessingActivity:
        activity = await self.get_activity(tenant_id, activity_id)
        if activity is None:
            raise ValueError("Trattamento non trovato")
        if activity.status in (ProcessingStatus.ARCHIVED,):
            raise ValueError("Un trattamento archiviato non è modificabile")

        purposes = fields.pop("purposes", None)
        legal_basis_codes = fields.pop("legal_basis_codes", None)
        data_subject_ids = fields.pop("data_subject_ids", None)
        data_category_ids = fields.pop("data_category_ids", None)
        await self._check_references(tenant_id, fields, data_subject_ids, data_category_ids)

        old_values = activity.to_dict(include_relations=True)
        await self._snapshot(activity, actor_user_id, reason="update", snapshot=old_values)
        for key, value in fields.items():
            if key in _EDITABLE_FIELDS:
                setattr(activity, key, value)
        activity.updated_by = actor_user_id
        activity.updated_at = datetime.utcnow()
        activity.version_no = (activity.version_no or 1) + 1

        await self._replace_relations(
            activity,
            purposes=purposes,
            legal_basis_codes=legal_basis_codes,
            data_subject_ids=data_subject_ids,
            data_category_ids=data_category_ids,
        )
        await self.db.flush()
        # Le relazioni sono state sostituite con query dirette: si rilegge lo stato reale prima di scrivere l'audit,
        # altrimenti new_values riporterebbe i collegamenti precedenti.
        activity = await self.get_activity(tenant_id, activity.id)
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="processing_activity",
            entity_id=activity.id,
            action="update",
            actor_user_id=actor_user_id,
            old_values=old_values,
            new_values=activity.to_dict(include_relations=True),
        )
        return activity

    async def transition_status(
        self,
        tenant_id: str,
        activity_id: str,
        actor_user_id: str,
        target: ProcessingStatus,
    ) -> ProcessingActivity:
        activity = await self.get_activity(tenant_id, activity_id)
        if activity is None:
            raise ValueError("Trattamento non trovato")
        allowed = ALLOWED_STATUS_TRANSITIONS.get(activity.status, set())
        if target not in allowed:
            raise ValueError(
                f"Transizione non ammessa: {activity.status.value} → {target.value}"
            )
        if target == ProcessingStatus.IN_REVIEW:
            missing = missing_review_fields(activity)
            if missing:
                raise MissingRequiredFields(missing)
        old_status = activity.status.value
        await self._snapshot(
            activity,
            actor_user_id,
            reason="approval" if target in (ProcessingStatus.APPROVED, ProcessingStatus.ACTIVE) else "status_change",
            snapshot=activity.to_dict(include_relations=True),
        )
        activity.status = target
        activity.updated_by = actor_user_id
        activity.updated_at = datetime.utcnow()
        if target == ProcessingStatus.ACTIVE and activity.start_date is None:
            activity.start_date = date.today()
        new_values = {"status": target.value}
        if target == ProcessingStatus.APPROVED:
            # §156: la prossima revisione si calcola dall'approvazione (mesi di calendario, per ente), senza spostare una data futura già indicata.
            months = (await get_deadlines(self.db, tenant_id))["processing_review_months"]
            renewed = renewed_review_date(activity.next_review_date, date.today(), months)
            if renewed != activity.next_review_date:
                activity.last_review_date, activity.next_review_date = date.today(), renewed
                new_values["next_review_date"] = renewed.isoformat()
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="processing_activity",
            entity_id=activity.id,
            action="status_change",
            event_type=f"processing_activity.status.{target.value.lower()}",
            actor_user_id=actor_user_id,
            old_values={"status": old_status},
            new_values=new_values,
        )
        return await self.get_activity(tenant_id, activity.id)

    async def soft_delete(
        self, tenant_id: str, activity_id: str, actor_user_id: str
    ) -> None:
        activity = await self.get_activity(tenant_id, activity_id)
        if activity is None:
            raise ValueError("Trattamento non trovato")
        if activity.status != ProcessingStatus.DRAFT:
            raise ValueError(
                "Cancellazione consentita solo in bozza. Per i trattamenti finalizzati usare l'archiviazione."
            )
        activity.deleted_at = datetime.utcnow()
        activity.deleted_by = actor_user_id
        activity.updated_by = actor_user_id
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            entity_type="processing_activity",
            entity_id=activity.id,
            action="delete",
            actor_user_id=actor_user_id,
            old_values=activity.to_dict(),
        )

    async def summary(
        self, tenant_id: str, accessible_unit_ids: set[str] | None = None
    ) -> dict:
        activities = await self.list_activities(
            tenant_id, accessible_unit_ids=accessible_unit_ids
        )
        today = date.today()
        by_status: dict[str, int] = {s.value: 0 for s in ProcessingStatus}
        review_due = 0
        for a in activities:
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
            if a.next_review_date and a.next_review_date <= today and a.status == ProcessingStatus.ACTIVE:
                review_due += 1
        return {
            "total": len(activities),
            "by_status": by_status,
            "active": by_status.get(ProcessingStatus.ACTIVE.value, 0),
            "review_due": review_due,
            "drafts": by_status.get(ProcessingStatus.DRAFT.value, 0),
            "in_review": by_status.get(ProcessingStatus.IN_REVIEW.value, 0),
        }

    async def list_versions(self, tenant_id: str, activity_id: str) -> list[ProcessingActivityVersion]:
        result = await self.db.execute(
            select(ProcessingActivityVersion)
            .where(
                ProcessingActivityVersion.tenant_id == tenant_id,
                ProcessingActivityVersion.processing_activity_id == activity_id,
            )
            .order_by(ProcessingActivityVersion.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_version(
        self, tenant_id: str, activity_id: str, version_id: str
    ) -> ProcessingActivityVersion | None:
        result = await self.db.execute(
            select(ProcessingActivityVersion).where(
                ProcessingActivityVersion.tenant_id == tenant_id,
                ProcessingActivityVersion.processing_activity_id == activity_id,
                ProcessingActivityVersion.id == version_id,
            )
        )
        return result.scalar_one_or_none()

    async def _snapshot(
        self,
        activity: ProcessingActivity,
        actor_user_id: str,
        reason: str,
        snapshot: dict,
    ) -> None:
        now = datetime.utcnow()
        open_rows = await self.db.execute(
            select(ProcessingActivityVersion).where(
                ProcessingActivityVersion.processing_activity_id == activity.id,
                ProcessingActivityVersion.valid_to.is_(None),
            )
        )
        for row in open_rows.scalars().all():
            row.valid_to = now
        self.db.add(
            ProcessingActivityVersion(
                tenant_id=activity.tenant_id,
                processing_activity_id=activity.id,
                version_no=activity.version_no or 1,
                reason=reason,
                snapshot=snapshot,
                created_by=actor_user_id,
                valid_from=now,
            )
        )
        await self.db.flush()

    async def _replace_relations(
        self,
        activity: ProcessingActivity,
        *,
        purposes: list[str] | None,
        legal_basis_codes: list[str] | None,
        data_subject_ids: list[str] | None,
        data_category_ids: list[str] | None,
    ) -> None:
        if purposes is not None:
            await self.db.execute(
                delete(ProcessingPurpose).where(ProcessingPurpose.processing_activity_id == activity.id)
            )
            for i, text in enumerate(purposes):
                desc = (text or "").strip()
                if not desc:
                    continue
                self.db.add(ProcessingPurpose(
                    processing_activity_id=activity.id, description=desc, order_no=i
                ))

        if legal_basis_codes is not None:
            await self.db.execute(
                delete(ProcessingLegalBasis).where(ProcessingLegalBasis.processing_activity_id == activity.id)
            )
            seen: set[str] = set()
            for code in legal_basis_codes:
                if not code or code in seen:
                    continue
                seen.add(code)
                self.db.add(ProcessingLegalBasis(
                    processing_activity_id=activity.id, legal_basis_code=code
                ))

        if data_subject_ids is not None:
            await self.db.execute(
                delete(ProcessingDataSubject).where(ProcessingDataSubject.processing_activity_id == activity.id)
            )
            seen_s: set[str] = set()
            for cid in data_subject_ids:
                if not cid or cid in seen_s:
                    continue
                seen_s.add(cid)
                self.db.add(ProcessingDataSubject(
                    processing_activity_id=activity.id, category_id=cid
                ))

        if data_category_ids is not None:
            await self.db.execute(
                delete(ProcessingDataCategory).where(ProcessingDataCategory.processing_activity_id == activity.id)
            )
            seen_c: set[str] = set()
            for cid in data_category_ids:
                if not cid or cid in seen_c:
                    continue
                seen_c.add(cid)
                self.db.add(ProcessingDataCategory(
                    processing_activity_id=activity.id, data_category_id=cid
                ))
        await self.db.flush()
