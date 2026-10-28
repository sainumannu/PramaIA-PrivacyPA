"""
PrivacyPA — Snapshot di lettura di un trattamento per i sistemi esterni (connettore VideoPA, W2).

Un solo documento, dati minimi e nessun testo descrittivo: stato del trattamento, DPIA «di riferimento», informative con la
versione in vigore, responsabili con l'atto art. 28 e conservazione.

Decisioni (vedi RISPOSTA_RICHIESTA_CONNETTORI_VIDEOPA.md §1.5):
- A: i giorni di conservazione sono quelli della regola della categoria configurata per l'ente (`retention_category`, confrontata con
  il codice della categoria di dati o con la classe documentale). Mesi e anni si convertono in giorni **di calendario** a partire da
  oggi (mai 30 o 365). Regole a evento o permanenti, categoria non configurata, nessuna regola, regole in conflitto: `days = null`.
- B: le liste restano liste; la DPIA è una sola («di riferimento») più il conteggio delle altre.
- F: una DPIA che l'account non può leggere per riservatezza compare come `{"restricted": true}`, senza titolo né versione.

Il filtro `updated_since` usa la data più recente tra trattamento, DPIA, informative, regole, soggetti e atti: l'eliminazione di un
collegamento non la sposta se non tocca anche il trattamento. La verità è l'ETag, calcolato sul contenuto.
"""
import hashlib
import json
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.privacypa_scope import VisibilityScope
from backend.models.privacypa_approvals import TenantPolicy
from backend.models.privacypa_assessments import Dpia, DpiaStatus
from backend.models.privacypa_catalog import DataCategoryCatalog
from backend.models.privacypa_notices import NoticeProcessingLink, PrivacyNotice
from backend.models.privacypa_parties import AgreementStatus, Party, ProcessingProcessor, ProcessorAgreement
from backend.models.privacypa_processing import ProcessingActivity
from backend.models.privacypa_retention import RetentionRule, RetentionUnit
from backend.services.privacypa_deadlines import add_months_to_date

CONTRACT_VERSION = 1
MAX_PAGE = 200
RETENTION_CATEGORY_MAX = 100

_MIN = datetime.min


def _iso(value) -> str | None:
    return value.isoformat() if value else None


async def get_integrations(db: AsyncSession, tenant_id: str) -> dict:
    policy = (await db.execute(select(TenantPolicy).where(TenantPolicy.tenant_id == tenant_id))).scalar_one_or_none()
    stored = (getattr(policy, "integrations", None) if policy else None) or {}
    return {
        "retention_category": (stored.get("retention_category") or None),
        "external_events_retention_months": int(stored.get("external_events_retention_months") or 24),
        # Richieste di accesso: formazione mancante o scaduta come FAIL (revisione del privacy manager) invece di WARNING.
        "access_training_blocking": bool(stored.get("access_training_blocking", False)),
    }


def validate_integrations(values: dict) -> dict:
    unknown = set(values) - {"retention_category", "external_events_retention_months", "access_training_blocking"}
    if unknown:
        raise ValueError(f"Parametri sconosciuti: {', '.join(sorted(unknown))}")
    category = values.get("retention_category")
    if category is not None:
        category = str(category).strip()
        if len(category) > RETENTION_CATEGORY_MAX:
            raise ValueError(f"La categoria è lunga al massimo {RETENTION_CATEGORY_MAX} caratteri")
    months = values.get("external_events_retention_months")
    if months is not None and (isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 120):
        raise ValueError("La conservazione degli eventi ricevuti va da 1 a 120 mesi")
    blocking = values.get("access_training_blocking")
    if blocking is not None and not isinstance(blocking, bool):
        raise ValueError("access_training_blocking è vero o falso")
    return {"retention_category": category or None, "external_events_retention_months": months, "access_training_blocking": bool(blocking)}


def rule_days(unit: RetentionUnit, value: int | None, today: date) -> int | None:
    """Giorni di conservazione: mesi e anni sono di calendario a partire da `today`."""
    if value is None or value < 0:
        return None
    if unit == RetentionUnit.DAYS:
        return value
    if unit == RetentionUnit.MONTHS:
        return (add_months_to_date(today, value) - today).days
    if unit == RetentionUnit.YEARS:
        return (add_months_to_date(today, value * 12) - today).days
    return None


