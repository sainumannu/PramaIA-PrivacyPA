"""
Dati di prova per PrivacyPA — Comune di Tempio Pausania (tenant Portal slug: 001).

Idempotente: se l'ente è già presente, non duplica.
Uso:  python seed_demo_data.py
"""
from __future__ import annotations

import os
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import func, select

from backend.db.database import AsyncSessionLocal
from backend.db.init_db import init_database
from backend.db.seed_privacypa import seed_privacypa_foundations
from backend.models.privacypa_annotations import CommentVisibility, CustomFieldType, Tag
from backend.models.privacypa_approvals import Approval
from backend.models.privacypa_catalog import DataCategoryCatalog, DataSubjectCategoryCatalog, GdprDataClass
from backend.models.privacypa_identity import PrivacyRoleCode, ScopeType
from backend.models.privacypa_organization import OrganizationalUnit, OrganizationalUnitType
from backend.models.privacypa_assets import Asset, AssetLifecycle, AssetType, HostingType
from backend.models.privacypa_documents import Document, DocumentType
from backend.models.privacypa_parties import (
    AgreementStatus,
    Contract,
    ContractStatus,
    Party,
    PartyType,
    ProcessorStatus,
    Subprocessor,
    SubprocessorAuthorization,
    SubprocessorStatus,
)
from backend.models.privacypa_assessments import (
    Dpia,
    DpiaStatus,
    ScreeningOutcome,
    TiaOutcome,
    TransferMechanism,
    TransferStatus,
)
from backend.models.privacypa_compliance import (
    ActionPriority,
    AuditType,
    CorrectiveSource,
    DataBreach,
    DataSubjectRequest,
    LegalReference,
    LegalSourceType,
    PrivacyAudit,
    RequestChannel,
    RequestType,
)
from backend.models.privacypa_notices import (
    DataFlow,
    FlowChannel,
    FlowEndpointType,
    FlowFrequency,
    PrivacyNotice,
)
from backend.models.privacypa_people import AnnualReport, Authorization, AuthorizationProfile, TrainingCourse
from backend.models.privacypa_processing import ProcessingActivity, ProcessingStatus
from backend.models.privacypa_projects import Project
from backend.models.privacypa_retention import DisposalMethod, RetentionRule, RetentionUnit
from backend.models.privacypa_tasks import TaskPriority, TaskSourceType, TaskStatus
from backend.models.privacypa_webhooks import WebhookSubscription
from backend.services.privacypa_annotation_service import CommentService, CustomFieldService, TagService, load_refs
from backend.services.privacypa_approval_service import ApprovalService
from backend.services.privacypa_asset_service import AssetService
from backend.services.privacypa_catalog_service import CatalogService
from backend.services.privacypa_compliance_service import (
    AuditPlanService,
    BreachService,
    CorrectiveActionService,
    LegalReferenceService,
    RequestService,
)
from backend.services.privacypa_dataflow_service import DataFlowService
from backend.services.privacypa_document_service import DocumentService
from backend.services.privacypa_dpo_service import AnnualReportService, OpinionService
from backend.services.privacypa_identity_service import IdentityService
from backend.services.privacypa_notice_service import NoticeService
from backend.services.privacypa_organization_service import OrganizationService
from backend.services.privacypa_party_service import PartyService
from backend.services.privacypa_people_service import AuthorizationService, TrainingService
from backend.services.privacypa_processing_service import ProcessingService
from backend.models.privacypa_risk import ImplementationStatus, Risk, SecurityMeasure
from backend.services.privacypa_dpia_service import DpiaService
from backend.services.privacypa_project_service import ProjectService
from backend.services.privacypa_retention_service import RetentionService
from backend.services.privacypa_risk_service import RiskService
from backend.services.privacypa_rules_engine import RulesEngine
from backend.services.privacypa_supply_chain_service import ContractService, SubprocessorService
from backend.services.privacypa_task_service import TaskService
from backend.services.privacypa_transfer_service import TransferService
from backend.services.privacypa_webhook_service import WebhookService

TENANT = "001"
ACTOR = "seed-demo"
TODAY = date.today()

# Utenti reali del Portal (JWT `sub` = UUID)
USER_FABRIZIO = "f6b32084-38fc-4325-8cb6-f4f030130b30"
USER_IVAN = "bd46de77-7d26-40fe-847e-2d8ada9d405b"
USER_MASSIMILIANO = "5caaf604-5196-407e-aadc-6dd26ad9e329"


