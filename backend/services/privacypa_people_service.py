"""
PrivacyPA — Servizi di formazione (§44) e autorizzati al trattamento (§45).

Il completamento di un corso crea un record storico (`TrainingCompletion`) e calcola la prossima scadenza dalla
validità del corso; le assegnazioni non si cancellano: si completano o si esonerano con motivazione. Le
autorizzazioni non si cancellano: si revocano con motivazione.
"""
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assets import Asset
from backend.models.privacypa_identity import PrivacyRoleCode, UserRoleAssignment
from backend.models.privacypa_people import (
    Authorization,
    AuthorizationProfile,
    AssignmentStatus,
    TrainingAssignment,
    TrainingCompletion,
    TrainingCourse,
    TrainingSession,
)
from backend.models.privacypa_processing import ProcessingActivity
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_compliance_service import ComplianceError, ComplianceNotFound, _Base, add_months

MAX_USERS_PER_ASSIGNMENT = 500


def _add_months(day: date, months: int) -> date:
    return add_months(datetime.combine(day, time.min), months).date()


def _user_id(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 100:
        raise ComplianceError("Identificativo utente non valido")
    return value


# ================================================================ formazione

_COURSE_FIELDS = {"title", "version", "description", "mandatory", "target_roles", "target_unit_ids", "validity_months"}


class TrainingService(_Base):
    def _courses(self, tenant_id: str):
        return select(TrainingCourse).where(TrainingCourse.tenant_id == tenant_id, TrainingCourse.deleted_at.is_(None))

    async def search_courses(self, tenant_id: str, active: bool | None = None) -> list[TrainingCourse]:
        stmt = self._courses(tenant_id)
        if active is not None:
            stmt = stmt.where(TrainingCourse.active == active)
        return list((await self.db.execute(stmt.order_by(TrainingCourse.code))).scalars().all())

    async def get_course(self, tenant_id: str, course_id: str) -> TrainingCourse | None:
        return (await self.db.execute(self._courses(tenant_id).where(TrainingCourse.id == course_id))).scalar_one_or_none()

    async def _clean_course(self, tenant_id: str, data: dict) -> dict:
        if not (data.get("title") or "").strip() or not (data.get("version") or "").strip():
            raise ComplianceError("Titolo e versione del corso sono obbligatori")
        months = data.get("validity_months")
        if months is not None and not 1 <= months <= 120:
            raise ComplianceError("La validità deve essere tra 1 e 120 mesi")
        roles = list(dict.fromkeys(data.get("target_roles") or []))
        valid_roles = {r.value for r in PrivacyRoleCode}
        if any(r not in valid_roles for r in roles):
            raise ComplianceError("Ruolo destinatario non valido")
        units = list(dict.fromkeys(data.get("target_unit_ids") or []))
        for unit_id in units:
            await self._check_unit(tenant_id, unit_id)
        data.update(title=data["title"].strip(), version=data["version"].strip(), target_roles=roles, target_unit_ids=units)
        return data

    async def create_course(self, tenant_id: str, actor: str, **fields) -> TrainingCourse:
        data = await self._clean_course(tenant_id, {k: v for k, v in fields.items() if k in _COURSE_FIELDS})
        course = TrainingCourse(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, TrainingCourse, tenant_id, "CO"), created_by=actor, **data
        )
        self.db.add(course)
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "create", entity_id=course.id, actor_user_id=actor,
                             new_values={"code": course.code, "mandatory": course.mandatory})
        return course

    async def update_course(self, tenant_id: str, course_id: str, actor: str, **fields) -> TrainingCourse:
        course = await self.get_course(tenant_id, course_id)
        if course is None:
            raise ComplianceNotFound("Corso non trovato")
        merged = {k: getattr(course, k) for k in _COURSE_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _COURSE_FIELDS})
        for key, value in (await self._clean_course(tenant_id, merged)).items():
            setattr(course, key, value)
        course.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "update", entity_id=course.id, actor_user_id=actor,
                             new_values={"code": course.code, "mandatory": course.mandatory})
        return course

    async def set_active(self, tenant_id: str, course_id: str, actor: str, active: bool) -> TrainingCourse:
        course = await self.get_course(tenant_id, course_id)
        if course is None:
            raise ComplianceNotFound("Corso non trovato")
        course.active, course.updated_at = active, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "activate" if active else "retire", entity_id=course.id, actor_user_id=actor,
                             new_values={"code": course.code})
        return course

    # ------------------------------------------------------------ sessioni
    async def add_session(self, tenant_id: str, course_id: str, actor: str, session_date: date, **fields) -> TrainingSession:
        if await self.get_course(tenant_id, course_id) is None:
            raise ComplianceNotFound("Corso non trovato")
        session = TrainingSession(
            tenant_id=tenant_id, course_id=course_id, session_date=session_date, created_by=actor,
            **{k: fields.get(k) for k in ("trainer", "location", "notes")},
        )
        self.db.add(session)
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "session_add", event_type="training_course.session.added",
                             entity_id=course_id, actor_user_id=actor, new_values={"session_date": session_date.isoformat()})
        return session

    async def list_sessions(self, tenant_id: str, course_id: str) -> list[TrainingSession]:
        rows = await self.db.execute(select(TrainingSession).where(
            TrainingSession.tenant_id == tenant_id, TrainingSession.course_id == course_id).order_by(TrainingSession.session_date))
        return list(rows.scalars().all())

    # ------------------------------------------------------------ assegnazioni
    async def _open_users(self, tenant_id: str, course_id: str) -> set[str]:
        rows = await self.db.execute(select(TrainingAssignment.user_id).where(
            TrainingAssignment.tenant_id == tenant_id, TrainingAssignment.course_id == course_id,
            TrainingAssignment.status == AssignmentStatus.ASSIGNED))
        return set(rows.scalars().all())

    async def _valid_users(self, tenant_id: str, course_id: str, today: date) -> set[str]:
        """Utenti con un completamento ancora valido (senza scadenza, o con scadenza futura)."""
        rows = await self.db.execute(select(TrainingCompletion.user_id, TrainingCompletion.next_due_at).where(
            TrainingCompletion.tenant_id == tenant_id, TrainingCompletion.course_id == course_id))
        return {u for u, due in rows.all() if due is None or due > today}

    async def assign(self, tenant_id: str, actor: str, course_id: str, user_ids: list[str], due_date: date) -> dict:
        course = await self.get_course(tenant_id, course_id)
        if course is None:
            raise ComplianceNotFound("Corso non trovato")
        if not course.active:
            raise ComplianceError("Il corso è ritirato e non si assegna")
        if due_date < date.today():
            raise ComplianceError("La scadenza non può essere nel passato")
        users = list(dict.fromkeys(_user_id(u) for u in user_ids))
        if not users or len(users) > MAX_USERS_PER_ASSIGNMENT:
            raise ComplianceError(f"Indicare da 1 a {MAX_USERS_PER_ASSIGNMENT} utenti")
        busy = await self._open_users(tenant_id, course_id)
        created = []
        for user in users:
            if user in busy:
                continue
            row = TrainingAssignment(tenant_id=tenant_id, course_id=course_id, user_id=user, due_date=due_date, assigned_by=actor)
            self.db.add(row)
            created.append(row)
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "assign", event_type="training_course.assigned", entity_id=course_id,
                             actor_user_id=actor, new_values={"assigned": len(created), "skipped": len(users) - len(created),
                                                              "due_date": due_date.isoformat()})
        return {"created": created, "skipped": [u for u in users if u in busy]}

    async def target_users(self, tenant_id: str, course: TrainingCourse) -> list[str]:
        """Utenti con un'assegnazione di ruolo attiva che corrisponde ai ruoli o alle strutture destinatarie."""
        roles, units = set(course.target_roles or []), set(course.target_unit_ids or [])
        if not roles and not units:
            return []
        now = datetime.utcnow()
        rows = (await self.db.execute(select(UserRoleAssignment).where(UserRoleAssignment.tenant_id == tenant_id))).scalars().all()
        return sorted({
            a.user_id for a in rows
            if a.valid_from <= now and (a.valid_to is None or a.valid_to >= now)
            and (a.role_code.value in roles or (a.organizational_unit_id and a.organizational_unit_id in units))
        })

    async def assign_targets(self, tenant_id: str, actor: str, course_id: str, due_date: date) -> dict:
        course = await self.get_course(tenant_id, course_id)
        if course is None:
            raise ComplianceNotFound("Corso non trovato")
        valid = await self._valid_users(tenant_id, course_id, date.today())
        users = [u for u in await self.target_users(tenant_id, course) if u not in valid]
        if not users:
            return {"created": [], "skipped": []}
        return await self.assign(tenant_id, actor, course_id, users, due_date)

    async def get_assignment(self, tenant_id: str, assignment_id: str) -> TrainingAssignment | None:
        row = await self.db.get(TrainingAssignment, assignment_id)
        return row if row and row.tenant_id == tenant_id else None

    async def search_assignments(
        self, tenant_id: str, *, course_id: str | None = None, user_id: str | None = None, status: AssignmentStatus | None = None,
    ) -> list[TrainingAssignment]:
        stmt = select(TrainingAssignment).where(TrainingAssignment.tenant_id == tenant_id)
        if course_id:
            stmt = stmt.where(TrainingAssignment.course_id == course_id)
        if user_id:
            stmt = stmt.where(TrainingAssignment.user_id == user_id)
        if status:
            stmt = stmt.where(TrainingAssignment.status == status)
        return list((await self.db.execute(stmt.order_by(TrainingAssignment.due_date, TrainingAssignment.id))).scalars().all())

    async def complete(
        self, tenant_id: str, assignment_id: str, actor: str, completed_at: date, *, score: int | None = None,
        certificate_document_id: str | None = None, session_id: str | None = None,
    ) -> TrainingCompletion:
        assignment = await self.get_assignment(tenant_id, assignment_id)
        if assignment is None:
            raise ComplianceNotFound("Assegnazione non trovata")
        if assignment.status != AssignmentStatus.ASSIGNED:
            raise ComplianceError("L'assegnazione è già chiusa")
        if completed_at > date.today():
            raise ComplianceError("La data di completamento non può essere nel futuro")
        if score is not None and not 0 <= score <= 100:
            raise ComplianceError("Il punteggio deve essere tra 0 e 100")
        await self._check_document(tenant_id, certificate_document_id)
        course = await self.get_course(tenant_id, assignment.course_id)
        if session_id:
            if session_id not in {s.id for s in await self.list_sessions(tenant_id, assignment.course_id)}:
                raise ComplianceError("Sessione non trovata per questo corso")
        completion = TrainingCompletion(
            tenant_id=tenant_id, assignment_id=assignment.id, course_id=assignment.course_id, user_id=assignment.user_id,
            session_id=session_id, completed_at=completed_at, score=score, certificate_document_id=certificate_document_id,
            next_due_at=_add_months(completed_at, course.validity_months) if course and course.validity_months else None,
            recorded_by=actor,
        )
        assignment.status = AssignmentStatus.COMPLETED
        self.db.add(completion)
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "complete", event_type="training_course.completed",
                             entity_id=assignment.course_id, actor_user_id=actor, new_values={"completed_at": completed_at.isoformat()})
        return completion

    async def waive(self, tenant_id: str, assignment_id: str, actor: str, reason: str) -> TrainingAssignment:
        assignment = await self.get_assignment(tenant_id, assignment_id)
        if assignment is None:
            raise ComplianceNotFound("Assegnazione non trovata")
        if assignment.status != AssignmentStatus.ASSIGNED:
            raise ComplianceError("L'assegnazione è già chiusa")
        if not (reason or "").strip():
            raise ComplianceError("L'esonero richiede una motivazione")
        assignment.status, assignment.waiver_reason = AssignmentStatus.WAIVED, reason.strip()
        await self.db.flush()
        await self.audit.log(tenant_id, "training_course", "waive", event_type="training_course.waived",
                             entity_id=assignment.course_id, actor_user_id=actor)
        return assignment

    async def completions(self, tenant_id: str, *, user_id: str | None = None, course_id: str | None = None) -> list[TrainingCompletion]:
        stmt = select(TrainingCompletion).where(TrainingCompletion.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(TrainingCompletion.user_id == user_id)
        if course_id:
            stmt = stmt.where(TrainingCompletion.course_id == course_id)
        return list((await self.db.execute(stmt.order_by(TrainingCompletion.completed_at.desc()))).scalars().all())


# ================================================================ autorizzati

_AUTH_FIELDS = {"user_id", "processing_activity_id", "asset_id", "authorization_profile_id", "valid_from", "valid_to"}


class AuthorizationService(_Base):
    # ------------------------------------------------------------ profili
    def _profiles(self, tenant_id: str):
        return select(AuthorizationProfile).where(AuthorizationProfile.tenant_id == tenant_id, AuthorizationProfile.deleted_at.is_(None))

    async def search_profiles(self, tenant_id: str) -> list[AuthorizationProfile]:
        return list((await self.db.execute(self._profiles(tenant_id).order_by(AuthorizationProfile.name))).scalars().all())

    async def get_profile(self, tenant_id: str, profile_id: str) -> AuthorizationProfile | None:
        return (await self.db.execute(self._profiles(tenant_id).where(AuthorizationProfile.id == profile_id))).scalar_one_or_none()

    async def create_profile(self, tenant_id: str, actor: str, name: str, description: str | None) -> AuthorizationProfile:
        name = (name or "").strip()
        if not name:
            raise ComplianceError("Il nome del profilo è obbligatorio")
        if (await self.db.execute(self._profiles(tenant_id).where(AuthorizationProfile.name == name))).first():
            raise ComplianceError("Esiste già un profilo con questo nome")
        profile = AuthorizationProfile(tenant_id=tenant_id, name=name, description=description)
        self.db.add(profile)
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization_profile", "create", entity_id=profile.id, actor_user_id=actor, new_values={"name": name})
        return profile

    async def update_profile(self, tenant_id: str, profile_id: str, actor: str, name: str, description: str | None) -> AuthorizationProfile:
        profile = await self.get_profile(tenant_id, profile_id)
        if profile is None:
            raise ComplianceNotFound("Profilo non trovato")
        name = (name or "").strip()
        if not name:
            raise ComplianceError("Il nome del profilo è obbligatorio")
        clash = (await self.db.execute(self._profiles(tenant_id).where(AuthorizationProfile.name == name, AuthorizationProfile.id != profile_id))).first()
        if clash:
            raise ComplianceError("Esiste già un profilo con questo nome")
        profile.name, profile.description, profile.updated_at = name, description, datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization_profile", "update", entity_id=profile.id, actor_user_id=actor, new_values={"name": name})
        return profile

    async def delete_profile(self, tenant_id: str, profile_id: str, actor: str) -> None:
        profile = await self.get_profile(tenant_id, profile_id)
        if profile is None:
            raise ComplianceNotFound("Profilo non trovato")
        used = (await self.db.execute(select(Authorization.id).where(Authorization.authorization_profile_id == profile_id))).first()
        if used:
            raise ComplianceError("Il profilo è usato da autorizzazioni: revocarle o cambiarne il profilo")
        profile.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization_profile", "delete", entity_id=profile.id, actor_user_id=actor)

    # ------------------------------------------------------------ autorizzazioni
    async def get(self, tenant_id: str, auth_id: str) -> Authorization | None:
        row = await self.db.get(Authorization, auth_id)
        return row if row and row.tenant_id == tenant_id else None

    async def search(
        self, tenant_id: str, *, user_id: str | None = None, processing_id: str | None = None, asset_id: str | None = None,
        active_only: bool = False,
    ) -> list[Authorization]:
        stmt = select(Authorization).where(Authorization.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(Authorization.user_id == user_id)
        if processing_id:
            stmt = stmt.where(Authorization.processing_activity_id == processing_id)
        if asset_id:
            stmt = stmt.where(Authorization.asset_id == asset_id)
        rows = list((await self.db.execute(stmt.order_by(Authorization.granted_at, Authorization.id))).scalars().all())
        return [a for a in rows if a.is_active(date.today())] if active_only else rows

    async def _validate(self, tenant_id: str, data: dict, exclude_id: str | None = None) -> dict:
        data["user_id"] = _user_id(data.get("user_id"))
        if not data.get("processing_activity_id") and not data.get("asset_id"):
            raise ComplianceError("L'autorizzazione riguarda un trattamento, un sistema o entrambi")
        if data.get("processing_activity_id") and not await self._exists(ProcessingActivity, tenant_id, data["processing_activity_id"]):
            raise ComplianceError("Trattamento non trovato")
        if data.get("asset_id") and not await self._exists(Asset, tenant_id, data["asset_id"]):
            raise ComplianceError("Sistema non trovato")
        if await self.get_profile(tenant_id, data["authorization_profile_id"]) is None:
            raise ComplianceError("Profilo di autorizzazione non trovato")
        if data.get("valid_to") and data["valid_to"] < data["valid_from"]:
            raise ComplianceError("La fine della validità precede l'inizio")
        for other in await self.search(tenant_id, user_id=data["user_id"]):
            same = (other.processing_activity_id, other.asset_id, other.authorization_profile_id) == (
                data.get("processing_activity_id"), data.get("asset_id"), data["authorization_profile_id"])
            overlaps = other.revoked_at is None and (other.valid_to is None or other.valid_to >= data["valid_from"]) and (
                data.get("valid_to") is None or other.valid_from <= data["valid_to"])
            if same and overlaps and other.id != exclude_id:
                raise ComplianceError("Esiste già un'autorizzazione equivalente nello stesso periodo")
        return data

    async def create(self, tenant_id: str, actor: str, **fields) -> Authorization:
        data = await self._validate(tenant_id, {k: fields.get(k) for k in _AUTH_FIELDS})
        auth = Authorization(tenant_id=tenant_id, granted_by=actor, **data)
        self.db.add(auth)
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization", "create", entity_id=auth.id, actor_user_id=actor,
                             new_values={"processing_activity_id": auth.processing_activity_id, "asset_id": auth.asset_id,
                                         "valid_to": auth.valid_to.isoformat() if auth.valid_to else None})
        return auth

    async def update(self, tenant_id: str, auth_id: str, actor: str, **fields) -> Authorization:
        auth = await self.get(tenant_id, auth_id)
        if auth is None:
            raise ComplianceNotFound("Autorizzazione non trovata")
        if auth.revoked_at is not None:
            raise ComplianceError("L'autorizzazione è revocata")
        merged = {k: getattr(auth, k) for k in _AUTH_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _AUTH_FIELDS})
        if merged["user_id"] != auth.user_id:
            raise ComplianceError("L'utente di un'autorizzazione non si cambia: revocarla e crearne un'altra")
        for key, value in (await self._validate(tenant_id, merged, exclude_id=auth.id)).items():
            setattr(auth, key, value)
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization", "update", entity_id=auth.id, actor_user_id=actor,
                             new_values={"valid_to": auth.valid_to.isoformat() if auth.valid_to else None})
        return auth

    async def revoke(self, tenant_id: str, auth_id: str, actor: str, reason: str) -> Authorization:
        auth = await self.get(tenant_id, auth_id)
        if auth is None:
            raise ComplianceNotFound("Autorizzazione non trovata")
        if auth.revoked_at is not None:
            raise ComplianceError("L'autorizzazione è già revocata")
        if not (reason or "").strip():
            raise ComplianceError("La revoca richiede una motivazione")
        auth.revoked_at, auth.revoked_by, auth.revoked_reason = datetime.utcnow(), actor, reason.strip()
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization", "revoke", entity_id=auth.id, actor_user_id=actor)
        return auth

    async def confirm_review(self, tenant_id: str, auth_id: str, actor: str) -> Authorization:
        """Conferma che l'autorizzazione resta corretta dopo un cambio di struttura del trattamento (§45)."""
        auth = await self.get(tenant_id, auth_id)
        if auth is None:
            raise ComplianceNotFound("Autorizzazione non trovata")
        if auth.revoked_at is not None:
            raise ComplianceError("L'autorizzazione è revocata")
        auth.last_reviewed_at, auth.last_reviewed_by = datetime.utcnow(), actor
        await self.db.flush()
        await self.audit.log(tenant_id, "authorization", "review_confirmed", entity_id=auth.id, actor_user_id=actor)
        return auth
