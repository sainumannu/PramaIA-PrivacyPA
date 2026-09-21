"""
PrivacyPA — Flussi dei dati (§20): da dove a dove circolano i dati di un trattamento, con quale frequenza e canale.

Un flusso appartiene a un trattamento e ne segue lo scope. Gli estremi possono riferirsi a un'anagrafica dell'ente
(sistema, soggetto, struttura) oppure a un'etichetta libera (interessati, soggetto esterno non censito).
Le categorie di dati del flusso devono essere tra quelle del trattamento.
"""
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.privacypa_assets import Asset
from backend.models.privacypa_catalog import DataCategoryCatalog
from backend.models.privacypa_notices import (
    REFERENCED_ENDPOINTS,
    DataFlow,
    DataFlowCategory,
    FlowEndpointType,
)
from backend.models.privacypa_organization import OrganizationalUnit
from backend.models.privacypa_parties import Party
from backend.models.privacypa_processing import ProcessingDataCategory
from backend.services.privacypa_audit_service import AuditService
from backend.services.privacypa_processing_service import ProcessingService

_FIELDS = {
    "source_type", "source_id", "source_label", "target_type", "target_id", "target_label",
    "frequency", "channel", "description", "security_notes",
}


class FlowError(ValueError):
    """Flusso non valido o riferimenti non coerenti (400)."""


