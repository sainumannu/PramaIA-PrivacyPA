"""
PrivacyPA — Richieste di accesso dall'HelpDesk: catalogo, valutazione deterministica e revisione dei casi non standard.

Contratto: «PrivacyPA – Specifiche per l'integrazione con HelpDesk (richieste di accesso)». La valutazione è
un'informazione per l'operatore: PrivacyPA non approva né nega. Ogni versione della richiesta (`source_ref` +
`request_version`) produce una sola valutazione; una versione nuova annulla la revisione aperta su quelle precedenti.
Nelle risposte, nell'audit e nell'outbox non compaiono dati della persona interessata.
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_access import REVIEW_REQUIRED, STANDARD, UNKNOWN, AccessAssessment
from backend.models.privacypa_approvals import Approval
from backend.models.privacypa_assets import Asset
from backend.models.privacypa_catalog import DataCategoryCatalog
from backend.models.privacypa_people import (
    AssignmentStatus,
    Authorization,
    AuthorizationProfile,
    TrainingAssignment,
    TrainingCompletion,
    TrainingCourse,
)
from backend.models.privacypa_processing import ProcessingActivity
from backend.services import privacypa_directory as directory
from backend.services.privacypa_approval_service import ACCESS, ApprovalConflict, ApprovalService, EntityInfo
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_integration_service import get_integrations

PASS, FAIL, WARNING, NOT_APPLICABLE, NOT_EXECUTABLE = "PASS", "FAIL", "WARNING", "NOT_APPLICABLE", "NOT_EXECUTABLE"


def _check(code: str, result: str, message: str | None = None) -> dict:
    return {"code": code, "result": result, **({"message": message} if message else {})}


def _instructions() -> dict:
    return _check("PRIVACY_INSTRUCTIONS_VALID", NOT_EXECUTABLE, "Presa visione delle istruzioni non ancora gestita in PrivacyPA")


@dataclass
class Request:
    request_type: str
    subject_user_id: str | None
    asset_id: str | None
    profile_id: str | None
    operations: list[str]
    scope: dict | None
    temporary: bool
    valid_from: date | None
    valid_to: date | None
    current_authorization_id: str | None
    source_module: str
    source_ref: str
    request_version: int
    source_url: str | None = None

    def stored(self) -> dict:
        return {
            "operations": self.operations, "scope": self.scope,
            "duration": {"type": "TEMPORARY" if self.temporary else "PERMANENT",
                         "valid_from": self.valid_from.isoformat() if self.valid_from else None,
                         "valid_to": self.valid_to.isoformat() if self.valid_to else None},
        }


async def assessment_title(db: AsyncSession, assessment: AccessAssessment) -> str:
    """Titolo leggibile nella coda approvazioni: profilo e sistema (mai la persona)."""
    profile = await db.get(AuthorizationProfile, assessment.profile_id) if assessment.profile_id else None
    asset = await db.get(Asset, assessment.asset_id) if assessment.asset_id else None
    parts = [assessment.request_type]
    if profile is not None and profile.tenant_id == assessment.tenant_id:
        parts.append(profile.code or profile.name)
    if asset is not None and asset.tenant_id == assessment.tenant_id:
        parts.append(asset.name)
    return " · ".join(parts)[:255]


class AccessGovernanceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)
        self.approvals = ApprovalService(db)

    # ------------------------------------------------------------ catalogo
    async def _asset(self, tenant_id: str, asset_id: str | None) -> Asset | None:
        if not asset_id:
            return None
        asset = await self.db.get(Asset, asset_id)
        return asset if asset is not None and asset.tenant_id == tenant_id and asset.deleted_at is None else None

    async def _profile(self, tenant_id: str, profile_id: str | None) -> AuthorizationProfile | None:
        if not profile_id:
            return None
        profile = await self.db.get(AuthorizationProfile, profile_id)
        return profile if profile is not None and profile.tenant_id == tenant_id and profile.deleted_at is None else None

    async def _active_profiles(self, tenant_id: str, asset_id: str | None = None) -> list[AuthorizationProfile]:
        stmt = select(AuthorizationProfile).where(
            AuthorizationProfile.tenant_id == tenant_id, AuthorizationProfile.deleted_at.is_(None),
            AuthorizationProfile.active.is_(True), AuthorizationProfile.asset_id.is_not(None))
        if asset_id:
            stmt = stmt.where(AuthorizationProfile.asset_id == asset_id)
        return list((await self.db.execute(stmt.order_by(AuthorizationProfile.name))).scalars().all())

    async def catalog_assets(self, tenant_id: str) -> list[dict]:
        """Sistemi con almeno un profilo attivo, con il responsabile della banca dati."""
        ids = {p.asset_id for p in await self._active_profiles(tenant_id)}
        if not ids:
            return []
        rows = (await self.db.execute(select(Asset).where(
            Asset.tenant_id == tenant_id, Asset.deleted_at.is_(None), Asset.id.in_(ids)).order_by(Asset.name))).scalars().all()
        return [{
            "id": a.id, "code": a.code, "name": a.name, "asset_type": a.asset_type.value if a.asset_type else None,
            "data_owner": {"name": a.data_owner_name, "role": a.data_owner_role, "user_id": a.data_owner_user_id},
        } for a in rows]

    async def _names(self, model, tenant_id: str, ids: list[str] | None, *, global_ok: bool = False) -> dict[str, object]:
        if not ids:
            return {}
        stmt = select(model).where(model.id.in_(ids))
        stmt = stmt.where((model.tenant_id == tenant_id) | model.tenant_id.is_(None)) if global_ok else stmt.where(model.tenant_id == tenant_id)
        if hasattr(model, "deleted_at"):
            stmt = stmt.where(model.deleted_at.is_(None))
        return {row.id: row for row in (await self.db.execute(stmt)).scalars().all()}

    async def _context(self, tenant_id: str, profile: AuthorizationProfile) -> dict:
        """Trattamenti, categorie di dati e corsi del profilo: solo codici e nomi."""
        activities = await self._names(ProcessingActivity, tenant_id, profile.processing_activity_ids)
        categories = await self._names(DataCategoryCatalog, tenant_id, profile.data_category_ids, global_ok=True)
        courses = await self._names(TrainingCourse, tenant_id, profile.required_training_course_ids)
        return {
            "processing_activities": [{"id": a.id, "code": a.code, "name": a.title}
                                      for i in profile.processing_activity_ids or [] if (a := activities.get(i))],
            "data_categories": [c.name for i in profile.data_category_ids or [] if (c := categories.get(i))],
            "required_courses": [{"id": c.id, "code": c.code, "title": c.title}
                                 for i in profile.required_training_course_ids or [] if (c := courses.get(i))],
        }

    async def catalog_profiles(self, tenant_id: str, asset_id: str | None) -> list[dict]:
        out = []
        for p in await self._active_profiles(tenant_id, asset_id):
            ctx = await self._context(tenant_id, p)
            out.append({
                "id": p.id, "code": p.code, "name": p.name, "description": p.description, "version": p.row_version,
                "asset_id": p.asset_id, "allowed_operations": p.allowed_operations or [],
                "allowed_scope_types": p.allowed_scope_types or [], "max_duration_days": p.max_duration_days, **ctx,
            })
        return out

    # ------------------------------------------------------------ valutazione
    async def find(self, tenant_id: str, source_module: str, source_ref: str, request_version: int) -> AccessAssessment | None:
        return (await self.db.execute(select(AccessAssessment).where(
            AccessAssessment.tenant_id == tenant_id, AccessAssessment.source_module == source_module,
            AccessAssessment.source_ref == source_ref, AccessAssessment.request_version == request_version,
        ))).scalar_one_or_none()

    async def get(self, tenant_id: str, assessment_id: str) -> AccessAssessment | None:
        row = await self.db.get(AccessAssessment, assessment_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def _training(self, tenant_id: str, user_id: str, profile: AuthorizationProfile, today: date, blocking: bool) -> dict:
        courses = await self._names(TrainingCourse, tenant_id, profile.required_training_course_ids)
        if not profile.required_training_course_ids:
            return _check("TRAINING_VALID", PASS)
        completions = (await self.db.execute(select(TrainingCompletion.course_id, TrainingCompletion.next_due_at).where(
            TrainingCompletion.tenant_id == tenant_id, TrainingCompletion.user_id == user_id,
            TrainingCompletion.course_id.in_(profile.required_training_course_ids)))).all()
        waived = set((await self.db.execute(select(TrainingAssignment.course_id).where(
            TrainingAssignment.tenant_id == tenant_id, TrainingAssignment.user_id == user_id,
            TrainingAssignment.status == AssignmentStatus.WAIVED))).scalars().all())
        problems = []
        for course_id in profile.required_training_course_ids:
            label = courses[course_id].code if course_id in courses else course_id
            dues = [due for cid, due in completions if cid == course_id]
            if course_id in waived or any(due is None or due > today for due in dues):
                continue
            problems.append(f"Corso {label} scaduto il {max(dues).isoformat()}" if dues else f"Corso {label} non completato")
        if not problems:
            return _check("TRAINING_VALID", PASS)
        return _check("TRAINING_VALID", FAIL if blocking else WARNING, "; ".join(problems))

    async def _profile_checks(
        self, tenant_id: str, req: Request, profile: AuthorizationProfile | None, today: date, *, grant: bool,
    ) -> list[dict]:
        """Controlli del profilo richiesto (GRANT e CHANGE che allarga i privilegi)."""
        if profile is None:
            missing = "Profilo non disponibile"
            checks = [_check("PROFILE_APPROVED", FAIL, "Profilo non trovato in PrivacyPA")]
            checks += [_check(code, NOT_EXECUTABLE, missing) for code in
                       ("OPERATION_ALLOWED", "SCOPE_ALLOWED", "TEMPORARY_ACCESS_POLICY", "TRAINING_VALID")]
            return checks + [_check("ALREADY_AUTHORIZED", NOT_EXECUTABLE if grant else NOT_APPLICABLE), _instructions()]

        if not profile.active:
            checks = [_check("PROFILE_APPROVED", FAIL, "Profilo non attivo")]
        elif profile.asset_id != req.asset_id:
            checks = [_check("PROFILE_APPROVED", FAIL, "Profilo non associato al sistema richiesto")]
        else:
            checks = [_check("PROFILE_APPROVED", PASS)]

        extra = [op for op in req.operations if op not in (profile.allowed_operations or [])]
        checks.append(_check("OPERATION_ALLOWED", FAIL, f"Operazioni non previste dal profilo: {', '.join(extra)}") if extra
                      else _check("OPERATION_ALLOWED", PASS))

        kind = (req.scope or {}).get("type")
        checks.append(_check("SCOPE_ALLOWED", PASS) if kind in (profile.allowed_scope_types or [])
                      else _check("SCOPE_ALLOWED", FAIL, f"Ambito {kind} non previsto dal profilo"))

        limit = profile.max_duration_days
        if limit is None:
            checks.append(_check("TEMPORARY_ACCESS_POLICY", PASS))
        elif not req.temporary:
            checks.append(_check("TEMPORARY_ACCESS_POLICY", FAIL, f"Il profilo ammette solo accessi temporanei (massimo {limit} giorni)"))
        else:
            days = (req.valid_to - req.valid_from).days + 1
            checks.append(_check("TEMPORARY_ACCESS_POLICY", FAIL, f"Durata di {days} giorni oltre il massimo di {limit}") if days > limit
                          else _check("TEMPORARY_ACCESS_POLICY", PASS))

        blocking = (await get_integrations(self.db, tenant_id))["access_training_blocking"]
        checks.append(await self._training(tenant_id, req.subject_user_id, profile, today, blocking))

        if grant:
            active = [a for a in (await self.db.execute(select(Authorization).where(
                Authorization.tenant_id == tenant_id, Authorization.user_id == req.subject_user_id,
                Authorization.authorization_profile_id == profile.id))).scalars().all() if a.is_current(today)]
            checks.append(_check("ALREADY_AUTHORIZED", WARNING, "Esiste già un'autorizzazione con lo stesso profilo, in vigore o con inizio futuro") if active
                          else _check("ALREADY_AUTHORIZED", PASS))
        else:
            checks.append(_check("ALREADY_AUTHORIZED", NOT_APPLICABLE))
        return checks + [_instructions()]

    @staticmethod
    def _reduces(req: Request, current: Authorization) -> bool:
        """La modifica non allarga nulla: stesso profilo, operazioni comprese, ambito non più ampio, scadenza non posticipata."""
        if req.profile_id != current.authorization_profile_id or not current.operations or current.scope is None:
            return False
        if not set(req.operations) <= set(current.operations):
            return False
        if req.scope != current.scope and current.scope.get("type") != "ALL_RECORDS":
            return False
        if current.valid_to is not None and (not req.temporary or req.valid_to > current.valid_to):
            return False
        return True

    async def _evaluate(self, tenant_id: str, req: Request, today: date) -> tuple[str, list[dict], AuthorizationProfile | None, Asset | None]:
        asset = await self._asset(tenant_id, req.asset_id)
        profile = await self._profile(tenant_id, req.profile_id)
        subject = await self._subject_check(tenant_id, req)
        unknown = []
        if asset is None:
            unknown.append(_check("SYSTEM_REGISTERED", FAIL, "Sistema non registrato in PrivacyPA"))
        if subject["result"] == FAIL:
            unknown.append(subject)
        if unknown:
            return UNKNOWN, unknown, profile, asset

        current = None
        if req.request_type in ("CHANGE", "REVOKE"):
            row = await self.db.get(Authorization, req.current_authorization_id) if req.current_authorization_id else None
            if row is not None and row.tenant_id == tenant_id and row.user_id == req.subject_user_id and row.revoked_at is None:
                current = row
            # Si modifica anche un accesso non ancora iniziato; uno scaduto va richiesto di nuovo (GRANT).
            if current is None or (req.request_type == "CHANGE" and not current.is_current(today)):
                return UNKNOWN, [_check("CURRENT_AUTHORIZATION", FAIL, "Autorizzazione attuale non trovata, revocata o scaduta")], profile, asset

        if req.request_type == "REVOKE":
            # Una revoca riduce i privilegi: non richiede revisione.
            return STANDARD, [subject, _check("CURRENT_AUTHORIZATION", PASS)], profile, asset
        if req.request_type == "CHANGE" and self._reduces(req, current):
            return STANDARD, [subject, _check("CHANGE_REDUCES_PRIVILEGES", PASS, "La modifica riduce operazioni, ambito o durata")], profile, asset

        checks = [subject] + await self._profile_checks(tenant_id, req, profile, today, grant=req.request_type == "GRANT")
        return (REVIEW_REQUIRED if any(c["result"] == FAIL for c in checks) else STANDARD), checks, profile, asset

    @staticmethod
    async def _subject_check(tenant_id: str, req: Request) -> dict:
        """La persona dell'anagrafica del Portal. Le revoche valgono anche per chi non è più attivo nell'ente."""
        if not req.subject_user_id:
            return _check("SUBJECT_RESOLVED", FAIL, "Persona non identificata: serve l'id utente del Portal")
        if req.request_type == "REVOKE":
            return _check("SUBJECT_RESOLVED", NOT_APPLICABLE)
        try:
            status = await directory.member_status(tenant_id, req.subject_user_id)
        except directory.DirectoryUnavailable:
            return _check("SUBJECT_RESOLVED", NOT_EXECUTABLE, "Anagrafica del Portal non raggiungibile: persona non verificata")
        if status == directory.NOT_FOUND:
            return _check("SUBJECT_RESOLVED", FAIL, "Persona non trovata tra gli utenti dell'ente nel Portal")
        if status == directory.INACTIVE:
            return _check("SUBJECT_RESOLVED", FAIL, "Persona disattivata nel Portal o non più membro dell'ente")
        return _check("SUBJECT_RESOLVED", PASS)

    async def assess(self, tenant_id: str, actor: str, on_behalf_of: str | None, req: Request) -> tuple[AccessAssessment, bool]:
        """(valutazione, creata). Stessa sorgente e versione: restituisce quella già fatta."""
        existing = await self.find(tenant_id, req.source_module, req.source_ref, req.request_version)
        if existing is not None:
            return existing, False
        older = (await self.db.execute(select(AccessAssessment.id).where(
            AccessAssessment.tenant_id == tenant_id, AccessAssessment.source_module == req.source_module,
            AccessAssessment.source_ref == req.source_ref, AccessAssessment.request_version < req.request_version,
        ))).scalars().all()
        outcome, checks, profile, asset = await self._evaluate(tenant_id, req, date.today())
        assessment = AccessAssessment(
            tenant_id=tenant_id, source_module=req.source_module, source_ref=req.source_ref, request_version=req.request_version,
            source_url=req.source_url, request_type=req.request_type, subject_user_id=req.subject_user_id, asset_id=req.asset_id, profile_id=req.profile_id,
            profile_version=profile.row_version if profile is not None else None,
            current_authorization_id=req.current_authorization_id, request=req.stored(), outcome=outcome, checks=checks,
            organizational_unit_id=asset.owner_unit_id if asset is not None else None,
            requested_by=actor, requested_on_behalf_of=on_behalf_of,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(assessment)
                await self.db.flush()
        except IntegrityError:                      # stessa richiesta arrivata in parallelo: vale la prima
            return await self.find(tenant_id, req.source_module, req.source_ref, req.request_version), False
        # Cambio sostanziale: le revisioni aperte sulle versioni precedenti non sono più applicabili.
        for old_id in older:
            await self.approvals.cancel_pending(tenant_id, ACCESS, old_id, actor)
        await self.audit.log(
            tenant_id, "access_assessment", "create", entity_id=assessment.id, actor_user_id=actor,
            new_values={"outcome": outcome, "source_ref": req.source_ref, "request_version": req.request_version,
                        "request_type": req.request_type, "profile_version": assessment.profile_version},
        )
        return assessment, True

    async def latest_approval(self, tenant_id: str, assessment: AccessAssessment) -> Approval | None:
        rows = await self.approvals.search(tenant_id, entity_type=ACCESS, entity_id=assessment.id, limit=1)
        return rows[0] if rows else None

    async def describe(self, tenant_id: str, assessment: AccessAssessment) -> dict:
        profile = await self._profile(tenant_id, assessment.profile_id)
        asset = await self._asset(tenant_id, assessment.asset_id)
        ctx = await self._context(tenant_id, profile) if profile is not None else {"processing_activities": [], "data_categories": []}
        approval = await self.latest_approval(tenant_id, assessment)
        return {
            "id": assessment.id, "outcome": assessment.outcome, "request_type": assessment.request_type,
            "source": {"module": assessment.source_module, "ref": assessment.source_ref, "request_version": assessment.request_version,
                       "url": assessment.source_url},
            "profile": ({"id": profile.id, "code": profile.code, "name": profile.name, "version": assessment.profile_version}
                        if profile is not None else None),
            "asset": {"id": asset.id, "code": asset.code, "name": asset.name} if asset is not None else None,
            "processing_activities": ctx["processing_activities"], "data_categories": ctx["data_categories"],
            "request": assessment.request or {}, "checks": assessment.checks or [],
            "approval": ({
                "id": approval.id, "state": approval.state.value,
                "decision": approval.decision.value if approval.decision else None, "entity_version": approval.entity_version,
                "notes": approval.notes, "approver_id": approval.approver_id,
                "requested_at": approval.requested_at.isoformat() + "Z" if approval.requested_at else None,
                "decision_at": approval.decision_at.isoformat() + "Z" if approval.decision_at else None,
            } if approval is not None else None),
            "links": {"profile": f"/profili-autorizzazione?id={profile.id}"} if profile is not None else {},
            "created_at": assessment.created_at.isoformat() + "Z" if assessment.created_at else None,
        }

    # ------------------------------------------------------------ revisione del privacy manager
    async def request_review(
        self, tenant_id: str, assessment: AccessAssessment, actor: str, on_behalf_of: str | None, notes: str | None,
    ) -> Approval:
        if assessment.outcome == STANDARD:
            raise ApprovalConflict("La valutazione è standard: non serve la revisione del privacy manager")
        newer = (await self.db.execute(select(AccessAssessment.id).where(
            AccessAssessment.tenant_id == tenant_id, AccessAssessment.source_module == assessment.source_module,
            AccessAssessment.source_ref == assessment.source_ref, AccessAssessment.request_version > assessment.request_version,
        ).limit(1))).first()
        if newer is not None:
            raise ApprovalConflict("Esiste una versione più recente della richiesta: va inviata quella")
        last = await self.latest_approval(tenant_id, assessment)
        if last is not None and last.decision is None and last.cancelled_at is None:
            return last                             # invio ripetuto: la revisione è già in coda
        if last is not None and last.decision is not None:
            raise ApprovalConflict("La richiesta è già stata decisa dal privacy manager")
        info = EntityInfo(assessment, ACCESS, assessment.organizational_unit_id, assessment.source_ref[:30],
                          await assessment_title(self.db, assessment), entity_version=assessment.request_version)
        approval = await self.approvals.request_review(tenant_id, info, actor, notes=(notes or "").strip() or None)
        approval.requested_on_behalf_of = on_behalf_of
        await self.db.flush()
        return approval

    async def review_details(self, tenant_id: str, assessment: AccessAssessment) -> dict:
        """Scheda per la coda approvazioni: richiesta e controlli non superati (non la descrizione del ticket)."""
        described = await self.describe(tenant_id, assessment)
        return {
            "request_type": assessment.request_type, "source_ref": assessment.source_ref,
            "source_url": assessment.source_url, "request_version": assessment.request_version, "subject_user_id": assessment.subject_user_id,
            "requested_on_behalf_of": assessment.requested_on_behalf_of, "outcome": assessment.outcome,
            "profile": described["profile"], "asset": described["asset"], "request": described["request"],
            "failed_checks": [c for c in described["checks"] if c["result"] in (FAIL, WARNING)],
        }
