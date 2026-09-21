from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assessments import (
    DPIA_TRANSITIONS,
    SCREENING_CRITERIA,
    Dpia,
    DpiaScreeningAnswer,
    DpiaStatus,
    ScreeningOutcome,
)
from backend.models.privacypa_processing import ProcessingActivity
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_deadlines import get_deadlines, renewed_review_date
from backend.services.privacypa_refs import ensure_exists

_EDITABLE = {
    "title", "processing_activity_id", "screening_outcome", "screening_rationale",
    "description", "necessity_proportionality", "risks_text", "measures_text",
    "residual_risk_text", "dpo_opinion", "controller_decision", "action_plan",
    "next_review_date", "confidentiality_level",
}


def suggest_from_answers(answers: list[DpiaScreeningAnswer], processing: ProcessingActivity | None) -> str:
    """Suggerimento del motore: non sostituisce la decisione umana (§24.2)."""
    yes = sum(1 for a in answers if a.answer is True)
    strong = {a.criterion_code for a in answers if a.answer is True} & {
        "AUTOMATED_DECISION", "SYSTEMATIC_MONITORING", "SPECIAL_DATA", "LARGE_SCALE", "VULNERABLE",
    }
    if processing:
        if processing.profiling or processing.automated_decision:
            strong.add("AUTOMATED_DECISION")
            yes = max(yes, 1)
    if strong or yes >= 2:
        return ScreeningOutcome.PROCEED.value
    if yes == 1:
        return ScreeningOutcome.UNCERTAIN.value
    return ScreeningOutcome.NOT_REQUIRED.value


