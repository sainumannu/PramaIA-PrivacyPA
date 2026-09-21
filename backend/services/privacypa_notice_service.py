"""
PrivacyPA — Informative (§25): versioni, approvazione, collegamento ai trattamenti e confronto (§32).

Una versione approvata non si modifica: per cambiare il contenuto si crea una nuova versione, che resta in
lavorazione finché non viene approvata; nel frattempo la precedente resta quella in vigore. Ogni modifica del
contenuto o dei collegamenti aggiorna anche l'informativa (versione riga), così un'approvazione già richiesta
non vale più per un contenuto cambiato (§54, §165).
"""
import difflib
import re
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.privacypa_documents import Document
from backend.models.privacypa_notices import (
    NOTICE_TRANSITIONS,
    NoticeProcessingLink,
    NoticeStatus,
    PrivacyNotice,
    PrivacyNoticeVersion,
)
from backend.models.privacypa_organization import OrganizationalUnit
from backend.models.privacypa_processing import ProcessingActivity
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code

_VERSION_FIELDS = {"version_label", "content", "effective_from", "published_url", "document_id"}
_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
MAX_CONTENT = 200_000


class NoticeError(ValueError):
    """Richiesta non valida o non coerente con lo stato dell'informativa (400)."""


class NoticeIncomplete(NoticeError):
    """Mancano dati per inviare l'informativa in approvazione (422)."""


def word_diff(old: str, new: str) -> list[dict]:
    """Differenze parola per parola: lista di {op: equal|delete|insert, text}."""
    tokens = lambda s: re.findall(r"\s+|\S+", s or "")  # noqa: E731
    a, b = tokens(old), tokens(new)
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": "".join(a[i1:i2])})
            continue
        if i2 > i1:
            ops.append({"op": "delete", "text": "".join(a[i1:i2])})
        if j2 > j1:
            ops.append({"op": "insert", "text": "".join(b[j1:j2])})
    return ops


class NoticeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return (
            select(PrivacyNotice)
            .where(PrivacyNotice.tenant_id == tenant_id, PrivacyNotice.deleted_at.is_(None))
            .options(selectinload(PrivacyNotice.versions))
        )

    async def search(
        self, tenant_id: str, *, status: NoticeStatus | None = None, processing_id: str | None = None,
        search: str | None = None,
    ) -> list[PrivacyNotice]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(PrivacyNotice.status == status)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(PrivacyNotice.title.ilike(like) | PrivacyNotice.code.ilike(like))
        rows = list((await self.db.execute(stmt.order_by(PrivacyNotice.code))).scalars().all())
        if processing_id:
            rows = [n for n in rows if any(l.processing_activity_id == processing_id for l in n.links)]
        return rows

    async def get(self, tenant_id: str, notice_id: str) -> PrivacyNotice | None:
        return (await self.db.execute(self._q(tenant_id).where(PrivacyNotice.id == notice_id))).scalar_one_or_none()

    # ------------------------------------------------------------ validazioni
    async def _check_unit(self, tenant_id: str, unit_id: str) -> None:
        found = (await self.db.execute(select(OrganizationalUnit.id).where(
            OrganizationalUnit.id == unit_id, OrganizationalUnit.tenant_id == tenant_id))).scalar_one_or_none()
        if found is None:
            raise NoticeError("Struttura organizzativa non trovata")

    async def _check_fields(self, tenant_id: str, data: dict) -> dict:
        if data.get("version_label") is not None and not data["version_label"].strip():
            raise NoticeError("L'etichetta della versione è obbligatoria")
        if data.get("content") is not None and len(data["content"]) > MAX_CONTENT:
            raise NoticeError(f"Il testo supera {MAX_CONTENT} caratteri")
        url = (data.get("published_url") or "").strip() or None
        if url and not _URL.match(url):
            raise NoticeError("L'indirizzo di pubblicazione deve iniziare con http:// o https://")
        data["published_url"] = url
        if data.get("document_id"):
            doc = (await self.db.execute(select(Document.id).where(
                Document.id == data["document_id"], Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None)))).scalar_one_or_none()
            if doc is None:
                raise NoticeError("Documento non trovato")
        return data

    def _touch(self, notice: PrivacyNotice, actor: str) -> None:
        notice.updated_by, notice.updated_at = actor, datetime.utcnow()

    # ------------------------------------------------------------ informativa
    async def create(
        self, tenant_id: str, actor: str, title: str, owner_unit_id: str, audience: str | None = None, **version
    ) -> PrivacyNotice:
        await self._check_unit(tenant_id, owner_unit_id)
        data = await self._check_fields(tenant_id, {k: v for k, v in version.items() if k in _VERSION_FIELDS})
        notice = PrivacyNotice(
            tenant_id=tenant_id, code=await next_prefixed_code(self.db, PrivacyNotice, tenant_id, "INF"),
            title=title.strip(), audience=(audience or "").strip() or None, owner_unit_id=owner_unit_id,
            created_by=actor, updated_by=actor, links=[],
        )
        notice.versions.append(PrivacyNoticeVersion(
            version_no=1, version_label=(data.get("version_label") or "1.0").strip(), content=data.get("content") or "",
            effective_from=data.get("effective_from"), published_url=data.get("published_url"),
            document_id=data.get("document_id"), created_by=actor,
        ))
        self.db.add(notice)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "create", entity_id=notice.id, actor_user_id=actor,
                             new_values={"code": notice.code, "status": notice.status.value})
        return notice

    async def update(self, tenant_id: str, notice_id: str, actor: str, *, title: str, audience: str | None, owner_unit_id: str):
        notice = await self._editable(tenant_id, notice_id, allow_review=False)
        await self._check_unit(tenant_id, owner_unit_id)
        old = {"title": notice.title, "audience": notice.audience, "owner_unit_id": notice.owner_unit_id}
        notice.title, notice.audience = title.strip(), (audience or "").strip() or None
        notice.owner_unit_id = owner_unit_id
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "update", entity_id=notice.id, actor_user_id=actor,
                             old_values=old, new_values={"title": notice.title, "audience": notice.audience,
                                                         "owner_unit_id": notice.owner_unit_id})
        return notice

    async def _editable(self, tenant_id: str, notice_id: str, *, allow_review: bool) -> PrivacyNotice:
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        if notice.status == NoticeStatus.RETIRED:
            raise NoticeError("L'informativa è ritirata e non si può modificare")
        if notice.status == NoticeStatus.IN_REVIEW and not allow_review:
            raise NoticeError("L'informativa è in approvazione: riportarla in bozza per modificarla")
        return notice

    async def soft_delete(self, tenant_id: str, notice_id: str, actor: str) -> None:
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        if notice.status != NoticeStatus.DRAFT or notice.current_version_id:
            raise NoticeError("Si può eliminare solo un'informativa mai approvata e in bozza; per le altre usare il ritiro")
        notice.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "delete", entity_id=notice.id, actor_user_id=actor,
                             old_values={"code": notice.code})

    # ------------------------------------------------------------ versioni
    async def create_version(
        self, tenant_id: str, notice_id: str, actor: str, *, copy_current: bool = True, **fields
    ) -> PrivacyNoticeVersion:
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        if notice.status != NoticeStatus.APPROVED:
            raise NoticeError(
                "Una nuova versione si crea solo da un'informativa approvata: "
                "se è già in lavorazione o in approvazione, modificare la versione esistente"
            )
        data = await self._check_fields(tenant_id, {k: v for k, v in fields.items() if k in _VERSION_FIELDS})
        if not (data.get("version_label") or "").strip():
            raise NoticeError("L'etichetta della versione è obbligatoria")
        current = notice.current_version
        content = data.get("content")
        if content is None:
            content = current.content if current and copy_current else ""
        version = PrivacyNoticeVersion(
            privacy_notice_id=notice.id, version_no=max(v.version_no for v in notice.versions) + 1,
            version_label=data["version_label"].strip(), content=content, effective_from=data.get("effective_from"),
            published_url=data.get("published_url"), document_id=data.get("document_id"), created_by=actor,
        )
        notice.versions.append(version)
        notice.status = NoticeStatus.DRAFT
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "version_create", entity_id=notice.id, actor_user_id=actor,
                             new_values={"version_no": version.version_no, "version_label": version.version_label})
        return version

    async def update_version(self, tenant_id: str, notice_id: str, version_id: str, actor: str, **fields) -> PrivacyNoticeVersion:
        notice = await self._editable(tenant_id, notice_id, allow_review=False)
        version = next((v for v in notice.versions if v.id == version_id), None)
        if version is None:
            raise NoticeError("Versione non trovata")
        if version.approved_at is not None:
            raise NoticeError("Una versione approvata è immutabile: creare una nuova versione")
        data = await self._check_fields(tenant_id, {k: v for k, v in fields.items() if k in _VERSION_FIELDS})
        for key, value in data.items():
            if key == "content" and value is None:
                continue
            setattr(version, key, value)
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "version_update", entity_id=notice.id, actor_user_id=actor,
                             new_values={"version_no": version.version_no, "version_label": version.version_label})
        return version

    async def diff(self, tenant_id: str, notice_id: str, old_id: str, new_id: str) -> dict:
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        by_id = {v.id: v for v in notice.versions}
        if old_id not in by_id or new_id not in by_id:
            raise NoticeError("Versione non trovata")
        old, new = by_id[old_id], by_id[new_id]
        return {
            "old": old.to_dict(include_content=False), "new": new.to_dict(include_content=False),
            "changes": word_diff(old.content, new.content),
        }

    # ------------------------------------------------------------ collegamenti
    async def set_links(self, tenant_id: str, notice_id: str, actor: str, processing_ids: list[str]) -> PrivacyNotice:
        notice = await self._editable(tenant_id, notice_id, allow_review=True)
        wanted = list(dict.fromkeys(processing_ids))
        if wanted:
            found = set((await self.db.execute(select(ProcessingActivity.id).where(
                ProcessingActivity.tenant_id == tenant_id, ProcessingActivity.deleted_at.is_(None),
                ProcessingActivity.id.in_(wanted)))).scalars().all())
            if found != set(wanted):
                raise NoticeError("Trattamento non trovato")
        before = sorted(l.processing_activity_id for l in notice.links)
        if before == sorted(wanted):
            return notice
        notice.links = [NoticeProcessingLink(privacy_notice_id=notice.id, processing_activity_id=p) for p in wanted]
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "links_update", event_type="privacy_notice.links.updated",
                             entity_id=notice.id, actor_user_id=actor, old_values={"processing_activity_ids": before},
                             new_values={"processing_activity_ids": sorted(wanted)})
        return notice

    # ------------------------------------------------------------ stato
    async def transition(self, tenant_id: str, notice_id: str, actor: str, target: NoticeStatus) -> PrivacyNotice:
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        if target not in NOTICE_TRANSITIONS.get(notice.status, set()):
            raise NoticeError(f"Transizione non ammessa: {notice.status.value} → {target.value}")
        working = notice.working_version
        today, now = date.today(), datetime.utcnow()
        if target == NoticeStatus.IN_REVIEW:
            missing = []
            if working is None:
                missing.append("versione in lavorazione")
            else:
                if not (working.content or "").strip():
                    missing.append("testo")
                if working.effective_from is None:
                    missing.append("data di decorrenza")
            if missing:
                raise NoticeIncomplete("Per inviare l'informativa in approvazione mancano: " + ", ".join(missing))
        if target == NoticeStatus.APPROVED:
            previous = notice.current_version
            if previous is not None and (previous.effective_to is None or previous.effective_to >= working.effective_from):
                previous.effective_to = working.effective_from - timedelta(days=1)
            working.approved_by, working.approved_at = actor, now
            notice.current_version_id = working.id
        if target == NoticeStatus.RETIRED:
            current = notice.current_version
            if current is not None and (current.effective_to is None or current.effective_to > today):
                current.effective_to = today
        old = notice.status
        notice.status = target
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "privacy_notice", "status_change", event_type=f"privacy_notice.status.{target.value.lower()}",
            entity_id=notice.id, actor_user_id=actor, old_values={"status": old.value}, new_values={"status": target.value},
        )
        return notice

    async def confirm_review(self, tenant_id: str, notice_id: str, actor: str, notes: str | None) -> PrivacyNotice:
        """Conferma che l'informativa resta coerente con i trattamenti, senza creare una nuova versione."""
        notice = await self.get(tenant_id, notice_id)
        if notice is None:
            raise NoticeError("Informativa non trovata")
        if notice.current_version_id is None or notice.status == NoticeStatus.RETIRED:
            raise NoticeError("La verifica di coerenza si conferma su un'informativa con una versione approvata in vigore")
        notice.last_reviewed_at, notice.last_reviewed_by = datetime.utcnow(), actor
        notice.review_notes = (notes or "").strip() or None
        self._touch(notice, actor)
        await self.db.flush()
        await self.audit.log(tenant_id, "privacy_notice", "review_confirmed", event_type="privacy_notice.review_confirmed",
                             entity_id=notice.id, actor_user_id=actor, new_values={"notes": notice.review_notes})
        return notice
