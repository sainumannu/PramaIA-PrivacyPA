from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_tasks import PrivacyTask, TaskStatus
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_codes import next_prefixed_code
from backend.services.privacypa_refs import ensure_exists, ensure_task_source

_EDITABLE = {
    "title", "description", "source_type", "source_id", "assigned_user_id",
    "assigned_unit_id", "priority", "status", "due_at",
}


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(PrivacyTask).where(PrivacyTask.tenant_id == tenant_id, PrivacyTask.deleted_at.is_(None))

    async def list_tasks(
        self,
        tenant_id: str,
        status: TaskStatus | None = None,
        assigned_user_id: str | None = None,
        search: str | None = None,
        overdue_only: bool = False,
    ) -> list[PrivacyTask]:
        stmt = self._q(tenant_id)
        if status:
            stmt = stmt.where(PrivacyTask.status == status)
        if assigned_user_id:
            stmt = stmt.where(PrivacyTask.assigned_user_id == assigned_user_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(PrivacyTask.title.ilike(like) | PrivacyTask.code.ilike(like))
        if overdue_only:
            stmt = stmt.where(
                PrivacyTask.due_at.is_not(None),
                PrivacyTask.due_at < datetime.utcnow(),
                PrivacyTask.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        result = await self.db.execute(stmt.order_by(PrivacyTask.code))
        return list(result.scalars().all())

    async def get(self, tenant_id: str, task_id: str) -> PrivacyTask | None:
        result = await self.db.execute(self._q(tenant_id).where(PrivacyTask.id == task_id))
        return result.scalar_one_or_none()

    async def _check_references(self, tenant_id: str, fields: dict, current=None) -> None:
        await ensure_exists(self.db, tenant_id, "unit", fields.get("assigned_unit_id"))
        if fields.get("source_id"):
            await ensure_task_source(self.db, tenant_id, fields.get("source_type") or getattr(current, "source_type", None), fields["source_id"])

    async def create(self, tenant_id: str, actor: str, title: str, **fields) -> PrivacyTask:
        await self._check_references(tenant_id, fields)
        task = PrivacyTask(
            tenant_id=tenant_id,
            code=await next_prefixed_code(self.db, PrivacyTask, tenant_id, "TK"),
            title=title.strip(),
            created_by=actor,
            **{k: v for k, v in fields.items() if k in _EDITABLE},
        )
        self.db.add(task)
        await self.db.flush()
        await self.audit.log(tenant_id, "task", "create", entity_id=task.id, actor_user_id=actor, new_values=task.to_dict())
        await self.db.refresh(task)
        return task

    async def update(self, tenant_id: str, task_id: str, actor: str, **fields) -> PrivacyTask:
        task = await self.get(tenant_id, task_id)
        if not task:
            raise ValueError("Task non trovato")
        await self._check_references(tenant_id, fields, task)
        old = task.to_dict()
        new_status = fields.get("status")
        for k, v in fields.items():
            if k in _EDITABLE:
                setattr(task, k, v)
        if new_status in (TaskStatus.COMPLETED, TaskStatus.COMPLETED.value) and not task.completed_at:
            task.completed_at = datetime.utcnow()
        if new_status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
            task.completed_at = None
        task.updated_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "task", "update", entity_id=task.id, actor_user_id=actor, old_values=old, new_values=task.to_dict())
        await self.db.refresh(task)
        return task

    async def soft_delete(self, tenant_id: str, task_id: str, actor: str) -> None:
        task = await self.get(tenant_id, task_id)
        if not task:
            raise ValueError("Task non trovato")
        task.deleted_at = datetime.utcnow()
        await self.db.flush()
        await self.audit.log(tenant_id, "task", "delete", entity_id=task.id, actor_user_id=actor)

    async def overdue_and_upcoming(self, tenant_id: str, days: int = 30) -> dict:
        now = datetime.utcnow()
        soon = now + timedelta(days=days)
        tasks = await self.list_tasks(tenant_id)
        overdue, due_7, due_30, mine = [], [], [], []
        for t in tasks:
            if t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                continue
            if t.due_at and t.due_at < now:
                overdue.append(t)
            elif t.due_at and t.due_at <= now + timedelta(days=7):
                due_7.append(t)
            elif t.due_at and t.due_at <= soon:
                due_30.append(t)
        return {
            "overdue": overdue,
            "due_7": due_7,
            "due_30": due_30,
        }