class DpiaService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(Dpia).where(Dpia.tenant_id == tenant_id, Dpia.deleted_at.is_(None))

    async def list_dpias(self, tenant_id: str, status: DpiaStatus | None = None) -> list[Dpia]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(Dpia.status == status)
        result = await self.db.execute(stmt.order_by(Dpia.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, dpia_id: str) -> Dpia | None:
        result = await self.db.execute(self._q(tenant_id).where(Dpia.id == dpia_id))
        return result.scalar_one_or_none()

    async def list_answers(self, dpia_id: str) -> list[DpiaScreeningAnswer]:
        result = await self.db.execute(
            select(DpiaScreeningAnswer).where(DpiaScreeningAnswer.dpia_id == dpia_id)
        )
        by_code = {a.criterion_code: a for a in result.scalars().all()}
        out: list[DpiaScreeningAnswer] = []
        for code, _label in SCREENING_CRITERIA:
            if code in by_code:
                out.append(by_code[code])
            else:
                out.append(DpiaScreeningAnswer(dpia_id=dpia_id, criterion_code=code, answer=None))
        return out

    async def _processing(self, tenant_id: str, processing_id: str | None) -> ProcessingActivity | None:
        if not processing_id:
            return None
        result = await self.db.execute(
            select(ProcessingActivity).where(
                ProcessingActivity.tenant_id == tenant_id,
                ProcessingActivity.id == processing_id,
                ProcessingActivity.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, tenant_id: str, actor: str, title: str, **fields) -> Dpia:
        await ensure_exists(self.db, tenant_id, "processing", fields.get("processing_activity_id"))
        dpia = Dpia(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, Dpia, tenant_id, "DPIA"),
            title=title.strip(),
            created_by=actor,
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(dpia)
        await self.db.flush()
        for code, _label in SCREENING_CRITERIA:
            self.db.add(DpiaScreeningAnswer(dpia_id=dpia.id, criterion_code=code, answer=None))
        await self.db.flush()
        proc = await self._processing(tenant_id, dpia.processing_activity_id)
        answers = await self.list_answers(dpia.id)
        dpia.engine_suggestion = suggest_from_answers(answers, proc)
        await self.audit.log(
            tenant_id, "dpia", "create", entity_id=dpia.id, actor_user_id=actor, new_values=dpia.to_dict()
        )
        await self.db.refresh(dpia)
        return dpia

    async def update(self, tenant_id: str, dpia_id: str, actor: str, **fields) -> Dpia:
        dpia = await self.get(tenant_id, dpia_id)
        if not dpia:
            raise ValueError("DPIA non trovata")
        if dpia.status == DpiaStatus.ARCHIVED:
            raise ValueError("DPIA archiviata non modificabile")
        await ensure_exists(self.db, tenant_id, "processing", fields.get("processing_activity_id"))
        old = dpia.to_dict()
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(dpia, k, v)
        dpia.version_no = (dpia.version_no or 1) + 1
        dpia.updated_at = datetime.utcnow()
        answers = await self.list_answers(dpia.id)
        proc = await self._processing(tenant_id, dpia.processing_activity_id)
        dpia.engine_suggestion = suggest_from_answers(answers, proc)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "dpia", "update", entity_id=dpia.id, actor_user_id=actor, old_values=old, new_values=dpia.to_dict()
        )
        await self.db.refresh(dpia)
        return dpia

    async def save_screening(
        self, tenant_id: str, dpia_id: str, actor: str, answers: list[dict]
    ) -> Dpia:
        dpia = await self.get(tenant_id, dpia_id)
        if not dpia:
            raise ValueError("DPIA non trovata")
        existing = {
            a.criterion_code: a
            for a in (
                await self.db.execute(
                    select(DpiaScreeningAnswer).where(DpiaScreeningAnswer.dpia_id == dpia_id)
                )
            ).scalars().all()
        }
        for row in answers:
            code = row.get("criterion_code")
            if not code:
                continue
            if code in existing:
                existing[code].answer = row.get("answer")
                existing[code].notes = row.get("notes")
            else:
                self.db.add(DpiaScreeningAnswer(
                    dpia_id=dpia_id, criterion_code=code, answer=row.get("answer"), notes=row.get("notes")
                ))
        await self.db.flush()
        loaded = await self.list_answers(dpia_id)
        proc = await self._processing(tenant_id, dpia.processing_activity_id)
        dpia.engine_suggestion = suggest_from_answers(loaded, proc)
        dpia.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "dpia", "screening", entity_id=dpia.id, actor_user_id=actor,
            new_values={"engine_suggestion": dpia.engine_suggestion},
        )
        await self.db.refresh(dpia)
        return dpia

    async def transition(self, tenant_id: str, dpia_id: str, actor: str, target: DpiaStatus) -> Dpia:
        dpia = await self.get(tenant_id, dpia_id)
        if not dpia:
            raise ValueError("DPIA non trovata")
        allowed = DPIA_TRANSITIONS.get(dpia.status, set())
        if target not in allowed:
            raise ValueError(f"Transizione non ammessa: {dpia.status.value} → {target.value}")
        if target == DpiaStatus.NOT_REQUIRED and not (dpia.screening_rationale or "").strip():
            raise ValueError("Per escludere la DPIA serve una motivazione di screening")
        if target == DpiaStatus.APPROVED and not (dpia.controller_decision or "").strip():
            raise ValueError("Per approvare serve la decisione del titolare")
        old = dpia.status.value
        dpia.status = target
        new_values = {"status": target.value}
        if target == DpiaStatus.APPROVED:
            dpia.approved_at = datetime.utcnow()
            dpia.approved_by = actor
            # §156: prossima revisione = approvazione + N mesi di calendario (per ente); una data futura già indicata non si sposta.
            months = (await get_deadlines(self.db, tenant_id))["dpia_review_months"]
            renewed = renewed_review_date(dpia.next_review_date, dpia.approved_at.date(), months)
            if renewed != dpia.next_review_date:
                dpia.next_review_date = renewed
                new_values["next_review_date"] = renewed.isoformat()
        dpia.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "dpia", "status_change", entity_id=dpia.id, actor_user_id=actor,
            old_values={"status": old}, new_values=new_values,
        )
        await self.db.refresh(dpia)
        return dpia

    async def review_due(self, tenant_id: str) -> list[Dpia]:
        from datetime import date
        today = date.today()
        items = await self.list_dpias(tenant_id)
        return [
            d for d in items
            if d.status in (DpiaStatus.TO_REVIEW, DpiaStatus.APPROVED)
            and d.next_review_date
            and d.next_review_date <= today
        ]
