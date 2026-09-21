from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_catalog import (
    DataCategoryCatalog,
    DataSubjectCategoryCatalog,
    GdprDataClass,
    LegalBasisCatalog,
)
from backend.services.privacypa_audit_service import AuditService


class CatalogService:
    """
    Cataloghi di base. tenant_id nullable = template globale;
    valorizzato = estensione del tenant. I template globali non si cancellano.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def list_legal_basis(self) -> list[LegalBasisCatalog]:
        result = await self.db.execute(select(LegalBasisCatalog))
        return list(result.scalars().all())

    async def list_data_subject_categories(self, tenant_id: str) -> list[DataSubjectCategoryCatalog]:
        result = await self.db.execute(
            select(DataSubjectCategoryCatalog).where(
                or_(
                    DataSubjectCategoryCatalog.tenant_id.is_(None),
                    DataSubjectCategoryCatalog.tenant_id == tenant_id,
                )
            )
        )
        return list(result.scalars().all())

    async def list_data_categories(self, tenant_id: str) -> list[DataCategoryCatalog]:
        result = await self.db.execute(
            select(DataCategoryCatalog).where(
                or_(
                    DataCategoryCatalog.tenant_id.is_(None),
                    DataCategoryCatalog.tenant_id == tenant_id,
                )
            )
        )
        return list(result.scalars().all())

    async def create_legal_basis(self, actor: str, **fields) -> LegalBasisCatalog:
        code = (fields.get("code") or "").strip().upper()
        if not code:
            raise ValueError("Codice obbligatorio")
        existing = await self.db.get(LegalBasisCatalog, code)
        if existing:
            raise ValueError(f"Base giuridica '{code}' già presente")
        item = LegalBasisCatalog(
            code=code,
            article=fields.get("article") or "",
            paragraph=fields.get("paragraph"),
            letter=fields.get("letter"),
            description=fields.get("description") or code,
        )
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            "global", "legal_basis", "create", entity_id=code, actor_user_id=actor, new_values=item.to_dict()
        )
        await self.db.refresh(item)
        return item

    async def create_subject(self, tenant_id: str, actor: str, **fields) -> DataSubjectCategoryCatalog:
        item = DataSubjectCategoryCatalog(
            tenant_id=tenant_id,
            code=(fields.get("code") or "").strip().upper(),
            name=(fields.get("name") or "").strip(),
            description=fields.get("description"),
        )
        if not item.code or not item.name:
            raise ValueError("Codice e nome sono obbligatori")
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "data_subject_category", "create",
            entity_id=item.id, actor_user_id=actor, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def update_subject(self, tenant_id: str, item_id: str, actor: str, **fields) -> DataSubjectCategoryCatalog:
        item = await self.db.get(DataSubjectCategoryCatalog, item_id)
        if not item or item.tenant_id != tenant_id:
            raise ValueError("Voce di catalogo non trovata o non modificabile (template globale)")
        old = item.to_dict()
        for k in ("code", "name", "description"):
            if k in fields and fields[k] is not None:
                setattr(item, k, fields[k].strip() if isinstance(fields[k], str) else fields[k])
        await self.db.flush()
        await self.audit.log(
            tenant_id, "data_subject_category", "update",
            entity_id=item.id, actor_user_id=actor, old_values=old, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def delete_subject(self, tenant_id: str, item_id: str, actor: str) -> None:
        item = await self.db.get(DataSubjectCategoryCatalog, item_id)
        if not item or item.tenant_id != tenant_id:
            raise ValueError("Voce di catalogo non trovata o non eliminabile (template globale)")
        await self.audit.log(
            tenant_id, "data_subject_category", "delete",
            entity_id=item.id, actor_user_id=actor, old_values=item.to_dict(),
        )
        await self.db.delete(item)
        await self.db.flush()

    async def create_data_category(self, tenant_id: str, actor: str, **fields) -> DataCategoryCatalog:
        gdpr = fields.get("gdpr_class") or GdprDataClass.ORDINARY
        if isinstance(gdpr, str):
            gdpr = GdprDataClass(gdpr)
        item = DataCategoryCatalog(
            tenant_id=tenant_id,
            parent_id=fields.get("parent_id") or None,
            code=(fields.get("code") or "").strip().upper(),
            name=(fields.get("name") or "").strip(),
            gdpr_class=gdpr,
            description=fields.get("description"),
        )
        if not item.code or not item.name:
            raise ValueError("Codice e nome sono obbligatori")
        self.db.add(item)
        await self.db.flush()
        await self.audit.log(
            tenant_id, "data_category", "create",
            entity_id=item.id, actor_user_id=actor, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def update_data_category(self, tenant_id: str, item_id: str, actor: str, **fields) -> DataCategoryCatalog:
        item = await self.db.get(DataCategoryCatalog, item_id)
        if not item or item.tenant_id != tenant_id:
            raise ValueError("Voce di catalogo non trovata o non modificabile (template globale)")
        old = item.to_dict()
        if fields.get("name"):
            item.name = fields["name"].strip()
        if fields.get("code"):
            item.code = fields["code"].strip().upper()
        if "description" in fields:
            item.description = fields["description"]
        if "parent_id" in fields:
            item.parent_id = fields["parent_id"] or None
        if fields.get("gdpr_class"):
            item.gdpr_class = (
                GdprDataClass(fields["gdpr_class"]) if isinstance(fields["gdpr_class"], str) else fields["gdpr_class"]
            )
        await self.db.flush()
        await self.audit.log(
            tenant_id, "data_category", "update",
            entity_id=item.id, actor_user_id=actor, old_values=old, new_values=item.to_dict(),
        )
        await self.db.refresh(item)
        return item

    async def delete_data_category(self, tenant_id: str, item_id: str, actor: str) -> None:
        item = await self.db.get(DataCategoryCatalog, item_id)
        if not item or item.tenant_id != tenant_id:
            raise ValueError("Voce di catalogo non trovata o non eliminabile (template globale)")
        await self.audit.log(
            tenant_id, "data_category", "delete",
            entity_id=item.id, actor_user_id=actor, old_values=item.to_dict(),
        )
        await self.db.delete(item)
        await self.db.flush()

    async def import_subjects(self, tenant_id: str, actor: str, rows: list[dict]) -> int:
        created = 0
        for row in rows:
            try:
                await self.create_subject(tenant_id, actor, **row)
                created += 1
            except ValueError:
                continue
        return created

    async def import_data_categories(self, tenant_id: str, actor: str, rows: list[dict]) -> int:
        created = 0
        for row in rows:
            try:
                await self.create_data_category(tenant_id, actor, **row)
                created += 1
            except ValueError:
                continue
        return created