async def _by_code(session, model, tenant_id: str | None, code: str):
    stmt = select(model).where(model.code == code)
    if tenant_id is None:
        stmt = stmt.where(model.tenant_id.is_(None))
    else:
        stmt = stmt.where(model.tenant_id == tenant_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def _advance(svc: ProcessingService, tenant: str, activity_id: str, target: ProcessingStatus) -> None:
    order = [
        ProcessingStatus.DRAFT,
        ProcessingStatus.IN_REVIEW,
        ProcessingStatus.APPROVED,
        ProcessingStatus.ACTIVE,
    ]
    current = ProcessingStatus.DRAFT
    for step in order[1:]:
        if current == target:
            return
        await svc.transition_status(tenant, activity_id, ACTOR, step)
        current = step
        if current == target:
            return


async def seed() -> None:
    await init_database()
    await seed_privacypa_foundations()
    async with AsyncSessionLocal() as session:
        created = await seed_for_tenant(session, TENANT, ACTOR)
        await session.commit()
        print(f"Seed demo completato per tenant {TENANT}.")
        for key, value in created.items():
            if value:
                print(f"  {key}: {value}")


async def seed_for_tenant(session, tenant_id: str = TENANT, actor: str = ACTOR) -> dict:
    """Idempotente: crea l'ente se manca e riempie le sezioni ancora vuote."""
    global TENANT, ACTOR
    TENANT, ACTOR = tenant_id, actor

    org_svc = OrganizationService(session)
    existing = await org_svc.get_organization(TENANT)
    if existing:
        print(f"Ente già presente per tenant {TENANT} ({existing.name}). Completo le sezioni mancanti.")
    else:
        await seed_core(session)

    await seed_assessments(session)
    await seed_risks(session)
    created = await seed_extended(session)
    return created


async def seed_core(session) -> None:
        org_svc = OrganizationService(session)
        org = await org_svc.create_or_update_organization(
            TENANT,
            ACTOR,
            name="Comune di Tempio Pausania",
            tax_code="00253250906",
            ipa_code="c_l093",
            pec="protocollo@pec.comune.tempiopausania.ot.it",
            address={"via": "Piazza Gallura 3", "cap": "07029", "comune": "Tempio Pausania", "provincia": "SS"},
            dpo_name="Ivan Lelli",
            dpo_contact="ivan.lelli@comuneditempiopausania.it",
        )

        async def unit(code, name, utype, parent_id=None):
            return await org_svc.create_unit(
                TENANT,
                organization_id=org.id,
                actor_user_id=ACTOR,
                code=code,
                name=name,
                unit_type=utype,
                parent_id=parent_id,
            )

        area_affari = await unit("AREA-AFFARI", "Area Affari Generali", OrganizationalUnitType.AREA)
        demografici = await unit("SET-DEMOGRAFICI", "Servizi Demografici", OrganizationalUnitType.SETTORE, area_affari.id)
        segreteria = await unit("SET-SEGRETERIA", "Segreteria Generale", OrganizationalUnitType.SETTORE, area_affari.id)
        area_sociale = await unit("AREA-SOCIALE", "Area Servizi alla Persona", OrganizationalUnitType.AREA)
        sociale = await unit("SET-SOCIALE", "Servizi Sociali", OrganizationalUnitType.SETTORE, area_sociale.id)
        area_fin = await unit("AREA-FIN", "Area Economico-Finanziaria", OrganizationalUnitType.AREA)
        tributi = await unit("SET-TRIBUTI", "Servizio Tributi", OrganizationalUnitType.SETTORE, area_fin.id)
        polizia = await unit("CMD-PL", "Comando Polizia Locale", OrganizationalUnitType.COMANDO)

        identity = IdentityService(session)
        for uid, role in [
            (USER_FABRIZIO, PrivacyRoleCode.PRIVACY_MANAGER),
            (USER_IVAN, PrivacyRoleCode.DPO),
            (USER_MASSIMILIANO, PrivacyRoleCode.ICT),
        ]:
            try:
                await identity.assign_role(
                    TENANT, uid, role, granted_by=ACTOR, scope_type=ScopeType.TENANT
                )
            except ValueError:
                pass

        cittadini = await _by_code(session, DataSubjectCategoryCatalog, None, "CITTADINI")
        dipendenti = await _by_code(session, DataSubjectCategoryCatalog, None, "DIPENDENTI")
        utenti = await _by_code(session, DataSubjectCategoryCatalog, None, "UTENTI_SERVIZI")
        minori = await _by_code(session, DataSubjectCategoryCatalog, None, "MINORI")
        cf = await _by_code(session, DataCategoryCatalog, None, "CODICE_FISCALE")
        nome = await _by_code(session, DataCategoryCatalog, None, "NOME_COGNOME")
        ind = await _by_code(session, DataCategoryCatalog, None, "INDIRIZZO")
        salute = await _by_code(session, DataCategoryCatalog, None, "SALUTE")
        iban = await _by_code(session, DataCategoryCatalog, None, "IBAN")

        party_svc = PartyService(session)
        maggioli = await party_svc.create(
            TENANT, ACTOR, legal_name="Maggioli S.p.A.",
            party_type=PartyType.PROCESSOR, pec="maggioli@legalmail.it",
            email="privacy@maggioli.it", country_code="IT", vat_id="IT02066400405",
            processor_profile={"status": ProcessorStatus.ACTIVE, "privacy_contact": "dpo@maggioli.it"},
        )
        aruba = await party_svc.create(
            TENANT, ACTOR, legal_name="Aruba S.p.A.",
            party_type=PartyType.PROCESSOR, pec="aruba@pec.aruba.it",
            country_code="IT", vat_id="IT01573850516",
            processor_profile={"status": ProcessorStatus.TO_REVIEW, "next_assessment_date": TODAY + timedelta(days=20)},
        )
        ade = await party_svc.create(
            TENANT, ACTOR, legal_name="Agenzia delle Entrate",
            party_type=PartyType.RECIPIENT, country_code="IT",
        )
        asl = await party_svc.create(
            TENANT, ACTOR, legal_name="ASL Gallura",
            party_type=PartyType.PUBLIC_BODY, country_code="IT",
        )

        await party_svc.create_agreement(
            TENANT, ACTOR, maggioli.id,
            status=AgreementStatus.SIGNED,
            signed_date=date(2024, 3, 1),
            effective_from=date(2024, 3, 1),
            expiry_date=TODAY + timedelta(days=400),
            instructions_present=True, confidentiality_clause=True, security_clause=True,
            subprocessor_clause=True, rights_assistance_clause=True, audit_clause=True,
            breach_assistance_clause=True, deletion_return_clause=True,
        )
        await party_svc.create_agreement(
            TENANT, ACTOR, aruba.id,
            status=AgreementStatus.TO_REVIEW,
            expiry_date=TODAY + timedelta(days=12),
            instructions_present=True, security_clause=True,
        )

        asset_svc = AssetService(session)
        anpr = await asset_svc.create(
            TENANT, ACTOR, name="ANPR / Anagrafe",
            asset_type=AssetType.APPLICATION, supplier_id=maggioli.id, owner_unit_id=demografici.id,
            hosting_type=HostingType.SAAS, cloud_service=True, hosting_country="IT",
            encryption_in_transit=True, logging_supported=True, lifecycle_status=AssetLifecycle.ACTIVE,
        )
        protocollo = await asset_svc.create(
            TENANT, ACTOR, name="Protocollo informatico",
            asset_type=AssetType.APPLICATION, supplier_id=maggioli.id, owner_unit_id=segreteria.id,
            hosting_type=HostingType.CLOUD, cloud_service=True, hosting_country="IT",
            lifecycle_status=AssetLifecycle.ACTIVE,
        )
        gest_tributi = await asset_svc.create(
            TENANT, ACTOR, name="Gestionale tributi",
            asset_type=AssetType.APPLICATION, owner_unit_id=tributi.id,
            hosting_type=HostingType.ON_PREM, lifecycle_status=AssetLifecycle.LEGACY,
        )
        pec = await asset_svc.create(
            TENANT, ACTOR, name="Casella PEC istituzionale",
            asset_type=AssetType.CLOUD_SERVICE, supplier_id=aruba.id, owner_unit_id=segreteria.id,
            hosting_type=HostingType.SAAS, cloud_service=True, hosting_country="IT",
            lifecycle_status=AssetLifecycle.ACTIVE,
        )
        await asset_svc.create(
            TENANT, ACTOR, name="NAS documentale (senza owner)",
            asset_type=AssetType.FILE_SHARE, hosting_type=HostingType.ON_PREM,
            lifecycle_status=AssetLifecycle.ACTIVE,
        )

        proc = ProcessingService(session)

        async def trattamento(title, unit_id, status, **fields):
            act = await proc.create_activity(TENANT, ACTOR, title=title, organizational_unit_id=unit_id, **fields)
            if status != ProcessingStatus.DRAFT:
                await _advance(proc, TENANT, act.id, status)
            return await proc.get_activity(TENANT, act.id)

        t_anag = await trattamento(
            "Anagrafe della popolazione residente", demografici.id, ProcessingStatus.ACTIVE,
            purpose_summary="Tenuta dell'anagrafe della popolazione per finalità di legge.",
            purposes=["Iscrizione, variazione e cancellazione anagrafica", "Rilascio certificati"],
            legal_basis_codes=["GDPR_6_1_C", "GDPR_6_1_E"],
            data_subject_ids=[cittadini.id] if cittadini else [],
            data_category_ids=[x.id for x in (nome, cf, ind) if x],
            next_review_date=TODAY - timedelta(days=5),
            start_date=date(2020, 1, 1),
        )
        t_prot = await trattamento(
            "Protocollo informatico", segreteria.id, ProcessingStatus.ACTIVE,
            purpose_summary="Registrazione della corrispondenza in ingresso e uscita.",
            purposes=["Protocollazione atti"],
            legal_basis_codes=["GDPR_6_1_E"],
            data_subject_ids=[x.id for x in (cittadini, utenti) if x],
            data_category_ids=[x.id for x in (nome, cf, ind) if x],
            next_review_date=TODAY + timedelta(days=60),
        )
        t_soc = await trattamento(
            "Erogazione prestazioni socio-assistenziali", sociale.id, ProcessingStatus.IN_REVIEW,
            purpose_summary="Istruttoria e erogazione di interventi sociali, anche su dati particolari.",
            purposes=["Valutazione del bisogno", "Erogazione contributi"],
            legal_basis_codes=["GDPR_6_1_E", "GDPR_9_2_G"],
            data_subject_ids=[x.id for x in (cittadini, minori) if x],
            data_category_ids=[x.id for x in (nome, cf, salute) if x],
        )
        t_imu = await trattamento(
            "Gestione IMU e tributi locali", tributi.id, ProcessingStatus.DRAFT,
            purpose_summary="Accertamento e riscossione dei tributi comunali.",
            purposes=["Accertamento IMU", "Riscossione"],
            legal_basis_codes=["GDPR_6_1_C", "GDPR_6_1_E"],
            data_subject_ids=[cittadini.id] if cittadini else [],
            data_category_ids=[x.id for x in (nome, cf, iban) if x],
        )
        t_pl = await trattamento(
            "Videosorveglianza urbana", polizia.id, ProcessingStatus.APPROVED,
            purpose_summary="Tutela della sicurezza urbana mediante impianti di ripresa.",
            purposes=["Sicurezza urbana"],
            legal_basis_codes=["GDPR_6_1_E"],
            data_subject_ids=[cittadini.id] if cittadini else [],
            data_category_ids=[x.id for x in (nome,) if x],
            next_review_date=TODAY + timedelta(days=20),
        )
        t_hr = await trattamento(
            "Gestione del rapporto di lavoro", segreteria.id, ProcessingStatus.ACTIVE,
            purpose_summary="Adempimenti relativi al personale dipendente.",
            purposes=["Stipendi", "Presenze", "Adempimenti fiscali"],
            legal_basis_codes=["GDPR_6_1_B", "GDPR_6_1_C"],
            data_subject_ids=[dipendenti.id] if dipendenti else [],
            data_category_ids=[x.id for x in (nome, cf, iban) if x],
        )

        await asset_svc.set_links(t_anag.id, [{"asset_id": anpr.id, "role": "STORAGE"}])
        await asset_svc.set_links(t_prot.id, [{"asset_id": protocollo.id, "role": "PROCESSING"}, {"asset_id": pec.id, "role": "EXCHANGE"}])
        await asset_svc.set_links(t_imu.id, [{"asset_id": gest_tributi.id, "role": "PROCESSING"}])
        await party_svc.set_processors(t_anag.id, [{"party_id": maggioli.id}])
        await party_svc.set_processors(t_prot.id, [{"party_id": maggioli.id}, {"party_id": aruba.id}])
        await party_svc.set_recipients(t_imu.id, [{"party_id": ade.id, "communication_type": "COMMUNICATION"}])
        await party_svc.set_recipients(t_soc.id, [{"party_id": asl.id, "communication_type": "COMMUNICATION"}])

        tasks = TaskService(session)
        await tasks.create(
            TENANT, ACTOR, title="Revisionare trattamento Anagrafe",
            source_type=TaskSourceType.PROCESSING, source_id=t_anag.id,
            assigned_user_id=USER_FABRIZIO, assigned_unit_id=demografici.id,
            priority=TaskPriority.HIGH, status=TaskStatus.OPEN,
            due_at=datetime.utcnow() - timedelta(days=3),
        )
        await tasks.create(
            TENANT, ACTOR, title="Verificare atto art. 28 Aruba",
            source_type=TaskSourceType.PARTY, source_id=aruba.id,
            assigned_user_id=USER_MASSIMILIANO, priority=TaskPriority.MEDIUM,
            due_at=datetime.utcnow() + timedelta(days=5),
        )
        await tasks.create(
            TENANT, ACTOR, title="Completare bozza IMU",
            source_type=TaskSourceType.PROCESSING, source_id=t_imu.id,
            assigned_user_id=USER_FABRIZIO, priority=TaskPriority.MEDIUM,
            due_at=datetime.utcnow() + timedelta(days=25),
        )

        docs = DocumentService(session)
        policy = await docs.create(
            TENANT, ACTOR, title="Informativa videosorveglianza",
            document_type=DocumentType.NOTICE,
            links=[{"entity_type": "processing_activity", "entity_id": t_pl.id, "relation_type": "notice"}],
        )
        sample = Path("data/documents/_seed_notice.txt")
        sample.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "Comune di Tempio Pausania - Informativa videosorveglianza (bozza di test).\n"
            "Titolare: Comune di Tempio Pausania.\n"
        ).encode("utf-8")
        # Dati demo fittizi: scansione disabilitata solo se non già configurata.
        os.environ.setdefault("DOCUMENT_SCAN_MODE", "disabled")
        await docs.add_version(
            TENANT, policy.id, ACTOR,
            file_name="informativa-videosorveglianza.txt",
            content=content,
            effective_date=TODAY,
            expiry_date=TODAY + timedelta(days=10),
            notes="Versione di test",
        )
        await docs.create(
            TENANT, ACTOR, title="Atto art. 28 Maggioli (metadati)",
            document_type=DocumentType.AGREEMENT_ART28,
            links=[{"entity_type": "party", "entity_id": maggioli.id, "relation_type": "agreement"}],
        )

        print("  Nucleo: strutture, 6 trattamenti, sistemi, fornitori, task, documenti.")


