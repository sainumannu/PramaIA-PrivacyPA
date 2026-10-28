"""
PrivacyPA — API per le richieste di accesso dall'HelpDesk (`/api/privacypa/access-governance`).

Catalogo di sistemi e profili, valutazione sincrona e invio dei casi non standard al privacy manager. Le chiama
l'account di servizio dell'HelpDesk (ruolo `INTEGRATION_HELPDESK`) con `X-On-Behalf-Of` = operatore; il registro
degli autorizzati resta su `/api/privacypa/authorizations`. Contratto: `INTEGRAZIONE_HELPDESK.md`.
"""
from datetime import date
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.portal_jwt import TokenPayload, get_current_user
from backend.auth.privacypa_authz import record_on_behalf_of, require_tenant
from backend.auth.privacypa_license import ACCESS_GOVERNANCE, require_licensed_module
from backend.auth.privacypa_permissions import require_module_view, require_permission
from backend.auth.privacypa_scope import build_scope
from backend.db.database import get_db
from backend.models.privacypa_people import ACCESS_OPERATIONS
from backend.services.privacypa_access_service import AccessGovernanceService, Request
from backend.services.privacypa_approval_service import ApprovalError, http_error
from backend.services.privacypa_compliance_service import ComplianceError
from backend.services.privacypa_people_service import normalize_choices, validate_scope

router = APIRouter(
    prefix="/api/privacypa/access-governance",
    tags=["PrivacyPA - Richieste di accesso (HelpDesk)"],
    dependencies=[
        Depends(require_licensed_module(ACCESS_GOVERNANCE)),
        Depends(require_module_view("access")),
        Depends(record_on_behalf_of),
    ],
)


class ScopeIn(BaseModel):
    type: str = Field(..., max_length=30)
    value: str | None = Field(None, max_length=2000)


class DurationIn(BaseModel):
    type: Literal["TEMPORARY", "PERMANENT"] = "PERMANENT"
    valid_from: date | None = None
    valid_to: date | None = None


class SourceIn(BaseModel):
    module: str = Field(..., min_length=1, max_length=30)
    ref: str = Field(..., min_length=1, max_length=100)
    request_version: int = Field(..., ge=1)
    url: str | None = Field(None, max_length=500)

    @field_validator("url")
    @classmethod
    def _web_address(cls, value: str | None) -> str | None:
        # Diventa un collegamento nella pagina: solo indirizzi web, mai javascript: o data:.
        if value is None or not value.strip():
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("source.url deve essere un indirizzo http o https")
        return value.strip()


class AssessmentIn(BaseModel):
    request_type: Literal["GRANT", "CHANGE", "REVOKE"]
    subject_user_id: str | None = Field(None, max_length=100)
    asset_id: str | None = Field(None, max_length=36)
    profile_id: str | None = Field(None, max_length=36)
    operations: list[str] = Field(default_factory=list, max_length=len(ACCESS_OPERATIONS))
    scope: ScopeIn | None = None
    duration: DurationIn = Field(default_factory=DurationIn)
    current_authorization_id: str | None = Field(None, max_length=36)
    source: SourceIn


class ReviewIn(BaseModel):
    notes: str | None = Field(None, max_length=4000)


def _uid(user: TokenPayload) -> str:
    return user.sub or user.email


def _request(body: AssessmentIn) -> Request:
    """Richiesta normalizzata; 400 se il contenuto non è valutabile (non un esito)."""
    try:
        operations = normalize_choices(body.operations, ACCESS_OPERATIONS, "Operazioni") or []
        scope = validate_scope(body.scope.model_dump()) if body.scope else None
    except ComplianceError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if body.request_type != "REVOKE" and (not operations or scope is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Operazioni e ambito sono obbligatori per concessioni e modifiche")
    temporary = body.duration.type == "TEMPORARY"
    valid_from = body.duration.valid_from or (date.today() if temporary else None)
    if temporary and body.duration.valid_to is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un accesso temporaneo richiede la data di fine")
    if temporary and body.duration.valid_to < valid_from:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La fine dell'accesso precede l'inizio")
    return Request(
        request_type=body.request_type, subject_user_id=(body.subject_user_id or "").strip() or None, asset_id=body.asset_id,
        profile_id=body.profile_id, operations=operations, scope=scope, temporary=temporary, valid_from=valid_from,
        valid_to=body.duration.valid_to if temporary else None, current_authorization_id=body.current_authorization_id,
        source_module=body.source.module.strip().upper(), source_ref=body.source.ref.strip(), request_version=body.source.request_version,
        source_url=body.source.url,
    )


async def _load(svc: AccessGovernanceService, db: AsyncSession, tenant_id: str, user: TokenPayload, assessment_id: str):
    assessment = await svc.get(tenant_id, assessment_id)
    if assessment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Valutazione non trovata")
    (await build_scope(db, tenant_id, user)).assert_unit(assessment.organizational_unit_id)
    return assessment


@router.get("/assets")
async def list_assets(
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Sistemi selezionabili nel modulo dell'HelpDesk: solo quelli con almeno un profilo attivo."""
    return await AccessGovernanceService(db).catalog_assets(tenant_id)


@router.get("/profiles")
async def list_profiles(
    asset_id: str | None = None,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await AccessGovernanceService(db).catalog_profiles(tenant_id, asset_id)


@router.post("/assessments", status_code=status.HTTP_201_CREATED)
async def create_assessment(
    body: AssessmentIn, response: Response,
    tenant_id: str = Depends(require_tenant), on_behalf_of: str | None = Depends(record_on_behalf_of),
    user: TokenPayload = Depends(require_permission("access.create")), db: AsyncSession = Depends(get_db),
):
    """Valutazione sincrona. Stessa sorgente e versione della richiesta: restituisce quella già fatta (200)."""
    svc = AccessGovernanceService(db)
    assessment, created = await svc.assess(tenant_id, _uid(user), on_behalf_of, _request(body))
    if not created:
        response.status_code = status.HTTP_200_OK
    return await svc.describe(tenant_id, assessment)


@router.get("/assessments/{assessment_id}")
async def get_assessment(
    assessment_id: str,
    tenant_id: str = Depends(require_tenant), user: TokenPayload = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Rilettura della valutazione, con lo stato dell'eventuale revisione (decisione e note del privacy manager)."""
    svc = AccessGovernanceService(db)
    return await svc.describe(tenant_id, await _load(svc, db, tenant_id, user, assessment_id))


@router.post("/assessments/{assessment_id}/review", status_code=status.HTTP_201_CREATED)
async def request_review(
    assessment_id: str, body: ReviewIn,
    tenant_id: str = Depends(require_tenant), on_behalf_of: str | None = Depends(record_on_behalf_of),
    user: TokenPayload = Depends(require_permission("access.create")), db: AsyncSession = Depends(get_db),
):
    """Invio al privacy manager di un caso non standard: crea la richiesta nella coda approvazioni."""
    svc = AccessGovernanceService(db)
    assessment = await _load(svc, db, tenant_id, user, assessment_id)
    try:
        await svc.request_review(tenant_id, assessment, _uid(user), on_behalf_of, body.notes)
    except ApprovalError as e:
        raise http_error(e)
    return await svc.describe(tenant_id, assessment)