def _retention_block(rules: list[dict], category: str | None) -> dict:
    block = {"days": None, "kind": "NOT_CONFIGURED", "rule_id": None, "rules_total": len(rules), "rules": rules}
    if not category:
        return block
    wanted = category.strip().lower()
    matching = [r for r in rules if (r["category"] or "").lower() == wanted or (r["record_class"] or "").lower() == wanted]
    if not matching:
        block["kind"] = "NO_RULE"
        return block
    kinds = {(r["unit"], r["days"]) for r in matching}
    if len(kinds) > 1:
        block["kind"] = "AMBIGUOUS"
        return block
    chosen = matching[0]
    block["rule_id"] = chosen["id"] if len(matching) == 1 else None
    if chosen["unit"] in ("EVENT_BASED", "PERMANENT"):
        block["kind"] = chosen["unit"]
    elif chosen["days"] is None:
        block["kind"] = "NO_RULE"
    else:
        block["kind"], block["days"] = "FIXED", chosen["days"]
    return block


def _reference_dpia(dpias: list[Dpia]) -> Dpia | None:
    """Quella approvata più recente; altrimenti la più recente tra quelle non archiviate né «non necessarie»; altrimenti la più recente."""
    if not dpias:
        return None
    approved = [d for d in dpias if d.status == DpiaStatus.APPROVED]
    if approved:
        return max(approved, key=lambda d: (d.approved_at or _MIN, d.updated_at or _MIN))
    live = [d for d in dpias if d.status not in (DpiaStatus.ARCHIVED, DpiaStatus.NOT_REQUIRED)]
    return max(live or dpias, key=lambda d: d.updated_at or _MIN)


def etag_of_snapshot(snapshot: dict) -> str:
    """ETag sul contenuto (senza `as_of`, che cambia a ogni richiesta)."""
    body = json.dumps({k: v for k, v in snapshot.items() if k != "as_of"}, sort_keys=True, separators=(",", ":"), default=str)
    return f'"{hashlib.sha256(body.encode()).hexdigest()[:24]}"'


