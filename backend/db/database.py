import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from typing import AsyncGenerator

DEFAULT_DATABASE_URL = "sqlite:///./PramaIA-PrivacyManager.db"

# Nessun .env al primo avvio -> usa SQLite locale di default, così il
# backend può partire e mostrare il Setup Wizard (vedi setup_router.py).
DATABASE_URL = os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL

# SQLite async URL: sqlite:///./file.db → sqlite+aiosqlite:///./file.db
if DATABASE_URL.startswith("sqlite:///") and not DATABASE_URL.startswith("sqlite+aiosqlite"):
    DATABASE_URL = DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def sync_url(url: str) -> str:
    """URL con il driver sincrono equivalente (per le migrazioni): sqlite+aiosqlite -> sqlite, postgresql+asyncpg -> postgresql."""
    return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql")


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
