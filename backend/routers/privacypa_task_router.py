from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import require_tenant
from backend.auth.privacypa_permissions import require_module_view, require_permission, roles_for
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_tasks import TaskPriority, TaskSourceType, TaskStatus
from backend.services.privacypa_task_service import TaskService
from backend.utils.concurrency import assert_version, etag_of, require_if_match

router = APIRouter(
    prefix="/api/privacypa/tasks",
    tags=["PrivacyPA - Task"],
    dependencies=[Depends(require_module_view("task"))],
)


class TaskUpsert(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    source_type: TaskSourceType = TaskSourceType.MANUAL
    source_id: str | None = None
    assigned_user_id: str | None = None
    assigned_unit_id: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.OPEN
    due_at: datetime | None = None


async def _get_visible_task(
    db: AsyncSession, tenant_id: str, user: TokenPayload, task_id: str, version: int | None = None,
    write_code: str | None = None,
):
    """Task (404) visibile all'utente (403); per le scritture, nello scope dei ruoli che hanno il permesso."""
    task = await TaskService(db).get(tenant_id, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task non trovato")
    roles = await roles_for(db, tenant_id, write_code) if write_code else None
    if not (await build_scope(db, tenant_id, user, roles=roles)).task_visible(task):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fuori dallo scope organizzativo dell'utente")
    if version is not None:
        assert_version(task, version)
    return task


@router.get("")
async def list_tasks(
    status_filter: TaskStatus | None = None,
    assigned_user_id: str | None = None,
    search: str | None = None,
    overdue_only: bool = False,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await build_scope(db, tenant_id, user)
    items = await TaskService(db).list_tasks(
        tenant_id,
        status=status_filter,
        assigned_user_id=assigned_user_id,
        search=search,
        overdue_only=overdue_only,
    )
    return [t.to_dict() for t in scope.filter_tasks(items)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskUpsert,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("task.create")),
    db: AsyncSession = Depends(get_db),
):
    if body.source_type == TaskSourceType.PROCESSING and body.source_id:
        # Un task non si aggancia a un trattamento fuori dallo scope di chi ha il permesso di creare.
        scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, "task.create"))
        scope.assert_processing(body.source_id)
    task = await TaskService(db).create(tenant_id, user.sub or user.email, **body.model_dump())
    return task.to_dict()


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    body: TaskUpsert,
    tenant_id: str = Depends(require_tenant),
    version: int = Depends(require_if_match),
    user: TokenPayload = Depends(require_permission("task.edit")),
    db: AsyncSession = Depends(get_db),
):
    await _get_visible_task(db, tenant_id, user, task_id, version, "task.edit")
    if body.source_type == TaskSourceType.PROCESSING and body.source_id:
        scope = await build_scope(db, tenant_id, user, roles=await roles_for(db, tenant_id, "task.edit"))
        scope.assert_processing(body.source_id)
    try:
        task = await TaskService(db).update(tenant_id, task_id, user.sub or user.email, **body.model_dump())
        return task.to_dict()
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    tenant_id: str = Depends(require_tenant),
    user: TokenPayload = Depends(require_permission("task.delete")),
    db: AsyncSession = Depends(get_db),
):
    await _get_visible_task(db, tenant_id, user, task_id, write_code="task.delete")
    try:
        await TaskService(db).soft_delete(tenant_id, task_id, user.sub or user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