async def seed_assessments(session) -> None:
    n = (await session.execute(select(func.count()).select_from(Dpia).where(Dpia.tenant_id == TENANT, Dpia.deleted_at.is_(None)))).scalar() or 0
    if n:
        print("DPIA già presenti, skip.")
        return

    async def proc_by_title(title: str):
        r = await session.execute(
            select(ProcessingActivity).where(
                ProcessingActivity.tenant_id == TENANT,
                ProcessingActivity.title == title,
                ProcessingActivity.deleted_at.is_(None),
            )
        )
        return r.scalar_one_or_none()

    t_pl = await proc_by_title("Videosorveglianza urbana")
    t_soc = await proc_by_title("Erogazione prestazioni socio-assistenziali")
    t_prot = await proc_by_title("Protocollo informatico")

    dpia_svc = DpiaService(session)
    if t_pl:
        d1 = await dpia_svc.create(
            TENANT, ACTOR, title="DPIA videosorveglianza urbana",
            processing_activity_id=t_pl.id,
            description="Impianti di ripresa in area pubblica per finalità di sicurezza urbana.",
            necessity_proportionality="Misura prevista da ordinanza sindacale; alternative meno invasive insufficienti nei punti a rischio.",
            risks_text="Rischio di identificazione sistematica di passanti e conservazione eccessiva delle immagini.",
            measures_text="Cartelli informativi, retention 7 giorni, accesso nominativo, no riconoscimento biometrico.",
            residual_risk_text="Rischio residuo medio, ritenuto accettabile con le misure indicate.",
            screening_outcome=ScreeningOutcome.PROCEED,
            screening_rationale="Monitoraggio sistematico in luogo pubblico, larga scala.",
            next_review_date=TODAY + timedelta(days=180),
        )
        await dpia_svc.save_screening(TENANT, d1.id, ACTOR, [
            {"criterion_code": "SYSTEMATIC_MONITORING", "answer": True},
            {"criterion_code": "LARGE_SCALE", "answer": True},
            {"criterion_code": "INNOVATIVE_TECH", "answer": False},
            {"criterion_code": "SPECIAL_DATA", "answer": False},
            {"criterion_code": "VULNERABLE", "answer": False},
            {"criterion_code": "SCORING", "answer": False},
            {"criterion_code": "AUTOMATED_DECISION", "answer": False},
            {"criterion_code": "DATASET_COMBINATION", "answer": False},
            {"criterion_code": "RIGHTS_OBSTACLE", "answer": False},
        ])
        await dpia_svc.transition(TENANT, d1.id, ACTOR, DpiaStatus.IN_PROGRESS)

    if t_soc:
        d2 = await dpia_svc.create(
            TENANT, ACTOR, title="Screening servizi sociali",
            processing_activity_id=t_soc.id,
            screening_outcome=ScreeningOutcome.PROCEED,
            screening_rationale="Dati particolari e interessati vulnerabili: DPIA opportuna.",
        )
        await dpia_svc.save_screening(TENANT, d2.id, ACTOR, [
            {"criterion_code": "SPECIAL_DATA", "answer": True},
            {"criterion_code": "VULNERABLE", "answer": True},
            {"criterion_code": "LARGE_SCALE", "answer": False},
            {"criterion_code": "SYSTEMATIC_MONITORING", "answer": False},
            {"criterion_code": "SCORING", "answer": False},
            {"criterion_code": "AUTOMATED_DECISION", "answer": False},
            {"criterion_code": "DATASET_COMBINATION", "answer": False},
            {"criterion_code": "INNOVATIVE_TECH", "answer": False},
            {"criterion_code": "RIGHTS_OBSTACLE", "answer": False},
        ])

    party_svc = PartyService(session)
    ms = (await session.execute(
        select(Party).where(Party.tenant_id == TENANT, Party.legal_name == "Microsoft Corporation")
    )).scalar_one_or_none()
    if not ms:
        ms = await party_svc.create(
            TENANT, ACTOR, legal_name="Microsoft Corporation",
            party_type=PartyType.PROCESSOR, country_code="US", website="https://microsoft.com",
            processor_profile={"status": ProcessorStatus.TO_REVIEW},
        )

    xfer = TransferService(session)
    tf = await xfer.create(
        TENANT, ACTOR,
        title="Microsoft 365 — posta e collaborazione",
        country_code="US",
        importer_party_id=ms.id,
        mechanism=TransferMechanism.SCC,
        scc_module="2",
        description="Trattamento di messaggi di posta e documenti di lavoro su cloud extra-SEE.",
        tia_required=True,
        tia_outcome=TiaOutcome.PENDING,
        tia_rationale="TIA in corso: valutare CLOUD Act e misure DPF/SCC.",
        law_access_assessment="Rischio di accesso da autorità USA da approfondire.",
        supplementary_measures="Cifratura in transito, tenant EU boundary ove disponibile, DPA Microsoft.",
        status=TransferStatus.TO_REVIEW,
        next_review_date=TODAY + timedelta(days=14),
    )
    if t_prot:
        await xfer.set_links(tf.id, [{"entity_type": "processing_activity", "entity_id": t_prot.id}])
    print("DPIA e trasferimento/TIA di prova creati.")


