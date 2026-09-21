from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_risk import (
    DEFAULT_MATRIX,
    ImplementationStatus,
    MeasureImplementation,
    Risk,
    RiskAssessment,
    RiskLevel,
    RiskMatrixConfig,
    SecurityMeasure,
)
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_entity, ensure_exists


def classify_score(likelihood: int, impact: int, matrix: dict) -> tuple[int, RiskLevel]:
    scale_min = int(matrix.get("scale_min") or 1)
    scale_max = int(matrix.get("scale_max") or 5)
    if not (scale_min <= likelihood <= scale_max and scale_min <= impact <= scale_max):
        raise ValueError(f"Likelihood e impatto devono essere tra {scale_min} e {scale_max}")
    score = likelihood * impact
    for band in matrix.get("bands") or DEFAULT_MATRIX["bands"]:
        if int(band["min_score"]) <= score <= int(band["max_score"]):
            return score, RiskLevel(band["level"])
    return score, RiskLevel.MEDIUM


class RiskService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def get_matrix(self, tenant_id: str) -> dict:
        row = await self.db.get(RiskMatrixConfig, tenant_id)
        if not row:
            return {"tenant_id": tenant_id, "config": DEFAULT_MATRIX, "updated_at": None, "updated_by": None}
        return row.to_dict()

    async def save_matrix(self, tenant_id: str, actor: str, config: dict) -> dict:
        bands = config.get("bands") or []
        if not bands:
            raise ValueError("La matrice deve avere almeno una fascia")
        row = await self.db.get(RiskMatrixConfig, tenant_id)
        if row is None:
            row = RiskMatrixConfig(tenant_id=tenant_id, config=config, updated_by=actor)
            self.db.add(row)
        else:
            row.config = config
            row.updated_by = actor
            row.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "risk_matrix", "update", entity_id=tenant_id, actor_user_id=actor, new_values=config)
        return row.to_dict()

    async def _matrix(self, tenant_id: str) -> dict:
        data = await self.get_matrix(tenant_id)
        return data.get("config") or DEFAULT_MATRIX

    async def list_risks(self, tenant_id: str) -> list[Risk]:
        result = await self.db.execute(
            select(Risk).where(Risk.tenant_id == tenant_id, Risk.deleted_at.is_(None)).order_by(Risk.code)
        )
        return list(result.scalars().all())

    async def create_risk(self, tenant_id: str, actor: str, title: str, **fields) -> Risk:
        risk = Risk(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, Risk, tenant_id, "RSK"),
            title=title.strip(),
            description=fields.get("description"),
            threat_category=fields.get("threat_category"),
            affected_rights=fields.get("affected_rights") or [],
        )
        self.db.add(risk)
        await self.db.flush()
        await self.audit.log(tenant_id, "risk", "create", entity_id=risk.id, actor_user_id=actor, new_values=risk.to_dict())
        await self.db.refresh(risk)
        return risk

    async def update_risk(self, tenant_id: str, risk_id: str, actor: str, **fields) -> Risk:
        result = await self.db.execute(
            select(Risk).where(Risk.tenant_id == tenant_id, Risk.id == risk_id, Risk.deleted_at.is_(None))
        )
        risk = result.scalar_one_or_none()
        if not risk:
            raise ValueError("Rischio non trovato")
        old = risk.to_dict()
        for k in ("title", "description", "threat_category", "affected_rights"):
            if k in fields and fields[k] is not None:
                setattr(risk, k, fields[k].strip() if isinstance(fields[k], str) else fields[k])
        await self.db.flush()
        await self.audit.log(tenant_id, "risk", "update", entity_id=risk.id, actor_user_id=actor, old_values=old, new_values=risk.to_dict())
        await self.db.refresh(risk)
        return risk

    async def list_assessments(
        self, tenant_id: str, entity_type: str | None = None, entity_id: str | None = None
    ) -> list[RiskAssessment]:
        stmt = select(RiskAssessment).where(RiskAssessment.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(RiskAssessment.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(RiskAssessment.entity_id == entity_id)
        result = await self.db.execute(stmt.order_by(RiskAssessment.code))
        return list(result.scalars().all())

    async def create_assessment(self, tenant_id: str, actor: str, **fields) -> RiskAssessment:
        await ensure_exists(self.db, tenant_id, "risk", fields.get("risk_id"))
        await ensure_entity(self.db, tenant_id, fields.get("entity_type"), fields.get("entity_id"))
        rationale = (fields.get("assessment_rationale") or "").strip()
        if not rationale:
            raise ValueError("La classificazione richiede una motivazione")
        matrix = await self._matrix(tenant_id)
        score, level = classify_score(int(fields["likelihood"]), int(fields["impact"]), matrix)
        residual_level = None
        residual_score = None
        if fields.get("residual_likelihood") is not None and fields.get("residual_impact") is not None:
            if not (fields.get("residual_rationale") or "").strip():
                raise ValueError("Il rischio residuo richiede una motivazione")
            residual_score, residual_level = classify_score(
                int(fields["residual_likelihood"]), int(fields["residual_impact"]), matrix
            )
        item = RiskAssessment(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, RiskAssessment, tenant_id, "RA"),
            entity_type=fields["entity_type"],
            entity_id=fields["entity_id"],
            risk_id=fields["risk_id"],
            likelihood=int(fields["likelihood"]),
            impact=int(fields["impact"]),
            inherent_level=level,
            inherent_score=score,
            assessment_rationale=rationale,
            residual_likelihood=fields.get("residual_likelihood"),
            residual_impact=fields.get("residual_impact"),
            residual_level=residual_level,
            residual_score=residual_score,
            residual_rationale=fields.get("residual_rationale"),
            assessed_by=actor,
        )
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "risk_assessment", "create", entity_id=item.id, actor_user_id=actor, new_values=item.to_dict()
        )
        await self.db.refresh(item)
        return item

    async def update_assessment(self, tenant_id: str, assessment_id: str, actor: str, **fields) -> RiskAssessment:
        result = await self.db.execute(
            select(RiskAssessment).where(RiskAssessment.tenant_id == tenant_id, RiskAssessment.id == assessment_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise ValueError("Valutazione non trovata")
        await ensure_exists(self.db, tenant_id, "risk", fields.get("risk_id"))
        rationale = (fields.get("assessment_rationale") or item.assessment_rationale or "").strip()
        if not rationale:
            raise ValueError("La classificazione richiede una motivazione")
        matrix = await self._matrix(tenant_id)
        likelihood = int(fields.get("likelihood") or item.likelihood)
        impact = int(fields.get("impact") or item.impact)
        score, level = classify_score(likelihood, impact, matrix)
        old = item.to_dict()
        item.likelihood = likelihood
        item.impact = impact
        item.inherent_level = level
        item.inherent_score = score
        item.assessment_rationale = rationale
        if "risk_id" in fields and fields["risk_id"]:
            item.risk_id = fields["risk_id"]
        rl = fields.get("residual_likelihood", item.residual_likelihood)
        ri = fields.get("residual_impact", item.residual_impact)
        if rl is not None and ri is not None:
            rr = (fields.get("residual_rationale") or item.residual_rationale or "").strip()
            if not rr:
                raise ValueError("Il rischio residuo richiede una motivazione")
            rscore, rlevel = classify_score(int(rl), int(ri), matrix)
            item.residual_likelihood = int(rl)
            item.residual_impact = int(ri)
            item.residual_level = rlevel
            item.residual_score = rscore
            item.residual_rationale = rr
        item.version_no = (item.version_no or 1) + 1
        item.assessed_by = actor
        item.assessed_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            tenant_id, "risk_assessment", "update", entity_id=item.id, actor_user_id=actor, old_values=old, new_values=item.to_dict()
        )
        await self.db.refresh(item)
        return item

    async def high_residual(self, tenant_id: str) -> list[RiskAssessment]:
        items = await self.list_assessments(tenant_id)
        return [
            a for a in items
            if (a.residual_level or a.inherent_level) in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]

    async def list_measures(self, tenant_id: str) -> list[SecurityMeasure]:
        result = await self.db.execute(
            select(SecurityMeasure).where(
                or_(SecurityMeasure.tenant_id.is_(None), SecurityMeasure.tenant_id == tenant_id)
            ).order_by(SecurityMeasure.code)
        )
        return list(result.scalars().all())

    async def create_measure(self, tenant_id: str, actor: str, **fields) -> SecurityMeasure:
        item = SecurityMeasure(
            tenant_id=tenant_id,
            code=(fields.get("code") or "").strip().upper() or await next_prefixed_code(self.db, SecurityMeasure, tenant_id, "SM"),
            name=(fields.get("name") or "").strip(),
            category=fields.get("category"),
            description=fields.get("description"),
        )
        if not item.name:
            raise ValueError("Nome misura obbligatorio")
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(tenant_id, "security_measure", "create", entity_id=item.id, actor_user_id=actor, new_values=item.to_dict())
        await self.db.refresh(item)
        return item

    async def list_implementations(
        self, tenant_id: str, entity_type: str | None = None, entity_id: str | None = None
    ) -> list[MeasureImplementation]:
        stmt = select(MeasureImplementation).where(MeasureImplementation.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(MeasureImplementation.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(MeasureImplementation.entity_id == entity_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_implementation(self, tenant_id: str, actor: str, **fields) -> MeasureImplementation:
        await ensure_exists(self.db, tenant_id, "measure", fields.get("measure_id"))
        await ensure_entity(self.db, tenant_id, fields.get("entity_type"), fields.get("entity_id"))
        await ensure_exists(self.db, tenant_id, "document", fields.get("evidence_document_id"))
        item = MeasureImplementation(
            tenant_id=tenant_id,
            measure_id=fields["measure_id"],
            entity_type=fields["entity_type"],
            entity_id=fields["entity_id"],
            implementation_status=fields.get("implementation_status") or ImplementationStatus.PLANNED,
            implementation_notes=fields.get("implementation_notes"),
            owner_user_id=fields.get("owner_user_id"),
            last_verified_at=fields.get("last_verified_at"),
            next_review_at=fields.get("next_review_at"),
            evidence_document_id=fields.get("evidence_document_id"),
        )
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "measure_implementation", "create", entity_id=item.id, actor_user_id=actor, new_values=item.to_dict()
        )
        await self.db.refresh(item)
        return item