class DataFlowService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    def _q(self, tenant_id: str):
        return select(DataFlow).where(DataFlow.tenant_id == tenant_id, DataFlow.deleted_at.is_(None))

    async def list_for_processing(self, tenant_id: str, processing_id: str) -> list[DataFlow]:
        rows = await self.db.execute(
            self._q(tenant_id).where(DataFlow.processing_activity_id == processing_id).order_by(DataFlow.created_at, DataFlow.id)
        )
        return list(rows.scalars().all())

    async def get(self, tenant_id: str, flow_id: str) -> DataFlow | None:
        return (await self.db.execute(self._q(tenant_id).where(DataFlow.id == flow_id))).scalar_one_or_none()

    # ------------------------------------------------------------ validazioni
    async def _endpoint_name(self, tenant_id: str, kind: FlowEndpointType, entity_id: str) -> str | None:
        model, column = {
            FlowEndpointType.ASSET: (Asset, Asset.name),
            FlowEndpointType.PARTY: (Party, Party.legal_name),
            FlowEndpointType.ORGANIZATIONAL_UNIT: (OrganizationalUnit, OrganizationalUnit.name),
        }[kind]
        stmt = select(column).where(model.id == entity_id, model.tenant_id == tenant_id)
        if hasattr(model, "deleted_at"):
            stmt = stmt.where(model.deleted_at.is_(None))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _check_endpoint(self, tenant_id: str, side: str, data: dict) -> None:
        kind: FlowEndpointType = data[f"{side}_type"]
        entity_id, label = data.get(f"{side}_id"), (data.get(f"{side}_label") or "").strip() or None
        name = {"source": "di origine", "target": "di destinazione"}[side]
        if kind in REFERENCED_ENDPOINTS:
            if not entity_id:
                raise FlowError(f"L'estremo {name} ({kind.value}) deve indicare un elemento dell'ente")
            if await self._endpoint_name(tenant_id, kind, entity_id) is None:
                raise FlowError(f"Elemento non trovato per l'estremo {name}")
        else:
            if entity_id:
                raise FlowError(f"L'estremo {name} ({kind.value}) non si riferisce a un'anagrafica: usare l'etichetta")
            if not label:
                raise FlowError(f"L'estremo {name} ({kind.value}) richiede un'etichetta")
        data[f"{side}_label"] = label

    async def _check_categories(self, tenant_id: str, processing_id: str, category_ids: list[str]) -> list[str]:
        wanted = list(dict.fromkeys(category_ids))
        if not wanted:
            return []
        known = set((await self.db.execute(select(DataCategoryCatalog.id).where(
            DataCategoryCatalog.id.in_(wanted),
            or_(DataCategoryCatalog.tenant_id.is_(None), DataCategoryCatalog.tenant_id == tenant_id)))).scalars().all())
        if known != set(wanted):
            raise FlowError("Categoria di dati non trovata")
        linked = set((await self.db.execute(select(ProcessingDataCategory.data_category_id).where(
            ProcessingDataCategory.processing_activity_id == processing_id,
            ProcessingDataCategory.data_category_id.in_(wanted)))).scalars().all())
        if linked != set(wanted):
            raise FlowError("Una categoria di dati non è associata al trattamento")
        return wanted

    async def _validate(self, tenant_id: str, processing_id: str, data: dict, category_ids: list[str]) -> list[str]:
        if not (data.get("description") or "").strip():
            raise FlowError("La descrizione del flusso è obbligatoria")
        await self._check_endpoint(tenant_id, "source", data)
        await self._check_endpoint(tenant_id, "target", data)
        same = data["source_type"] == data["target_type"] and (
            (data.get("source_id") and data.get("source_id") == data.get("target_id"))
            or (not data.get("source_id") and (data["source_label"] or "").lower() == (data["target_label"] or "").lower())
        )
        if same:
            raise FlowError("Origine e destinazione del flusso coincidono")
        return await self._check_categories(tenant_id, processing_id, category_ids)

    async def _touch_processing(self, tenant_id: str, processing_id: str, actor: str) -> None:
        activity = await ProcessingService(self.db).get_activity(tenant_id, processing_id)
        if activity is not None:
            activity.updated_by, activity.updated_at = actor, datetime.utcnow()
            await self.db.flush()

    # ------------------------------------------------------------ scritture
    async def create(self, tenant_id: str, actor: str, processing_id: str, data_category_ids: list[str], **fields) -> DataFlow:
        data = {k: v for k, v in fields.items() if k in _FIELDS}
        categories = await self._validate(tenant_id, processing_id, data, data_category_ids)
        flow = DataFlow(tenant_id=tenant_id, processing_activity_id=processing_id, created_by=actor, updated_by=actor, **data)
        flow.categories = [DataFlowCategory(data_category_id=c) for c in categories]
        self.db.add(flow)
        await self.db.flush()
        await self._touch_processing(tenant_id, processing_id, actor)
        await self.audit.log(tenant_id, "data_flow", "create", entity_id=flow.id, actor_user_id=actor,
                             new_values=flow.to_dict(), metadata={"processing_activity_id": processing_id})
        return flow

    async def update(self, tenant_id: str, flow_id: str, actor: str, data_category_ids: list[str], **fields) -> DataFlow:
        flow = await self.get(tenant_id, flow_id)
        if flow is None:
            raise FlowError("Flusso non trovato")
        old = flow.to_dict()
        merged = {k: getattr(flow, k) for k in _FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _FIELDS})
        categories = await self._validate(tenant_id, flow.processing_activity_id, merged, data_category_ids)
        for key, value in merged.items():
            setattr(flow, key, value)
        flow.categories = [DataFlowCategory(data_flow_id=flow.id, data_category_id=c) for c in categories]
        flow.updated_by, flow.updated_at = actor, datetime.utcnow()
        await self.db.flush()
        await self._touch_processing(tenant_id, flow.processing_activity_id, actor)
        await self.audit.log(tenant_id, "data_flow", "update", entity_id=flow.id, actor_user_id=actor, old_values=old,
                             new_values=flow.to_dict(), metadata={"processing_activity_id": flow.processing_activity_id})
        return flow

    async def soft_delete(self, tenant_id: str, flow_id: str, actor: str) -> None:
        flow = await self.get(tenant_id, flow_id)
        if flow is None:
            raise FlowError("Flusso non trovato")
        flow.deleted_at, flow.updated_by = datetime.utcnow(), actor
        await self.db.flush()
        await self._touch_processing(tenant_id, flow.processing_activity_id, actor)
        await self.audit.log(tenant_id, "data_flow", "delete", entity_id=flow.id, actor_user_id=actor,
                             old_values=flow.to_dict(), metadata={"processing_activity_id": flow.processing_activity_id})

    # ------------------------------------------------------------ grafo
    async def graph(self, tenant_id: str, processing_id: str) -> dict:
        """Nodi e archi del trattamento, con i nomi risolti, pronti per la visualizzazione."""
        flows = await self.list_for_processing(tenant_id, processing_id)
        categories = {c.id: c.name for c in (await self.db.execute(select(DataCategoryCatalog))).scalars().all()}
        nodes: dict[str, dict] = {}

        async def node(kind: FlowEndpointType, entity_id: str | None, label: str | None) -> str:
            key = f"{kind.value}:{entity_id or (label or '').strip().lower()}"
            if key not in nodes:
                name = await self._endpoint_name(tenant_id, kind, entity_id) if entity_id else None
                nodes[key] = {"key": key, "type": kind.value, "id": entity_id, "label": name or label}
            return key

        edges = []
        for f in flows:
            edges.append({
                "id": f.id,
                "source": await node(f.source_type, f.source_id, f.source_label),
                "target": await node(f.target_type, f.target_id, f.target_label),
                "frequency": f.frequency.value, "channel": f.channel.value, "description": f.description,
                "categories": [categories.get(c.data_category_id, c.data_category_id) for c in f.categories],
            })
        return {"processing_activity_id": processing_id, "nodes": list(nodes.values()), "edges": edges}
