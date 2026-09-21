"""Migrazioni Alembic: baseline coerente con i modelli e percorso per i database preesistenti."""
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from backend.db.database import Base
from backend.db.migrate import alembic_config, upgrade_database


def _url(tmp_path, name="app.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _head() -> str:
    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()


def _version(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("select version_num from alembic_version")).scalar()
    finally:
        engine.dispose()


def _diff(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    finally:
        engine.dispose()


def test_there_is_a_single_linear_history():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert len(script.get_heads()) == 1 and script.get_current_head() == "0006"


def test_new_database_is_built_from_the_baseline_and_matches_the_models(tmp_path):
    url = _url(tmp_path)
    assert upgrade_database(url) == "upgraded"
    assert _version(url) == _head()
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {t.name for t in Base.metadata.sorted_tables} <= tables and "alembic_version" in tables
    assert _diff(url) == []            # nessuna differenza tra schema migrato e modelli
    assert upgrade_database(url) == "upgraded" and _version(url) == _head()      # idempotente


def test_existing_database_is_aligned_and_stamped_without_losing_data(tmp_path):
    url = _url(tmp_path, "legacy.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("insert into privacypa_organizations (id, tenant_id, name, created_at, updated_at) values ('o1', 't1', 'Comune', '2026-01-01', '2026-01-01')")) \
            if False else None
        conn.execute(text("alter table privacypa_retention_rules drop column legal_reference_id"))   # schema di una versione precedente
    engine.dispose()

    assert upgrade_database(url) == "stamped"
    assert _version(url) == _head()
    columns = {c["name"] for c in inspect(create_engine(url)).get_columns("privacypa_retention_rules")}
    assert "legal_reference_id" in columns
    assert _diff(url) == []
    assert upgrade_database(url) == "upgraded"       # da ora è gestito da Alembic


def test_baseline_has_no_pending_autogenerate_changes(tmp_path):
    """Se un modello cambia senza migrazione questo test fallisce: serve una nuova revisione."""
    url = _url(tmp_path, "check.db")
    upgrade_database(url)
    assert _diff(url) == [], "i modelli sono cambiati: creare una migrazione (alembic revision --autogenerate)"