async def seed_risks(session) -> None:
    n = (await session.execute(
        select(func.count()).select_from(Risk).where(Risk.tenant_id == TENANT, Risk.deleted_at.is_(None))
    )).scalar() or 0
    if n:
        print("Rischi già presenti, skip.")
        return
    dpia = (await session.execute(
        select(Dpia).where(Dpia.tenant_id == TENANT, Dpia.title.like("%videosorveglianza%"), Dpia.deleted_at.is_(None))
    )).scalars().first()
    svc = RiskService(session)
    r1 = await svc.create_risk(
        TENANT, ACTOR, title="Identificazione sistematica di passanti",
        threat_category="videosorveglianza",
        description="Riprese continue in area pubblica con possibile riconoscimento.",
        affected_rights=["libertà di movimento", "riservatezza"],
    )
    r2 = await svc.create_risk(
        TENANT, ACTOR, title="Conservazione eccessiva delle immagini",
        threat_category="retention",
        description="Periodo di conservazione superiore al necessario.",
        affected_rights=["cancellazione"],
    )
    r3 = await svc.create_risk(
        TENANT, ACTOR, title="Accesso indebito al cloud extra-SEE",
        threat_category="trasferimento",
        description="Accesso da autorità di Paese terzo ai dati di posta.",
        affected_rights=["riservatezza della corrispondenza"],
    )
    if dpia:
        await svc.create_assessment(
            TENANT, ACTOR,
            entity_type="dpia", entity_id=dpia.id, risk_id=r1.id,
            likelihood=4, impact=4,
            assessment_rationale="Monitoraggio sistematico in piazza e vie centrali, larga scala di interessati non selezionati.",
            residual_likelihood=2, residual_impact=3,
            residual_rationale="Cartellonistica, retention 7 giorni e divieto di biometria riducono probabilità e impatto.",
        )
        await svc.create_assessment(
            TENANT, ACTOR,
            entity_type="dpia", entity_id=dpia.id, risk_id=r2.id,
            likelihood=3, impact=3,
            assessment_rationale="Senza policy la conservazione può superare i 7 giorni.",
            residual_likelihood=2, residual_impact=2,
            residual_rationale="Cancellazione automatica e log di accesso.",
        )
        signage = (await session.execute(select(SecurityMeasure).where(SecurityMeasure.code == "SIGNAGE"))).scalars().first()
        logm = (await session.execute(select(SecurityMeasure).where(SecurityMeasure.code == "LOG_AUDIT"))).scalars().first()
        ret = (await session.execute(select(SecurityMeasure).where(SecurityMeasure.code == "RETENTION"))).scalars().first()
        for m in (signage, logm, ret):
            if m:
                await svc.upsert_implementation(
                    TENANT, ACTOR,
                    measure_id=m.id, entity_type="dpia", entity_id=dpia.id,
                    implementation_status=ImplementationStatus.IMPLEMENTED,
                    implementation_notes="Misura prevista nella DPIA videosorveglianza (seed).",
                )
    t_prot = (await session.execute(
        select(ProcessingActivity).where(
            ProcessingActivity.tenant_id == TENANT,
            ProcessingActivity.title == "Protocollo informatico",
        )
    )).scalar_one_or_none()
    if t_prot:
        await svc.create_assessment(
            TENANT, ACTOR,
            entity_type="processing_activity", entity_id=t_prot.id, risk_id=r3.id,
            likelihood=3, impact=5,
            assessment_rationale="Posta su cloud extra-SEE: impatto alto in caso di accesso da autorità terze.",
            residual_likelihood=2, residual_impact=4,
            residual_rationale="SCC e cifratura in transito; TIA ancora da chiudere.",
        )
    print("Catalogo rischi, valutazioni e misure di prova creati.")


async def _count(session, model) -> int:
    stmt = select(func.count()).select_from(model).where(model.tenant_id == TENANT)
    if hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar() or 0


async def _unit(session, code: str):
    r = await session.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.tenant_id == TENANT, OrganizationalUnit.code == code)
    )
    return r.scalar_one_or_none()


