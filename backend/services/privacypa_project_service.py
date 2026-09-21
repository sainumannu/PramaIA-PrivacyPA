"""
PrivacyPA — Privacy by design: nuovi progetti (§30).

Workflow: PROPOSAL -> PRIVACY_REVIEW -> ICT_REVIEW -> DPO_REVIEW (se previsto) -> DPIA (se necessaria) -> APPROVAL ->
GO_LIVE -> POST_REVIEW -> CLOSED. Ogni revisione è un record (`ProjectReview`) e decide il passo successivo; chiedere
modifiche riporta il progetto in proposta e apre un nuovo giro. Il responsabile del progetto non decide sul proprio.
Dall'ingresso nella DPIA si possono generare gli elementi del progetto (trattamento, sistema, contratto, atto art. 28,
DPIA, informativa, task): la generazione è idempotente e non sovrascrive ciò che esiste già.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from backend.models.privacypa_assessments import DpiaStatus
from backend.models.privacypa_parties import AgreementStatus, ContractStatus, Party, PartyType
from backend.models.privacypa_processing import ConfidentialityLevel
from backend.models.privacypa_projects import (
    FLAGS,
    GENERATION_STATUSES,
    TERMINAL_STATUSES,
    Project,
    ProjectArtifact,
    ProjectReview,
    ProjectStatus,
    ReviewOutcome,
    ReviewStage,
)
from backend.models.privacypa_tasks import TaskPriority, TaskSourceType, TaskStatus
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound, _Base, add_months
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_notice_service import NoticeService
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_processing_service import ProcessingService
from backend.services.privacypa_supply_chain_service import ContractService
from backend.services.privacypa_task_service import TaskService

_FIELDS = {"title", "description", "proposing_unit_id", "project_owner_id", "supplier_id", "confidentiality_level", *FLAGS}
POST_REVIEW_MONTHS = 6
TASK_DUE_DAYS = 30

# Stato -> fase di revisione che vi si svolge.
STAGE_OF = {
    ProjectStatus.PRIVACY_REVIEW: ReviewStage.PRIVACY, ProjectStatus.ICT_REVIEW: ReviewStage.ICT,
    ProjectStatus.DPO_REVIEW: ReviewStage.DPO, ProjectStatus.APPROVAL: ReviewStage.APPROVAL,
    ProjectStatus.POST_REVIEW: ReviewStage.POST,
}
# Criteri di screening della DPIA prefissati dalle caratteristiche dichiarate nel progetto.
_SCREENING_OF = {
    "profiles_people": "SCORING", "automated_decisions": "AUTOMATED_DECISION", "systematic_monitoring": "SYSTEMATIC_MONITORING",
    "special_data": "SPECIAL_DATA", "criminal_data": "SPECIAL_DATA", "large_scale": "LARGE_SCALE", "minors": "VULNERABLE",
    "uses_ai": "INNOVATIVE_TECH",
}
TARGETS = ("processing", "asset", "contract", "agreement", "notice", "tasks")


class ProjectSegregation(ComplianceError):
    """Il responsabile del progetto non decide sulle revisioni del proprio progetto (403)."""


class ProjectService(_Base):
    def _q(self, tenant_id: str):
        return select(Project).where(Project.tenant_id == tenant_id, Project.deleted_at.is_(None))

    async def get(self, tenant_id: str, project_id: str) -> Project | None:
        return (await self.db.execute(self._q(tenant_id).where(Project.id == project_id))).scalar_one_or_none()

    async def search(self, tenant_id: str, *, status: ProjectStatus | None = None, unit_id: str | None = None) -> list[Project]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(Project.status == status)
        if unit_id:
            stmt = stmt.where(Project.proposing_unit_id == unit_id)
        return list((await self.db.execute(stmt.order_by(Project.code))).scalars().all())

    async def reviews(self, tenant_id: str, project_id: str) -> list[ProjectReview]:
        rows = await self.db.execute(select(ProjectReview).where(
            ProjectReview.tenant_id == tenant_id, ProjectReview.project_id == project_id).order_by(ProjectReview.decided_at, ProjectReview.id))
        return list(rows.scalars().all())

    async def artifacts(self, tenant_id: str, project_id: str) -> list[ProjectArtifact]:
        rows = await self.db.execute(select(ProjectArtifact).where(
            ProjectArtifact.tenant_id == tenant_id, ProjectArtifact.project_id == project_id).order_by(ProjectArtifact.created_at, ProjectArtifact.id))
        return list(rows.scalars().all())

    @staticmethod
    def _summary(p: Project) -> dict:
        return {"code": p.code, "status": p.status.value, "cycle": p.cycle}

    async def _check(self, tenant_id: str, data: dict) -> dict:
        data["title"] = (data.get("title") or "").strip()
        data["project_owner_id"] = (data.get("project_owner_id") or "").strip()
        if not data["title"]:
            raise ComplianceError("Il titolo è obbligatorio")
        if not data["project_owner_id"] or len(data["project_owner_id"]) > 100:
            raise ComplianceError("Il responsabile del progetto è obbligatorio")
        if not data.get("proposing_unit_id"):
            raise ComplianceError("La struttura proponente è obbligatoria")
        await self._check_unit(tenant_id, data["proposing_unit_id"])
        if data.get("supplier_id") and not await self._exists(Party, tenant_id, data["supplier_id"]):
            raise ComplianceError("Fornitore non trovato")
        data["supplier_id"] = data.get("supplier_id") or None
        data["description"] = (data.get("description") or "").strip() or None
        data["confidentiality_level"] = data.get("confidentiality_level") or ConfidentialityLevel.INTERNAL
        for flag in FLAGS:
            data[flag] = bool(data.get(flag))
        return data

    async def create(self, tenant_id: str, actor: str, **fields) -> Project:
        data = await self._check(tenant_id, {k: v for k, v in fields.items() if k in _FIELDS})
        project = Project(tenant_id=tenant_id, code=await next_prefixed_code(self.db, Project, tenant_id, "PR"), created_by=actor, **data)
        project.apply_assessment()
        self.db.add(project)
        await self.db.flush()
        await self.audit.log(tenant_id, "project", "create", entity_id=project.id, actor_user_id=actor, new_values=self._summary(project))
        return project

    async def update(self, tenant_id: str, project_id: str, actor: str, **fields) -> Project:
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status != ProjectStatus.PROPOSAL:
            raise ComplianceError("Il progetto si modifica solo in proposta: chiedere modifiche alla revisione lo riporta in proposta")
        merged = {k: getattr(project, k) for k in _FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _FIELDS})
        data = await self._check(tenant_id, merged)
        for key, value in data.items():
            setattr(project, key, value)
        project.apply_assessment()
        project.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "project", "update", entity_id=project.id, actor_user_id=actor, new_values=self._summary(project))
        return project

    async def soft_delete(self, tenant_id: str, project_id: str, actor: str) -> None:
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status != ProjectStatus.PROPOSAL or project.cycle > 1 or await self.reviews(tenant_id, project_id):
            raise ComplianceError("Si elimina solo una proposta mai sottoposta a revisione; per gli altri progetti usare l'annullamento")
        project.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "project", "delete", entity_id=project.id, actor_user_id=actor, old_values={"code": project.code})

    # ------------------------------------------------------------ workflow
    async def _set_status(self, project: Project, target: ProjectStatus, actor: str, **extra) -> None:
        project.status, project.status_changed_at, project.updated_at = target, datetime.utcnow(), datetime.utcnow()
        await self.db.flush()
        await self.audit.log(
            project.tenant_id, "project", "status_change", event_type=f"project.status.{target.value.lower()}", entity_id=project.id,
            actor_user_id=actor, new_values={"status": target.value, **extra},
        )

    async def submit(self, tenant_id: str, project_id: str, actor: str) -> Project:
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status != ProjectStatus.PROPOSAL:
            raise ComplianceError("Solo una proposta si invia in revisione")
        if not (project.description or "").strip():
            raise ComplianceError("Prima dell'invio descrivere il progetto")
        await self._set_status(project, ProjectStatus.PRIVACY_REVIEW, actor)
        return project

    async def _dpia_ready(self, tenant_id: str, project: Project) -> None:
        if not project.dpia_screening_required:
            return
        dpia = await DpiaService(self.db).get(tenant_id, project.dpia_id) if project.dpia_id else None
        if dpia is None:
            raise ComplianceError("Serve lo screening della DPIA: crearlo dal progetto")
        if dpia.status not in (DpiaStatus.APPROVED, DpiaStatus.NOT_REQUIRED):
            raise ComplianceError("La DPIA collegata deve essere approvata, oppure lo screening deve escluderla")

    @staticmethod
    def _next_after(project: Project, stage: ReviewStage) -> ProjectStatus:
        assessment = project.assess()
        if stage == ReviewStage.PRIVACY:
            return ProjectStatus.ICT_REVIEW
        if stage == ReviewStage.ICT:
            return ProjectStatus.DPO_REVIEW if assessment["dpo_review_required"] else ProjectStatus.APPROVAL
        if stage == ReviewStage.DPO:
            return ProjectStatus.DPIA if assessment["dpia_screening_required"] else ProjectStatus.APPROVAL
        if stage == ReviewStage.APPROVAL:
            return ProjectStatus.GO_LIVE
        return ProjectStatus.CLOSED

    async def review(
        self, tenant_id: str, project_id: str, actor: str, outcome: ReviewOutcome, notes: str | None, go_live_date: date | None = None,
    ) -> Project:
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        stage = STAGE_OF.get(project.status)
        if stage is None:
            raise ComplianceError("Il progetto non è in una fase di revisione")
        if actor == project.project_owner_id:
            raise ProjectSegregation("Il responsabile del progetto non può decidere sulle revisioni del proprio progetto")
        notes = (notes or "").strip() or None
        if outcome != ReviewOutcome.APPROVED and not notes:
            raise ComplianceError("Chiedere modifiche o respingere richiede una motivazione")
        if stage == ReviewStage.POST and outcome == ReviewOutcome.REJECTED:
            raise ComplianceError("La revisione post-implementazione si chiude o registra rilievi: un progetto in esercizio non si respinge")
        if go_live_date and stage != ReviewStage.APPROVAL:
            raise ComplianceError("La data di go-live si indica alla decisione di approvazione")
        if outcome == ReviewOutcome.APPROVED and stage == ReviewStage.APPROVAL:
            await self._dpia_ready(tenant_id, project)
            go_live_date = go_live_date or date.today()
            if go_live_date > date.today() + timedelta(days=730):
                raise ComplianceError("La data di go-live è troppo lontana")
        review = ProjectReview(
            tenant_id=tenant_id, project_id=project.id, cycle=project.cycle, stage=stage, outcome=outcome,
            reviewer_user_id=actor, notes=notes,
        )
        self.db.add(review)
        await self.audit.log(
            tenant_id, "project", "review", event_type="project.review", entity_id=project.id, actor_user_id=actor,
            new_values={"stage": stage.value, "outcome": outcome.value},
        )
        if outcome == ReviewOutcome.REJECTED:
            await self._set_status(project, ProjectStatus.REJECTED, actor)
        elif outcome == ReviewOutcome.CHANGES_REQUESTED:
            if stage != ReviewStage.POST:           # dopo l'esercizio i rilievi restano registrati, il progetto non torna indietro
                project.cycle += 1
                await self._set_status(project, ProjectStatus.PROPOSAL, actor, cycle=project.cycle)
        else:
            target = self._next_after(project, stage)
            if stage == ReviewStage.APPROVAL:
                project.go_live_date = go_live_date
                project.post_review_due_date = add_months(datetime.combine(go_live_date, datetime.min.time()), POST_REVIEW_MONTHS).date()
            if target == ProjectStatus.CLOSED:
                project.closed_at = datetime.utcnow()
            await self._set_status(
                project, target, actor,
                **({"go_live_date": project.go_live_date.isoformat(), "post_review_due_date": project.post_review_due_date.isoformat()}
                   if stage == ReviewStage.APPROVAL else {}),
            )
        return project

    async def advance(self, tenant_id: str, project_id: str, actor: str) -> Project:
        """Passi senza decisione di una revisione: dalla DPIA all'approvazione, dal go-live alla revisione successiva."""
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status == ProjectStatus.DPIA:
            await self._dpia_ready(tenant_id, project)
            await self._set_status(project, ProjectStatus.APPROVAL, actor)
        elif project.status == ProjectStatus.GO_LIVE:
            if project.go_live_date and project.go_live_date > date.today():
                raise ComplianceError("Il go-live è previsto per il " + project.go_live_date.isoformat())
            await self._set_status(project, ProjectStatus.POST_REVIEW, actor)
        else:
            raise ComplianceError("Da questo stato il progetto avanza con una revisione")
        return project

    async def cancel(self, tenant_id: str, project_id: str, actor: str, notes: str | None) -> Project:
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status in TERMINAL_STATUSES:
            raise ComplianceError("Il progetto è già chiuso")
        if not (notes or "").strip():
            raise ComplianceError("L'annullamento richiede una motivazione")
        self.db.add(ProjectReview(
            tenant_id=tenant_id, project_id=project.id, cycle=project.cycle, stage=STAGE_OF.get(project.status, ReviewStage.PRIVACY),
            outcome=ReviewOutcome.REJECTED, reviewer_user_id=actor, notes=f"Annullato: {notes.strip()}",
        ))
        await self._set_status(project, ProjectStatus.CANCELLED, actor)
        return project

    # ------------------------------------------------------------ generazione degli elementi
    async def _record(self, project: Project, entity_type: str, entity_id: str, actor: str) -> None:
        self.db.add(ProjectArtifact(tenant_id=project.tenant_id, project_id=project.id, entity_type=entity_type, entity_id=entity_id, created_by=actor))
        await self.db.flush()

    async def _ensure_processing(self, project: Project, actor: str, existing: dict) -> tuple[str, bool]:
        if "processing" in existing:
            return existing["processing"], False
        activity = await ProcessingService(self.db).create_activity(
            project.tenant_id, actor, project.title, description=project.description, organizational_unit_id=project.proposing_unit_id,
            owner_user_id=project.project_owner_id, profiling=project.profiles_people, automated_decision=project.automated_decisions,
        )
        await self._record(project, "processing", activity.id, actor)
        existing["processing"] = activity.id
        return activity.id, True

    async def link_dpia(self, tenant_id: str, project_id: str, actor: str) -> Project:
        """Crea lo screening della DPIA del progetto, collegato al trattamento (generato se manca) e prefissato dalle caratteristiche."""
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status != ProjectStatus.DPIA:
            raise ComplianceError("Lo screening della DPIA si crea quando il progetto è nella fase DPIA")
        if not project.dpia_screening_required:
            raise ComplianceError("Le caratteristiche dichiarate non richiedono lo screening della DPIA")
        if project.dpia_id:
            raise ComplianceError("Il progetto ha già una DPIA collegata")
        existing = {a.entity_type: a.entity_id for a in await self.artifacts(tenant_id, project_id)}
        processing_id, _ = await self._ensure_processing(project, actor, existing)
        dpias = DpiaService(self.db)
        dpia = await dpias.create(tenant_id, actor, f"DPIA — {project.title}", processing_activity_id=processing_id)
        answers = [{"criterion_code": code, "answer": True, "notes": f"Dichiarato nel progetto {project.code}"}
                   for code in sorted({_SCREENING_OF[f] for f in _SCREENING_OF if getattr(project, f)})]
        await dpias.save_screening(tenant_id, dpia.id, actor, answers)
        await self._record(project, "dpia", dpia.id, actor)
        project.dpia_id, project.updated_at = dpia.id, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "project", "dpia_link", event_type="project.dpia.linked", entity_id=project.id, actor_user_id=actor,
                             new_values={"entity_id": dpia.id})
        return project

    @staticmethod
    def applicable(supplier: Party | None) -> list[str]:
        targets = ["processing", "asset", "notice"]
        if supplier is not None:
            targets.append("contract")
            if supplier.party_type == PartyType.PROCESSOR:
                targets.append("agreement")
        return targets + ["tasks"]

    async def generate(self, tenant_id: str, project_id: str, actor: str, targets: list[str] | None = None) -> dict:
        """Crea, in bozza, gli elementi mancanti del progetto; ciò che esiste già non si tocca. La DPIA nasce con `link_dpia`."""
        project = await self.get(tenant_id, project_id)
        if project is None:
            raise ComplianceNotFound("Progetto non trovato")
        if project.status not in GENERATION_STATUSES:
            raise ComplianceError("Gli elementi si generano dalla fase DPIA in poi")
        unknown = set(targets or []) - set(TARGETS)
        if unknown:
            raise ComplianceError(f"Elementi sconosciuti: {', '.join(sorted(unknown))}")
        supplier = await self._supplier(tenant_id, project)
        wanted = [t for t in self.applicable(supplier) if not targets or t in targets]
        rows = await self.artifacts(tenant_id, project_id)
        existing = {a.entity_type: a.entity_id for a in rows if not a.entity_type.startswith("task:")}
        done_tasks = {a.entity_type for a in rows if a.entity_type.startswith("task:")}
        before = dict(existing)
        created: dict[str, str] = {}
        codes: dict[str, str] = {}

        pid = None
        if any(t in wanted for t in ("processing", "asset", "agreement", "contract", "notice")):
            pid, is_new = await self._ensure_processing(project, actor, existing)
            if is_new:
                created["processing"] = pid
        assets = AssetService(self.db)
        if "asset" in wanted and "asset" not in existing:
            asset = await assets.create(
                tenant_id, actor, project.title, owner_unit_id=project.proposing_unit_id, supplier_id=project.supplier_id,
                cloud_service=project.uses_cloud, description=project.description, go_live_date=project.go_live_date,
            )
            links = [{"asset_id": link.asset_id, "role": link.role.value} for link in await assets.list_links(pid)]
            await assets.set_links(pid, links + [{"asset_id": asset.id, "role": "PROCESSING"}])
            await self._record(project, "asset", asset.id, actor)
            created["asset"], existing["asset"], codes["asset"] = asset.id, asset.id, asset.code
        if "contract" in wanted and "contract" not in existing:
            contract = await ContractService(self.db).create(
                tenant_id, actor, project.supplier_id, f"Contratto — {project.title}",
                organizational_unit_id=project.proposing_unit_id, status=ContractStatus.DRAFT,
            )
            await self._record(project, "contract", contract.id, actor)
            created["contract"], existing["contract"], codes["contract"] = contract.id, contract.id, contract.code
        if "agreement" in wanted and "agreement" not in existing:
            agreement = await PartyService(self.db).create_agreement(
                tenant_id, actor, project.supplier_id, contract_id=existing.get("contract"), status=AgreementStatus.DRAFT)
            await self._record(project, "agreement", agreement.id, actor)
            created["agreement"], existing["agreement"] = agreement.id, agreement.id
        if pid and supplier is not None and supplier.party_type == PartyType.PROCESSOR and any(t in wanted for t in ("contract", "agreement")):
            parties = PartyService(self.db)
            current = await parties.list_processors(pid)
            if all(link.party_id != supplier.id for link in current):
                links = [{"party_id": l.party_id, "processor_agreement_id": l.processor_agreement_id, "contract_id": l.contract_id, "notes": l.notes}
                         for l in current]
                await parties.set_processors(pid, links + [{"party_id": supplier.id, "processor_agreement_id": existing.get("agreement"),
                                                            "contract_id": existing.get("contract")}])
        if "notice" in wanted and "notice" not in existing:
            notices = NoticeService(self.db)
            notice = await notices.create(tenant_id, actor, f"Informativa — {project.title}", project.proposing_unit_id, version_label="1.0", content="")
            await notices.set_links(tenant_id, notice.id, actor, [pid])
            await self._record(project, "notice", notice.id, actor)
            created["notice"], existing["notice"], codes["notice"] = notice.id, notice.id, notice.code

        if "tasks" in wanted:
            todo = [(k, t) for k, t in (
                ("processing", "Completare il trattamento del progetto"), ("asset", "Completare la scheda del sistema"),
                ("contract", "Completare il contratto con il fornitore"), ("agreement", "Completare e far firmare l'atto art. 28"),
                ("notice", "Redigere l'informativa")) if k in existing]
            todo.append(("measures", "Definire e registrare le misure di sicurezza del progetto"))
            if project.uses_cloud:
                todo.append(("transfers", "Verificare ubicazione dei dati e trasferimenti extra UE del progetto"))
            for key, title in todo:
                if f"task:{key}" in done_tasks:
                    continue
                task = await TaskService(self.db).create(
                    tenant_id, actor, f"{title} ({project.code})", description=f"Generato dal progetto {project.code}",
                    source_type=TaskSourceType.PROJECT, source_id=project.id, assigned_user_id=project.project_owner_id,
                    assigned_unit_id=project.proposing_unit_id, priority=TaskPriority.MEDIUM, status=TaskStatus.OPEN,
                    due_at=datetime.utcnow() + timedelta(days=TASK_DUE_DAYS),
                )
                await self._record(project, f"task:{key}", task.id, actor)
                created[f"task:{key}"] = task.id
        await self.audit.log(tenant_id, "project", "generate", event_type="project.artifacts.generated", entity_id=project.id,
                             actor_user_id=actor, new_values={"created": sorted(created)})
        return {"created": created, "codes": codes, "existing": before}

    async def _supplier(self, tenant_id: str, project: Project) -> Party | None:
        if not project.supplier_id:
            return None
        return (await self.db.execute(select(Party).where(Party.tenant_id == tenant_id, Party.id == project.supplier_id))).scalar_one_or_none()
