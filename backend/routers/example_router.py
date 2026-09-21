from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import get_current_user, TokenPayload
from backend.db.database import get_db
from backend.services.example_service import ExampleService

router = APIRouter(prefix="/api/items", tags=["items"])


class ItemCreate(BaseModel):
    title: str
    description: str = ""


class ItemOut(BaseModel):
    id: int
    title: str
    description: str | None
    owner_email: str
    created_at: str | None

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[ItemOut])
async def list_items(
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista tutti gli item dell'utente corrente."""
    svc = ExampleService(db)
    items = await svc.get_all(user.email)
    return [ItemOut(**item.to_dict()) for item in items]


@router.post("/", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: ItemCreate,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea un nuovo item."""
    svc = ExampleService(db)
    item = await svc.create(body.title, body.description, user.email)
    return ItemOut(**item.to_dict())


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: int,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Elimina un item dell'utente corrente."""
    svc = ExampleService(db)
    deleted = await svc.delete(item_id, user.email)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
