"""
Migrazioni del database con Alembic (§59, §90).

`upgrade_database` porta il database all'ultima revisione:
- database nuovo: applica tutte le migrazioni dalla baseline;
- database creato dal vecchio percorso (`create_all` + `add_missing_columns`, senza `alembic_version`): lo
  allinea ai modelli con quel ponte e lo marca all'ultima revisione, senza ricreare nulla;
- database già gestito da Alembic: applica le revisioni mancanti.

Si attiva all'avvio con `MIGRATIONS=alembic`; senza, l'applicazione usa ancora il ponte `init_db`.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.db.database import DATABASE_URL, Base, sync_url

_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "backend" / "migrations"))
    config.set_main_option("sqlalchemy.url", (url or sync_url(DATABASE_URL)).replace("%", "%%"))
    return config


def upgrade_database(url: str | None = None) -> str:
    """Restituisce "upgraded" (migrazioni applicate) o "stamped" (database preesistente, allineato e marcato)."""
    from backend.db.init_db import add_missing_columns

    target = url or sync_url(DATABASE_URL)
    config = alembic_config(target)
    engine = create_engine(target)
    try:
        tables = set(inspect(engine).get_table_names())
        if tables and "alembic_version" not in tables:
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                add_missing_columns(conn)
            command.stamp(config, "head")
            return "stamped"
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    return "upgraded"