class SnapshotService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(
        self, tenant_id: str, activities: list[ProcessingActivity], scope: VisibilityScope, *, now: datetime | None = None
    ) -> list[dict]:
        """Snapshot dei trattamenti indicati (già filtrati per ente): interrogazioni raggruppate, non una per trattamento."""
        if not activities:
            return []
        now = now or datetime.utcnow()
        today = now.date()
        ids = [a.id for a in activities]
        category = (await get_integrations(self.db, tenant_id))["retention_category"]

        dpias: dict[str, list[Dpia]] = {}
        for d in (await self.db.execute(select(Dpia).where(
            Dpia.tenant_id == tenant_id, Dpia.processing_activity_id.in_(ids), Dpia.deleted_at.is_(None)
        ))).scalars():
            dpias.setdefault(d.processing_activity_id, []).append(d)

        notices: dict[str, list[PrivacyNotice]] = {}
        rows = (await self.db.execute(
            select(NoticeProcessingLink.processing_activity_id, PrivacyNotice)
            .join(PrivacyNotice, PrivacyNotice.id == NoticeProcessingLink.privacy_notice_id)
            .where(NoticeProcessingLink.processing_activity_id.in_(ids), PrivacyNotice.tenant_id == tenant_id,
                   PrivacyNotice.deleted_at.is_(None))
            .options(selectinload(PrivacyNotice.versions))
        )).all()
        for pid, notice in rows:
            notices.setdefault(pid, []).append(notice)

        processors: dict[str, list[tuple[ProcessingProcessor, Party, ProcessorAgreement | None]]] = {}
        links = (await self.db.execute(
            select(ProcessingProcessor, Party)
            .join(Party, Party.id == ProcessingProcessor.party_id)
            .where(ProcessingProcessor.processing_activity_id.in_(ids), Party.tenant_id == tenant_id, Party.deleted_at.is_(None))
        )).all()
        agreement_ids = {link.processor_agreement_id for link, _ in links if link.processor_agreement_id}
        agreements = {}
        if agreement_ids:
            agreements = {a.id: a for a in (await self.db.execute(
                select(ProcessorAgreement).where(ProcessorAgreement.tenant_id == tenant_id, ProcessorAgreement.id.in_(agreement_ids))
            )).scalars()}
        for link, party in links:
            processors.setdefault(link.processing_activity_id, []).append((link, party, agreements.get(link.processor_agreement_id)))

        rules: dict[str, list[RetentionRule]] = {}
        for rule in (await self.db.execute(select(RetentionRule).where(
            RetentionRule.tenant_id == tenant_id, RetentionRule.processing_activity_id.in_(ids), RetentionRule.deleted_at.is_(None)
        ))).scalars():
            rules.setdefault(rule.processing_activity_id, []).append(rule)
        category_ids = {r.data_category_id for group in rules.values() for r in group if r.data_category_id}
        category_codes = {}
        if category_ids:
            category_codes = {c.id: c.code for c in (await self.db.execute(
                select(DataCategoryCatalog).where(DataCategoryCatalog.id.in_(category_ids))
            )).scalars()}

        return [
            self._one(a, scope, now, today, category, dpias.get(a.id, []), notices.get(a.id, []), processors.get(a.id, []),
                      rules.get(a.id, []), category_codes)
            for a in activities
        ]

    def _one(self, a, scope, now, today, category, dpias, notices, processors, rules, category_codes) -> dict:
        moments = [a.updated_at, a.created_at]

        reference = _reference_dpia(dpias)
        if reference is None:
            dpia = None
        elif not scope.level_ok(reference.confidentiality_level, reference.created_by):
            dpia = {"restricted": True}
        else:
            dpia = {"id": reference.id, "code": reference.code, "version": reference.version_no, "status": reference.status.value,
                    "approved_at": _iso(reference.approved_at), "next_review_date": _iso(reference.next_review_date)}
        moments += [d.updated_at for d in dpias]

        notice_items = []
        for n in sorted(notices, key=lambda x: x.code):
            v = n.current_version
            notice_items.append({
                "id": n.id, "code": n.code, "status": n.status.value,
                "version": v.version_no if v else None, "version_label": v.version_label if v else None,
                "effective_from": _iso(v.effective_from) if v else None, "published_url": v.published_url if v else None,
            })
            moments += [n.updated_at, v.approved_at if v else None]

        processor_items = []
        for link, party, agreement in sorted(processors, key=lambda t: (t[1].legal_name.lower(), t[1].id)):
            item = {"party_id": party.id, "name": party.legal_name, "agreement": None}
            if agreement is not None:
                valid = (agreement.status == AgreementStatus.SIGNED
                         and (agreement.expiry_date is None or agreement.expiry_date >= today)
                         and (agreement.effective_from is None or agreement.effective_from <= today))
                item["agreement"] = {"id": agreement.id, "status": agreement.status.value, "valid": valid,
                                     "expiry_date": _iso(agreement.expiry_date)}
                moments.append(agreement.created_at)
            processor_items.append(item)
            moments.append(party.updated_at)

        rule_items = []
        for r in sorted(rules, key=lambda x: (x.created_at or _MIN, x.id)):
            rule_items.append({
                "id": r.id, "category": category_codes.get(r.data_category_id), "record_class": r.record_class,
                "unit": r.retention_unit.value, "value": r.retention_value, "days": rule_days(r.retention_unit, r.retention_value, today),
            })
            moments.append(r.updated_at)

        snapshot = {
            "processing_id": a.id, "code": a.code, "name": a.title, "status": a.status.value,
            "updated_at": _iso(max(m for m in moments if m)), "as_of": now.replace(microsecond=0).isoformat(),
            "dpia": dpia, "dpia_others": max(len(dpias) - 1, 0),
            "notices": notice_items, "processors": processor_items,
            "retention": _retention_block(rule_items, category),
        }
        snapshot["etag"] = etag_of_snapshot(snapshot)
        return snapshot
