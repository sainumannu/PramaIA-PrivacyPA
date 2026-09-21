"""Allineamento colonne su DB esistenti (soluzione ponte in attesa di migrazioni versionate)."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.db.init_db import add_missing_columns

OLD_DDL = """
CREATE TABLE privacypa_document_versions (
    id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL, version_no INTEGER NOT NULL,
    file_name VARCHAR(255) NOT NULL, mime_type VARCHAR(150) NOT NULL, file_size INTEGER NOT NULL,
    storage_key VARCHAR(500) NOT NULL, sha256 VARCHAR(64) NOT NULL, uploaded_by VARCHAR(100),
    uploaded_at DATETIME, effective_date DATE, expiry_date DATE, notes TEXT
)
"""


@pytest.mark.asyncio
async def test_adds_missing_columns_preserving_rows_and_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(OLD_DDL))
        await conn.execute(text(
            "INSERT INTO privacypa_document_versions (id, document_id, version_no, file_name, mime_type,"
            " file_size, storage_key, sha256) VALUES ('v1','d1',1,'a.pdf','application/pdf',10,'k','h')"
        ))
        added = await conn.run_sync(add_missing_columns)
        assert set(added) == {
            "privacypa_document_versions.scan_status",
            "privacypa_document_versions.scan_detail",
            "privacypa_document_versions.scanned_at",
        }
        row = (await conn.execute(text("SELECT scan_status, scan_detail FROM privacypa_document_versions"))).one()
        # i file già presenti non risultano mai scansionati: restano non scaricabili finché non ri-scansionati
        assert row == ("QUARANTINED", None)
        assert await conn.run_sync(add_missing_columns) == []
    await engine.dispose()
