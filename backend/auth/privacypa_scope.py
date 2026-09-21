"""
PrivacyPA — Visibilità dei record per scope organizzativo (§10, §37.3, §39, §133).

`VisibilityScope` è calcolato una volta per richiesta e risponde a "questo
record è visibile/modificabile dall'utente?". Regole:

- utente con visibilità sull'intero ente (Admin Portal o scope TENANT): tutto;
- trattamento: visibile se la sua struttura è nello scope;
- DPIA: visibile se il trattamento collegato è visibile (senza trattamento è
  un record di livello ente: solo visibilità intera);
- documento: visibile se creato dall'utente o collegato a un trattamento visibile;
- task: visibile se assegnato/creato dall'utente, assegnato a una struttura
  nello scope, o originato da un trattamento visibile.

Asset, fornitori, rischi e trasferimenti restano registri condivisi a livello
ente (single source of truth, §2.1) e non sono filtrati qui.
"""
from dataclasses import dataclass, field
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload
from backend.auth.privacypa_authz import get_accessible_unit_ids, is_portal_global_or_tenant_admin
from backend.auth.privacypa_confidentiality import LevelAccess, load_level_access
from backend.models.privacypa_identity import PrivacyRoleCode
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_tasks import TaskSourceType

PROCESSING_LINK_TYPES = {"processing_activity", "processing"}


def _forbidden(detail: str = "Fuori dallo scope organizzativo dell'utente") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


@dataclass
class VisibilityScope:
    user_id: str
    unit_ids: set[str] | None
    processing_ids: set[str] = field(default_factory=set)
    # Regole di riservatezza (§134): si aggiungono, non sostituiscono, lo scope per struttura.
    levels: LevelAccess | None = None

    @property
    def unrestricted(self) -> bool:
        return self.unit_ids is None

    def unit_visible(self, unit_id: str | None) -> bool:
        return self.unrestricted or bool(unit_id and unit_id in (self.unit_ids or set()))

    def processing_visible(self, processing_id: str | None) -> bool:
        return self.unrestricted or bool(processing_id and processing_id in self.processing_ids)

    def assert_unit(self, unit_id: str | None) -> None:
        if not self.unit_visible(unit_id):
            raise _forbidden()

    def assert_processing(self, processing_id: str | None) -> None:
        if not self.processing_visible(processing_id):
            raise _forbidden()

    def level_ok(self, level, *personal) -> bool:
        """Il livello di riservatezza dell'elemento ammette l'utente (o l'utente è l'autore/responsabile indicato in `personal`)."""
        return self.levels is None or self.levels.can_read(level, *personal)

    def assert_level(self, level, *personal) -> None:
        if not self.level_ok(level, *personal):
            raise _forbidden("Livello di riservatezza non accessibile all'utente")

    def assert_level_change(self, old, new, *personal) -> None:
        """Chi non può leggere un livello non lo assegna né lo toglie (né abbassa la riservatezza di ciò che non vede)."""
        if old is not None:
            self.assert_level(old, *personal)
        if new is not None:
            self.assert_level(new)        # il livello da assegnare lo si legge per ruolo: essere l'autore non basta

    def dpia_visible(self, dpia) -> bool:
        return self.processing_visible(dpia.processing_activity_id) and self.level_ok(dpia.confidentiality_level, dpia.created_by)

    def document_visible(self, doc) -> bool:
        if not self.level_ok(doc.confidentiality_level, doc.created_by):
            return False
        if self.unrestricted or doc.created_by == self.user_id:
            return True
        return any(
            lnk.entity_type in PROCESSING_LINK_TYPES and lnk.entity_id in self.processing_ids
            for lnk in doc.links
        )

    def task_visible(self, task) -> bool:
        if self.unrestricted:
            return True
        if self.user_id in (task.assigned_user_id, task.created_by):
            return True
        if task.assigned_unit_id and task.assigned_unit_id in (self.unit_ids or set()):
            return True
        return task.source_type == TaskSourceType.PROCESSING and task.source_id in self.processing_ids

    def filter_dpias(self, items: Iterable) -> list:
        return [d for d in items if self.dpia_visible(d)]

    def filter_documents(self, items: Iterable) -> list:
        return [d for d in items if self.document_visible(d)]

    def filter_tasks(self, items: Iterable) -> list:
        return [t for t in items if self.task_visible(t)]


async def build_scope(
    db: AsyncSession,
    tenant_id: str,
    user: TokenPayload,
    roles: Iterable[PrivacyRoleCode] | None = None,
) -> VisibilityScope:
    """
    Costruisce lo scope dell'utente. Con `roles` considera solo le assegnazioni
    con quei ruoli (usare i ruoli di scrittura/approvazione per i controlli di
    modifica, nessun filtro per le letture).
    """
    user_id = user.sub or user.email
    levels = await load_level_access(db, tenant_id, user)
    unit_ids = await get_accessible_unit_ids(
        db, tenant_id, user_id, is_portal_global_or_tenant_admin(user), roles=roles
    )
    if unit_ids is None:
        return VisibilityScope(user_id=user_id, unit_ids=None, levels=levels)
    processing_ids: set[str] = set()
    if unit_ids:
        result = await db.execute(
            select(ProcessingActivity.id).where(
                ProcessingActivity.tenant_id == tenant_id,
                ProcessingActivity.deleted_at.is_(None),
                ProcessingActivity.organizational_unit_id.in_(unit_ids),
            )
        )
        processing_ids = set(result.scalars().all())
    return VisibilityScope(user_id=user_id, unit_ids=unit_ids, processing_ids=processing_ids, levels=levels)
