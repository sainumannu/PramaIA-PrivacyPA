from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.example_model import ExampleItem


class ExampleService:
    """
    Service di esempio — rinominare e adattare all'applicativo.
    Contiene la logica di business, separata dal router.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self, owner_email: str) -> list[ExampleItem]:
        result = await self.db.execute(
            select(ExampleItem).where(ExampleItem.owner_email == owner_email)
        )
        return list(result.scalars().all())

    async def get_by_id(self, item_id: int, owner_email: str) -> ExampleItem | None:
        result = await self.db.execute(
            select(ExampleItem).where(
                ExampleItem.id == item_id,
                ExampleItem.owner_email == owner_email,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, title: str, description: str, owner_email: str) -> ExampleItem:
        item = ExampleItem(title=title, description=description, owner_email=owner_email)
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete(self, item_id: int, owner_email: str) -> bool:
        item = await self.get_by_id(item_id, owner_email)
        if not item:
            return False
        await self.db.delete(item)
        return True
