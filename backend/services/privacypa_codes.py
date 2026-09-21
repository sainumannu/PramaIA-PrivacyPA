from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def next_prefixed_code(db: AsyncSession, model, tenant_id: str, prefix: str) -> str:
    """Genera il prossimo codice leggibile per tenant (es. AS-000001)."""
    result = await db.execute(
        select(func.max(model.code)).where(model.tenant_id == tenant_id)
    )
    current = result.scalar_one_or_none()
    if not current or not str(current).startswith(f"{prefix}-"):
        return f"{prefix}-000001"
    try:
        n = int(str(current).split("-", 1)[1])
    except ValueError:
        n = 0
    return f"{prefix}-{n + 1:06d}"