async def _party(session, name: str):
    r = await session.execute(
        select(Party).where(Party.tenant_id == TENANT, Party.legal_name == name, Party.deleted_at.is_(None))
    )
    return r.scalar_one_or_none()


async def _proc(session, title: str):
    r = await session.execute(
        select(ProcessingActivity).where(
            ProcessingActivity.tenant_id == TENANT,
            ProcessingActivity.title == title,
            ProcessingActivity.deleted_at.is_(None),
        )
    )
    return r.scalar_one_or_none()


async def _asset(session, name: str):
    r = await session.execute(
        select(Asset).where(Asset.tenant_id == TENANT, Asset.name == name, Asset.deleted_at.is_(None))
    )
    return r.scalar_one_or_none()


async def _sample_doc(session, title: str, document_type: DocumentType, links: list[dict]) -> bool:
    existing = (
        await session.execute(
            select(Document).where(Document.tenant_id == TENANT, Document.title == title, Document.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing:
        return False
    docs = DocumentService(session)
    doc = await docs.create(TENANT, ACTOR, title=title, document_type=document_type, links=links)
    os.environ.setdefault("DOCUMENT_SCAN_MODE", "disabled")
    await docs.add_version(
        TENANT, doc.id, ACTOR,
        file_name=f"{title[:50].replace(' ', '-').lower()}.txt",
        content=f"{title}\nDocumento di esempio — Comune di Tempio Pausania.\n".encode("utf-8"),
        effective_date=TODAY,
        notes="Seed",
    )
    return True


async def seed_extended(session) -> dict:
    """Sezioni oltre al nucleo (registro, sistemi, fornitori, task, DPIA, rischi)."""
    created: dict[str, str] = {}

    async def run(name: str, coro):
        try:
            result = await coro
            if result:
                created[name] = result
                print(f"  {name}: {result}")
        except Exception as exc:
            created[name] = f"errore: {exc}"
            print(f"  {name}: errore — {exc}")

    await run("cataloghi_ente", seed_tenant_catalogs(session))
    await run("riferimenti", seed_legal_references(session))
    await run("informative", seed_notices(session))
    await run("flussi", seed_dataflows(session))
    await run("conservazione", seed_retention(session))
    await run("filiera", seed_supply(session))
    await run("approvazioni", seed_approvals(session))
    await run("progetti", seed_projects(session))
    await run("formazione", seed_training(session))
    await run("autorizzazioni", seed_authorizations(session))
    await run("violazioni", seed_breaches(session))
    await run("richieste", seed_requests(session))
    await run("audit", seed_audits(session))
    await run("azioni", seed_actions(session))
    await run("tag_campi", seed_annotations(session))
    await run("dpo", seed_dpo(session))
    await run("webhook", seed_webhooks(session))
    await run("integrazioni", seed_integrations(session))
    await run("documenti_sezioni", seed_section_documents(session))
    await run("verifiche", seed_findings(session))
    return created


async def seed_tenant_catalogs(session) -> str | None:
    cats = CatalogService(session)
    created = []
    if not await _by_code(session, DataSubjectCategoryCatalog, TENANT, "VOLONTARI"):
        await cats.create_subject(TENANT, ACTOR, code="VOLONTARI", name="Volontari e associazioni")
        created.append("VOLONTARI")
    if not await _by_code(session, DataCategoryCatalog, TENANT, "TARGA_VEICOLO"):
        await cats.create_data_category(
            TENANT, ACTOR, code="TARGA_VEICOLO", name="Targa veicolo",
            gdpr_class=GdprDataClass.ORDINARY, description="Identificativo del veicolo (seed locale).",
        )
        created.append("TARGA_VEICOLO")
    return ", ".join(created) if created else None


async def seed_legal_references(session) -> str | None:
    if await _count(session, LegalReference):
        return None
    svc = LegalReferenceService(session)
    gdpr = await svc.create(
        TENANT, ACTOR, source_type=LegalSourceType.REGULATION,
        title="Regolamento (UE) 2016/679", number="2016/679", year=2016, article="art. 6",
        url="https://eur-lex.europa.eu/eli/reg/2016/679/oj?locale=IT",
    )
    tuir = await svc.create(
        TENANT, ACTOR, source_type=LegalSourceType.LEGISLATIVE_DECREE,
        title="Codice della privacy", number="196", year=2003, article="art. 2-ter",
        url="https://www.normattiva.it/eli/id/2003/07/29/003G0218/",
    )
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    if t_anag:
        await svc.set_links(t_anag.id, [gdpr.id, tuir.id])
    return "2 riferimenti normativi"


async def seed_notices(session) -> str | None:
    if await _count(session, PrivacyNotice):
        return None
    polizia = await _unit(session, "CMD-PL")
    demografici = await _unit(session, "SET-DEMOGRAFICI")
    if not polizia or not demografici:
        return "strutture mancanti"
    svc = NoticeService(session)
    t_pl = await _proc(session, "Videosorveglianza urbana")
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    n1 = await svc.create(
        TENANT, ACTOR, title="Informativa videosorveglianza urbana",
        owner_unit_id=polizia.id, audience="Cittadini e passanti",
        version_label="1.0",
        content="Il Comune di Tempio Pausania tratta immagini di videosorveglianza per la sicurezza urbana.",
        effective_from=TODAY,
    )
    if t_pl:
        await svc.set_links(TENANT, n1.id, ACTOR, [t_pl.id])
    n2 = await svc.create(
        TENANT, ACTOR, title="Informativa servizi demografici",
        owner_unit_id=demografici.id, audience="Cittadini",
        version_label="1.0",
        content="Informativa per i servizi anagrafici e di stato civile.",
        effective_from=TODAY,
    )
    if t_anag:
        await svc.set_links(TENANT, n2.id, ACTOR, [t_anag.id])
    return "2 informative"


async def seed_dataflows(session) -> str | None:
    if await _count(session, DataFlow):
        return None
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    anpr = await _asset(session, "ANPR / Anagrafe")
    maggioli = await _party(session, "Maggioli S.p.A.")
    cf = await _by_code(session, DataCategoryCatalog, None, "CODICE_FISCALE")
    nome = await _by_code(session, DataCategoryCatalog, None, "NOME_COGNOME")
    if not t_anag or not anpr:
        return "trattamento o sistema mancanti"
    cats = [x.id for x in (nome, cf) if x]
    svc = DataFlowService(session)
    await svc.create(
        TENANT, ACTOR, t_anag.id, cats,
        source_type=FlowEndpointType.DATA_SUBJECT, source_label="Cittadini all'ufficio anagrafe",
        target_type=FlowEndpointType.ASSET, target_id=anpr.id,
        frequency=FlowFrequency.ON_DEMAND, channel=FlowChannel.PORTAL,
        description="Raccolta anagrafica allo sportello e registrazione su ANPR.",
        security_notes="Accesso nominativo, tracciamento delle interrogazioni.",
    )
    if maggioli:
        await svc.create(
            TENANT, ACTOR, t_anag.id, cats,
            source_type=FlowEndpointType.ASSET, source_id=anpr.id,
            target_type=FlowEndpointType.PARTY, target_id=maggioli.id,
            frequency=FlowFrequency.DAILY, channel=FlowChannel.API,
            description="Sincronizzazione quotidiana con il fornitore del gestionale anagrafico.",
        )
    return "2 flussi di dati"


async def seed_retention(session) -> str | None:
    if await _count(session, RetentionRule):
        return None
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    t_pl = await _proc(session, "Videosorveglianza urbana")
    if not t_anag:
        return "trattamento mancante"
    svc = RetentionService(session)
    await svc.create(
        TENANT, ACTOR, t_anag.id,
        retention_unit=RetentionUnit.PERMANENT, retention_criterion="Tenuta permanente dell'anagrafe per legge.",
        disposal_method=DisposalMethod.ARCHIVAL, legal_reference="d.P.R. 223/1989",
        record_class="schede anagrafiche",
    )
    if t_pl:
        await svc.create(
            TENANT, ACTOR, t_pl.id,
            retention_unit=RetentionUnit.DAYS, retention_value=7,
            retention_criterion="Immagini conservate 7 giorni salvo sequestri.",
            start_trigger="ripresa dell'immagine",
            disposal_method=DisposalMethod.DELETION, legal_reference="provvedimento Garante videosorveglianza",
        )
    return "2 regole di conservazione"


async def seed_supply(session) -> str | None:
    if await _count(session, Contract) and await _count(session, Subprocessor):
        return None
    maggioli = await _party(session, "Maggioli S.p.A.")
    aruba = await _party(session, "Aruba S.p.A.")
    ms = await _party(session, "Microsoft Corporation")
    segreteria = await _unit(session, "SET-SEGRETERIA")
    if not maggioli:
        return "fornitori mancanti"
    created = []
    if not await _count(session, Contract):
        contracts = ContractService(session)
        await contracts.create(
            TENANT, ACTOR, maggioli.id, "Contratto gestionale anagrafe e protocollo",
            status=ContractStatus.ACTIVE, start_date=date(2024, 3, 1),
            end_date=TODAY + timedelta(days=400),
            organizational_unit_id=segreteria.id if segreteria else None,
        )
        created.append("contratto Maggioli")
    if aruba and ms and not await _count(session, Subprocessor):
        subs = SubprocessorService(session)
        await subs.create(
            TENANT, ACTOR, aruba.id, ms.id,
            service_description="Infrastruttura cloud per posta e collaborazione (Microsoft 365).",
            authorization_type=SubprocessorAuthorization.SPECIFIC,
            authorization_date=date(2024, 6, 1),
            valid_from=date(2024, 6, 1),
            countries=["US", "NL"],
            status=SubprocessorStatus.ACTIVE,
        )
        created.append("sub-responsabile Microsoft")
    return ", ".join(created) if created else None


async def seed_approvals(session) -> str | None:
    if await _count(session, Approval):
        return None
    t_soc = await _proc(session, "Erogazione prestazioni socio-assistenziali")
    if not t_soc or t_soc.status != ProcessingStatus.IN_REVIEW:
        return "nessun trattamento in revisione"
    svc = ApprovalService(session)
    info = await svc.load_entity(TENANT, "processing_activity", t_soc.id)
    if info is None:
        return None
    await svc.request_review(TENANT, info, ACTOR, notes="Seed: richiesta di approvazione del trattamento servizi sociali.")
    return "1 richiesta di approvazione"


async def seed_projects(session) -> str | None:
    if await _count(session, Project):
        return None
    sociale = await _unit(session, "SET-SOCIALE")
    maggioli = await _party(session, "Maggioli S.p.A.")
    if not sociale:
        return "struttura mancante"
    svc = ProjectService(session)
    await svc.create(
        TENANT, ACTOR,
        title="Piattaforma digitale servizi sociali",
        description="Nuovo gestionale per l'istruttoria degli interventi socio-assistenziali, con dati particolari.",
        proposing_unit_id=sociale.id, project_owner_id=USER_FABRIZIO,
        supplier_id=maggioli.id if maggioli else None,
        uses_cloud=True, special_data=True, minors=True,
    )
    await svc.create(
        TENANT, ACTOR,
        title="App notifiche tributi",
        description="App per avvisi di pagamento IMU, senza dati particolari.",
        proposing_unit_id=sociale.id, project_owner_id=USER_MASSIMILIANO,
        uses_cloud=True,
    )
    return "2 progetti"


async def seed_training(session) -> str | None:
    if await _count(session, TrainingCourse):
        return None
    svc = TrainingService(session)
    course = await svc.create_course(
        TENANT, ACTOR, title="GDPR per referenti privacy", version="2026.1",
        description="Corso obbligatorio sui principi del trattamento e sul registro.",
        mandatory=True, target_roles=[PrivacyRoleCode.PRIVACY_MANAGER.value, PrivacyRoleCode.DPO.value],
        validity_months=24,
    )
    await svc.add_session(TENANT, course.id, ACTOR, TODAY + timedelta(days=14), trainer="Ivan Lelli", location="Sala consiliare")
    await svc.assign(
        TENANT, ACTOR, course.id,
        [USER_FABRIZIO, USER_IVAN, USER_MASSIMILIANO],
        TODAY + timedelta(days=60),
    )
    return "1 corso, 1 sessione, 3 assegnazioni"


async def seed_authorizations(session) -> str | None:
    if await _count(session, AuthorizationProfile):
        return None
    svc = AuthorizationService(session)
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    anpr = await _asset(session, "ANPR / Anagrafe")
    # Profilo di accesso completo: lo usa anche l'HelpDesk nelle richieste di accesso.
    profile = await svc.create_profile(
        TENANT, ACTOR, name="Operatore anagrafe", code="OPERATORE_ANAGRAFE",
        description="Consultazione e aggiornamento delle schede anagrafiche.",
        asset_id=anpr.id if anpr else None, allowed_operations=["READ", "UPDATE"],
        allowed_scope_types=["ORGANIZATIONAL_UNIT", "ASSIGNED_CASES"],
        processing_activity_ids=[t_anag.id] if t_anag else [], max_duration_days=365,
    )
    if t_anag:
        await svc.create(
            TENANT, ACTOR, user_id=USER_FABRIZIO, authorization_profile_id=profile.id,
            processing_activity_id=t_anag.id, asset_id=anpr.id if anpr else None,
            valid_from=TODAY, valid_to=TODAY + timedelta(days=365),
        )
    return "1 profilo e 1 autorizzazione"


async def seed_breaches(session) -> str | None:
    if await _count(session, DataBreach):
        return None
    demografici = await _unit(session, "SET-DEMOGRAFICI")
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    if not demografici:
        return "struttura mancante"
    svc = BreachService(session)
    breach = await svc.create(
        TENANT, ACTOR, datetime.utcnow() - timedelta(hours=6),
        title="Invio PEC a destinatario errato",
        organizational_unit_id=demografici.id,
        description="Una certificazione anagrafica è stata inviata a un indirizzo PEC di altro cittadino.",
        confidentiality_affected=True, approx_data_subjects=1, approx_records=1,
        reported_by="Ufficio anagrafe",
    )
    if t_anag:
        await svc.set_links(TENANT, breach.id, ACTOR, [{"entity_type": "processing_activity", "entity_id": t_anag.id}])
    return "1 violazione"


async def seed_requests(session) -> str | None:
    if await _count(session, DataSubjectRequest):
        return None
    demografici = await _unit(session, "SET-DEMOGRAFICI")
    if not demografici:
        return "struttura mancante"
    svc = RequestService(session)
    await svc.create(
        TENANT, ACTOR, datetime.utcnow() - timedelta(days=2),
        organizational_unit_id=demografici.id, request_type=RequestType.ACCESS,
        channel=RequestChannel.PEC, identity_verified=True,
        requester_reference="PROT-2026-4412",
        identity_verification_notes="Identità verificata tramite CIE all'ufficio.",
    )
    await svc.create(
        TENANT, ACTOR, datetime.utcnow() - timedelta(hours=12),
        organizational_unit_id=demografici.id, request_type=RequestType.ERASURE,
        channel=RequestChannel.WEB_FORM, identity_verified=False,
        requester_reference="WEB-2026-019",
    )
    return "2 richieste degli interessati"


async def seed_audits(session) -> str | None:
    if await _count(session, PrivacyAudit):
        return None
    demografici = await _unit(session, "SET-DEMOGRAFICI")
    svc = AuditPlanService(session)
    audit = await svc.create(
        TENANT, ACTOR, title="Audit interno servizi demografici 2026",
        audit_type=AuditType.INTERNAL, organizational_unit_id=demografici.id if demografici else None,
        planned_date=TODAY + timedelta(days=30), lead_auditor_user_id=USER_IVAN,
    )
    await svc.add_finding(
        TENANT, audit.id, ACTOR, severity="MEDIUM",
        title="Manca evidenza di formazione recente",
        description="Gli operatori dell'anagrafe non hanno attestati di formazione privacy degli ultimi 24 mesi.",
        evidence="Campione di fascicoli del personale.",
    )
    return "1 audit con 1 rilievo"


async def seed_actions(session) -> str | None:
    from backend.models.privacypa_compliance import CorrectiveAction

    if await _count(session, CorrectiveAction):
        return None
    demografici = await _unit(session, "SET-DEMOGRAFICI")
    dpia = (await session.execute(
        select(Dpia).where(Dpia.tenant_id == TENANT, Dpia.deleted_at.is_(None))
    )).scalars().first()
    svc = CorrectiveActionService(session)
    kwargs = dict(
        title="Integrare cartellonistica videosorveglianza",
        description="Verificare e completare i cartelli informativi in tutti i punti di ripresa.",
        priority=ActionPriority.HIGH, owner_user_id=USER_MASSIMILIANO,
        due_date=TODAY + timedelta(days=21),
        organizational_unit_id=demografici.id if demografici else None,
    )
    if dpia:
        await svc.create(TENANT, ACTOR, source_type=CorrectiveSource.DPIA, source_id=dpia.id, **kwargs)
    else:
        await svc.create(TENANT, ACTOR, source_type=CorrectiveSource.MANUAL, **kwargs)
    await svc.create(
        TENANT, ACTOR, source_type=CorrectiveSource.MANUAL,
        title="Aggiornare atto art. 28 Aruba",
        description="Chiudere le clausole mancanti e firmare l'atto in scadenza.",
        priority=ActionPriority.MEDIUM, owner_user_id=USER_FABRIZIO,
        due_date=TODAY + timedelta(days=10),
        organizational_unit_id=demografici.id if demografici else None,
    )
    return "2 azioni correttive"


async def seed_annotations(session) -> str | None:
    bits = []
    if not await _count(session, Tag):
        tags = TagService(session)
        t_high = await tags.create(TENANT, ACTOR, "alta priorità", color="#dc2626", description="Da trattare con urgenza")
        t_pa = await tags.create(TENANT, ACTOR, "PA locale", color="#2563eb")
        t_pl = await _proc(session, "Videosorveglianza urbana")
        if t_pl:
            refs = await load_refs(session, TENANT, "processing_activity", [t_pl.id])
            if refs:
                await tags.set_for_entity(TENANT, refs[0], ACTOR, [t_high.id, t_pa.id])
                comments = CommentService(session)
                await comments.create(
                    TENANT, refs[0], ACTOR,
                    "Seed: verificare i cartelli e la retention a 7 giorni prima del prossimo riesame.",
                    CommentVisibility.TEAM,
                )
        bits.append("tag e commento")
    fields = CustomFieldService(session)
    existing = await fields.definitions(TENANT, "processing_activity")
    if not any(d.key == "referente_ufficio" for d in existing):
        await fields.create_definition(
            TENANT, ACTOR, "processing_activity", "referente_ufficio", "Referente di ufficio",
            CustomFieldType.TEXT,
        )
        bits.append("campo custom")
    return ", ".join(bits) if bits else None


async def seed_dpo(session) -> str | None:
    from backend.models.privacypa_people import DpoOpinion

    bits = []
    if not await _count(session, DpoOpinion):
        t_pl = await _proc(session, "Videosorveglianza urbana")
        if t_pl:
            op = OpinionService(session)
            opinion = await op.request(
                TENANT, ACTOR, "processing_activity", t_pl.id,
                "Seed: parere sul trattamento di videosorveglianza.", dpo_user_id=USER_IVAN,
            )
            await op.issue(
                TENANT, opinion.id, USER_IVAN,
                "Parere favorevole con raccomandazione di mantenere la retention a 7 giorni e senza biometria.",
            )
            bits.append("parere DPO")
    if not await _count(session, AnnualReport):
        year = date.today().year
        reports = AnnualReportService(session)
        await reports.save_comments(
            TENANT, year, ACTOR,
            {"conclusions": "Bozza di relazione annuale (seed): quadro dei trattamenti in evoluzione.",
             "dpo_activity": "Parere emesso sulla videosorveglianza urbana."},
            version=0,
        )
        bits.append(f"relazione {year}")
    return ", ".join(bits) if bits else None


# ---------------------------------------------------------------- connettore VideoPA (W1-W3)

VIDEO_CLASS = "Immagini di videosorveglianza"
SERVICE_ACCOUNT = "svc-videopa"


async def seed_integrations(session) -> str | None:
    """
    Dati di prova per il connettore VideoPA: l'account di servizio (ruolo INTEGRATION), la categoria delle riprese, cinque trattamenti di
    videosorveglianza con situazioni diverse per lo snapshot (con e senza DPIA, informativa, responsabile; conservazione a giorni, a mesi,
    a evento, assente, in conflitto) e alcuni eventi tecnici già ricevuti. Idempotente.
    """
    from backend.auth.privacypa_scope import VisibilityScope
    from backend.services.privacypa_external_event_service import ExternalEventService
    from backend.services.privacypa_policy_service import PolicyService
    from backend.models.privacypa_approvals import TenantPolicy
    from backend.models.privacypa_parties import ProcessorAgreement

    polizia = await _unit(session, "CMD-PL")
    if not polizia:
        return "struttura del comando mancante"
    done = []

    # 1. account di servizio
    identity = IdentityService(session)
    if not any(a.role_code == PrivacyRoleCode.INTEGRATION for a in await identity.list_user_assignments(TENANT, SERVICE_ACCOUNT, active_only=True)):
        await identity.assign_role(TENANT, SERVICE_ACCOUNT, PrivacyRoleCode.INTEGRATION, ACTOR, ScopeType.TENANT)
        done.append(f"account {SERVICE_ACCOUNT} (INTEGRATION)")

    # 2. categoria delle riprese
    policy = (await session.execute(select(TenantPolicy).where(TenantPolicy.tenant_id == TENANT))).scalar_one_or_none()
    stored = (policy.integrations or {}) if policy else {}
    if stored.get("retention_category") != VIDEO_CLASS:
        await PolicyService(session).update_integrations(
            TENANT, ACTOR, {**stored, "retention_category": VIDEO_CLASS}, policy.row_version if policy else 0)
        done.append("categoria delle riprese")

    # 3. la regola di conservazione già presente sulla videosorveglianza urbana entra nella categoria
    urbana = await _proc(session, "Videosorveglianza urbana")
    if urbana:
        for rule in (await session.execute(select(RetentionRule).where(
            RetentionRule.tenant_id == TENANT, RetentionRule.processing_activity_id == urbana.id, RetentionRule.deleted_at.is_(None)
        ))).scalars():
            if not rule.record_class:
                rule.record_class = VIDEO_CLASS
        # un responsabile con atto art. 28 firmato (snapshot con `processors`)
        aruba = await _party(session, "Aruba S.p.A.")
        if aruba and not await PartyService(session).list_processors(urbana.id):
            agreement = ProcessorAgreement(tenant_id=TENANT, party_id=aruba.id, status=AgreementStatus.SIGNED,
                                           signed_date=TODAY - timedelta(days=200), effective_from=TODAY - timedelta(days=200),
                                           expiry_date=TODAY + timedelta(days=500))
            session.add(agreement)
            await session.flush()
            await PartyService(session).set_processors(urbana.id, [{"party_id": aruba.id, "processor_agreement_id": agreement.id}])
            done.append("responsabile con atto art. 28 sulla videosorveglianza urbana")

    # 4. trattamenti con situazioni diverse
    cittadini = await _by_code(session, DataSubjectCategoryCatalog, None, "CITTADINI")
    nome = await _by_code(session, DataCategoryCatalog, None, "NOME_COGNOME")
    proc = ProcessingService(session)
    retention = RetentionService(session)
    scenarios = [
        # titolo, stato, [(unità, valore, classe)] -> regole
        ("Videosorveglianza edifici comunali", ProcessingStatus.APPROVED, [(RetentionUnit.MONTHS, 1, VIDEO_CLASS)]),
        ("Lettura targhe varchi ZTL", ProcessingStatus.APPROVED, [(RetentionUnit.EVENT_BASED, None, VIDEO_CLASS)]),
        ("Videosorveglianza scuole", ProcessingStatus.APPROVED, [(RetentionUnit.DAYS, 7, VIDEO_CLASS), (RetentionUnit.DAYS, 30, VIDEO_CLASS)]),
        ("Fototrappole ambientali", ProcessingStatus.DRAFT, []),
    ]
    for title, status_, rules in scenarios:
        if await _proc(session, title):
            continue
        act = await proc.create_activity(
            TENANT, ACTOR, title=title, organizational_unit_id=polizia.id,
            purpose_summary="Trattamento di prova per il connettore VideoPA.", purposes=["Sicurezza urbana"],
            legal_basis_codes=["GDPR_6_1_E"], data_subject_ids=[cittadini.id] if cittadini else [],
            data_category_ids=[x.id for x in (nome,) if x],
        )
        if status_ != ProcessingStatus.DRAFT:
            await _advance(proc, TENANT, act.id, status_)
        for unit, value, klass in rules:
            await retention.create(
                TENANT, ACTOR, act.id, retention_unit=unit, retention_value=value, record_class=klass,
                retention_criterion="Conservazione delle riprese (dato di prova).", disposal_method=DisposalMethod.DELETION,
                start_trigger="ripresa dell'immagine",
            )
        done.append(f"trattamento «{title}»")

    # 5. eventi tecnici già ricevuti (id fissi: rilanciando il seed non si duplicano)
    events = []
    for n, (title, kind) in enumerate([("Videosorveglianza urbana", "AudioEnabled"), ("Videosorveglianza urbana", "CameraMoved"),
                                       ("Videosorveglianza urbana", "CameraMoved"), ("Videosorveglianza scuole", "AnalyticsEnabled")]):
        target = await _proc(session, title)
        if target:
            events.append({"source": "VIDEOPA", "event_id": f"demo-evt-{n}", "type": kind, "processing_id": target.id,
                           "occurred_at": (datetime.utcnow() - timedelta(hours=n + 1)).isoformat(), "subject_ref": f"CAM-{n + 1:04d}",
                           "summary": f"Evento di prova {kind}", "link": f"https://videopa.example/cameras/CAM-{n + 1:04d}"})
    events.append({"source": "VIDEOPA", "event_id": "demo-evt-unmatched", "type": "CameraInstalled", "processing_id": "non-esiste",
                   "occurred_at": datetime.utcnow().isoformat(), "subject_ref": "CAM-0099"})
    results = await ExternalEventService(session).ingest(TENANT, SERVICE_ACCOUNT, VisibilityScope(user_id=SERVICE_ACCOUNT, unit_ids=None), events)
    accepted = sum(1 for r in results if r["outcome"] in ("ACCEPTED", "UNMATCHED"))
    if accepted:
        done.append(f"{accepted} eventi ricevuti da VideoPA")
    return ", ".join(done) or None


async def seed_webhooks(session) -> str | None:
    if await _count(session, WebhookSubscription):
        return None
    try:
        svc = WebhookService(session)
        await svc.create(
            TENANT, ACTOR, "Notifiche di esempio", "https://example.com/webhooks/privacypa",
            ["processing.updated", "breach.created", "finding.opened"], active=False,
        )
        return "1 sottoscrizione (disattiva)"
    except Exception as exc:
        return f"non creato ({exc})"


async def seed_section_documents(session) -> str | None:
    created = 0
    t_anag = await _proc(session, "Anagrafe della popolazione residente")
    maggioli = await _party(session, "Maggioli S.p.A.")
    dpia = (await session.execute(
        select(Dpia).where(Dpia.tenant_id == TENANT, Dpia.deleted_at.is_(None))
    )).scalars().first()
    project = (await session.execute(
        select(Project).where(Project.tenant_id == TENANT, Project.deleted_at.is_(None))
    )).scalars().first()
    if t_anag and await _sample_doc(
        session, "Estratto registro anagrafe (seed)", DocumentType.REGISTER_EXTRACT,
        [{"entity_type": "processing_activity", "entity_id": t_anag.id, "relation_type": "evidence"}],
    ):
        created += 1
    if maggioli and await _sample_doc(
        session, "DPA Maggioli (seed)", DocumentType.AGREEMENT_ART28,
        [{"entity_type": "party", "entity_id": maggioli.id, "relation_type": "agreement"}],
    ):
        created += 1
    if dpia and await _sample_doc(
        session, "Relazione DPIA videosorveglianza (seed)", DocumentType.DPIA,
        [{"entity_type": "dpia", "entity_id": dpia.id, "relation_type": "evidence"}],
    ):
        created += 1
    if project and await _sample_doc(
        session, "Scheda privacy by design (seed)", DocumentType.POLICY,
        [{"entity_type": "project", "entity_id": project.id, "relation_type": "evidence"}],
    ):
        created += 1
    return f"{created} documenti collegati" if created else None


async def seed_findings(session) -> str | None:
    stats = await RulesEngine(session).evaluate(TENANT)
    return f"regole {stats.evaluated_rules}, aperti {stats.opened}, aggiornati {stats.updated}"


if __name__ == "__main__":
    asyncio.run(seed())
