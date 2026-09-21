import asyncio
import logging
import os

from sqlalchemy import inspect, text

from backend.db.database import engine, Base
# Import all models here to ensure they are registered with SQLAlchemy
from backend.models import example_model  # noqa: F401
from backend.models import team_member  # noqa: F401
from backend.models import license_log  # noqa: F401
from backend.models import permissions_models  # noqa: F401
from backend.models import backup_models  # noqa: F401
from backend.models import privacypa_organization  # noqa: F401
from backend.models import privacypa_identity  # noqa: F401
from backend.models import privacypa_catalog  # noqa: F401
from backend.models import privacypa_audit  # noqa: F401
from backend.models import privacypa_processing  # noqa: F401
from backend.models import privacypa_parties  # noqa: F401
from backend.models import privacypa_assets  # noqa: F401
from backend.models import privacypa_documents  # noqa: F401
from backend.models import privacypa_tasks  # noqa: F401
from backend.models import privacypa_assessments  # noqa: F401
from backend.models import privacypa_risk  # noqa: F401
from backend.models import privacypa_import  # noqa: F401
from backend.models import privacypa_approvals  # noqa: F401
from backend.models import privacypa_retention  # noqa: F401
from backend.models import privacypa_rules  # noqa: F401
from backend.models import privacypa_outbox  # noqa: F401
from backend.models import privacypa_notices  # noqa: F401
from backend.models import privacypa_people  # noqa: F401
from backend.models import privacypa_projects  # noqa: F401
from backend.models import privacypa_annotations  # noqa: F401
from backend.models import privacypa_compliance  # noqa: F401
from backend.models import privacypa_idempotency  # noqa: F401
from backend.models import privacypa_webhooks  # noqa: F401

logger = logging.getLogger(__name__)


def add_missing_columns(sync_conn) -> list[str]:
    """
    Soluzione ponte in attesa di migrazioni versionate (§59, §90): `create_all`
    non modifica le tabelle esistenti, quindi aggiunge le colonne nuove del
    modello che mancano nel DB. Gestisce solo colonne nullable o con
    server_default; le altre vengono segnalate e richiedono una migrazione.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable and col.server_default is None:
                logger.error(
                    "Colonna %s.%s mancante e non nullable senza server_default: serve una migrazione",
                    table.name, col.name,
                )
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=sync_conn.dialect)}'
            if col.server_default is not None:
                ddl += f" DEFAULT '{col.server_default.arg}'"
            if not col.nullable:
                ddl += " NOT NULL"
            sync_conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
            logger.warning("Aggiunta colonna mancante %s.%s", table.name, col.name)
    return added


async def init_database() -> None:
    if os.getenv("MIGRATIONS", "").lower() == "alembic":
        from backend.db.migrate import upgrade_database

        logger.info("Migrazioni Alembic: %s", await asyncio.to_thread(upgrade_database))
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(add_missing_columns)
