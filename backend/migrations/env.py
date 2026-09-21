"""
Ambiente Alembic: usa gli stessi modelli e lo stesso DATABASE_URL dell'applicazione.

L'applicazione usa driver asincroni; le migrazioni girano con quello sincrono equivalente
(sqlite+aiosqlite -> sqlite, postgresql+asyncpg -> postgresql).
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import backend.db.init_db  # noqa: F401  (registra tutti i modelli su Base.metadata)
from backend.db.database import DATABASE_URL, Base, sync_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or sync_url(DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",  # SQLite non supporta ALTER complessi
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
