"""
Seed dei dati di riferimento PrivacyPA: catalogo ruoli privacy (§9.2) e
basi giuridiche GDPR (§13). Sono cataloghi globali/statici, non legati
a un tenant: vengono creati una sola volta all'avvio, idempotenti.

Le categorie interessati/dati (§14, §15) vengono seedate come catalogo
globale di riferimento (tenant_id NULL), in forma compatta, così il
registro dei trattamenti è usabile senza un import preliminare.
"""
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models.privacypa_identity import PrivacyRoleCatalog, PrivacyRoleCode
from backend.models.privacypa_catalog import (
    DataCategoryCatalog,
    DataSubjectCategoryCatalog,
    GdprDataClass,
    LegalBasisCatalog,
)
from backend.models.privacypa_risk import MeasureCategory, SecurityMeasure
from backend.utils.logging import get_logger

logger = get_logger(__name__)


_ROLE_CATALOG = [
    (PrivacyRoleCode.SUPER_ADMIN, "Super Admin", "Accesso totale a tutti i tenant e app (Platform Admin)."),
    (PrivacyRoleCode.TENANT_ADMIN, "Tenant Admin", "Gestione utenti e impostazioni del proprio ente."),
    (PrivacyRoleCode.PRIVACY_MANAGER, "Privacy Manager", "Responsabile della gestione privacy dell'intero ente."),
    (PrivacyRoleCode.DPO, "DPO / RPD", "Responsabile della protezione dei dati."),
    (PrivacyRoleCode.MANAGER, "Dirigente/Responsabile", "Responsabile di una struttura organizzativa."),
    (PrivacyRoleCode.PRIVACY_REFERENT, "Referente privacy", "Referente privacy di una o più strutture."),
    (PrivacyRoleCode.ICT, "ICT", "Referente sistemi informativi e asset tecnologici."),
    (PrivacyRoleCode.RTD, "RTD", "Responsabile della transizione digitale."),
    (PrivacyRoleCode.OPERATOR, "Operatore", "Operatore con permessi operativi di base."),
    (PrivacyRoleCode.AUDITOR, "Auditor", "Accesso di sola verifica per finalità di audit."),
    (PrivacyRoleCode.READ_ONLY, "Sola lettura", "Accesso in sola lettura."),
    (PrivacyRoleCode.INTEGRATION, "Integrazione", "Account di servizio di un sistema esterno: legge lo snapshot dei trattamenti e invia eventi."),
]

_LEGAL_BASIS_CATALOG = [
    ("GDPR_6_1_A", "6", "1", "a", "Consenso dell'interessato"),
    ("GDPR_6_1_B", "6", "1", "b", "Esecuzione di un contratto di cui l'interessato è parte"),
    ("GDPR_6_1_C", "6", "1", "c", "Adempimento di un obbligo legale"),
    ("GDPR_6_1_D", "6", "1", "d", "Salvaguardia degli interessi vitali dell'interessato o di terzi"),
    ("GDPR_6_1_E", "6", "1", "e", "Esecuzione di un compito di interesse pubblico o pubblici poteri"),
    ("GDPR_6_1_F", "6", "1", "f", "Legittimo interesse del titolare o di terzi"),
    ("GDPR_9_2_A", "9", "2", "a", "Consenso esplicito dell'interessato"),
    ("GDPR_9_2_B", "9", "2", "b", "Obblighi in materia di diritto del lavoro e protezione sociale"),
    ("GDPR_9_2_C", "9", "2", "c", "Salvaguardia degli interessi vitali dell'interessato o di terzi"),
    ("GDPR_9_2_D", "9", "2", "d", "Attività legittime di fondazioni, associazioni o altri enti senza scopo di lucro"),
    ("GDPR_9_2_E", "9", "2", "e", "Dati resi manifestamente pubblici dall'interessato"),
    ("GDPR_9_2_F", "9", "2", "f", "Accertamento, esercizio o difesa di un diritto in sede giudiziaria"),
    ("GDPR_9_2_G", "9", "2", "g", "Motivi di interesse pubblico rilevante"),
    ("GDPR_9_2_H", "9", "2", "h", "Finalità di medicina preventiva, diagnosi, assistenza sanitaria"),
    ("GDPR_9_2_I", "9", "2", "i", "Interesse pubblico nel settore della sanità pubblica"),
    ("GDPR_9_2_J", "9", "2", "j", "Finalità di archiviazione nel pubblico interesse, ricerca scientifica/storica o statistica"),
    ("GDPR_10", "10", None, None, "Dati relativi a condanne penali e reati"),
]

_DATA_SUBJECT_CATALOG = [
    ("CITTADINI", "Cittadini"),
    ("UTENTI_SERVIZI", "Utenti dei servizi"),
    ("DIPENDENTI", "Dipendenti"),
    ("AMMINISTRATORI", "Amministratori"),
    ("MINORI", "Minori"),
    ("FAMILIARI", "Familiari"),
    ("FORNITORI", "Fornitori"),
    ("PROFESSIONISTI", "Professionisti"),
    ("RICHIEDENTI", "Richiedenti"),
    ("SEGNALANTI", "Segnalanti"),
    ("SOGGETTI_VULNERABILI", "Soggetti vulnerabili"),
]

_DATA_CATEGORY_TREE = [
    ("IDENTIFICATIVI", "Identificativi", GdprDataClass.ORDINARY, [
        ("NOME_COGNOME", "Nome e cognome"),
        ("CODICE_FISCALE", "Codice fiscale"),
        ("DATA_NASCITA", "Data di nascita"),
    ]),
    ("CONTATTO", "Contatto", GdprDataClass.ORDINARY, [
        ("INDIRIZZO", "Indirizzo"),
        ("EMAIL", "Email"),
        ("TELEFONO", "Telefono"),
    ]),
    ("ECONOMICO", "Economico/patrimoniali", GdprDataClass.ORDINARY, [
        ("REDDITO", "Reddito"),
        ("ISEE", "ISEE"),
        ("IBAN", "IBAN"),
    ]),
    ("ART9", "Categorie particolari (art. 9)", GdprDataClass.SPECIAL_ART9, [
        ("SALUTE", "Dati relativi alla salute"),
        ("ORIGINE", "Origine razziale/etnica"),
        ("RELIGIONE", "Convinzioni religiose"),
        ("BIOMETRIA", "Dati biometrici"),
        ("SESSUALE", "Vita/orientamento sessuale"),
    ]),
    ("ART10", "Dati giudiziari (art. 10)", GdprDataClass.CRIMINAL_ART10, []),
]

_SECURITY_MEASURES = [
    ("MFA", "Autenticazione a più fattori", MeasureCategory.AUTHENTICATION),
    ("RBAC", "Controllo accessi basato sui ruoli", MeasureCategory.ACCESS_CONTROL),
    ("TLS", "Cifratura in transito (TLS)", MeasureCategory.ENCRYPTION),
    ("ENC_REST", "Cifratura a riposo", MeasureCategory.ENCRYPTION),
    ("LOG_AUDIT", "Logging e tracciamento accessi", MeasureCategory.LOGGING),
    ("BACKUP", "Backup periodico e restore testato", MeasureCategory.BACKUP),
    ("RETENTION", "Policy di conservazione e cancellazione", MeasureCategory.DATA_MINIMIZATION),
    ("SIGNAGE", "Informativa e cartellonistica", MeasureCategory.ORGANIZATIONAL),
    ("VENDOR_DPA", "Accordo art. 28 con il fornitore", MeasureCategory.VENDOR_MANAGEMENT),
    ("INCIDENT", "Procedura di gestione incidenti", MeasureCategory.INCIDENT_MANAGEMENT),
]


async def seed_privacypa_foundations() -> None:
    async with AsyncSessionLocal() as session:
        existing_roles = (await session.execute(select(PrivacyRoleCatalog.code))).scalars().all()
        existing_role_codes = set(existing_roles)
        created_roles = 0
        for code, name, description in _ROLE_CATALOG:
            if code in existing_role_codes:
                continue
            session.add(PrivacyRoleCatalog(code=code, name=name, description=description))
            created_roles += 1

        existing_basis = (await session.execute(select(LegalBasisCatalog.code))).scalars().all()
        existing_basis_codes = set(existing_basis)
        created_basis = 0
        for code, article, paragraph, letter, description in _LEGAL_BASIS_CATALOG:
            if code in existing_basis_codes:
                continue
            session.add(LegalBasisCatalog(
                code=code, article=article, paragraph=paragraph, letter=letter, description=description,
            ))
            created_basis += 1

        existing_subjects = (await session.execute(select(DataSubjectCategoryCatalog.code))).scalars().all()
        existing_subject_codes = set(existing_subjects)
        created_subjects = 0
        for code, name in _DATA_SUBJECT_CATALOG:
            if code in existing_subject_codes:
                continue
            session.add(DataSubjectCategoryCatalog(tenant_id=None, code=code, name=name))
            created_subjects += 1

        existing_cats = (await session.execute(select(DataCategoryCatalog))).scalars().all()
        existing_cat_codes = {c.code for c in existing_cats}
        created_cats = 0
        parent_ids: dict[str, str] = {c.code: c.id for c in existing_cats}
        for parent_code, parent_name, gdpr_class, children in _DATA_CATEGORY_TREE:
            if parent_code not in existing_cat_codes:
                parent = DataCategoryCatalog(
                    tenant_id=None, code=parent_code, name=parent_name, gdpr_class=gdpr_class
                )
                session.add(parent)
                await session.flush()
                parent_ids[parent_code] = parent.id
                existing_cat_codes.add(parent_code)
                created_cats += 1
            for child_code, child_name in children:
                if child_code in existing_cat_codes:
                    continue
                session.add(DataCategoryCatalog(
                    tenant_id=None,
                    parent_id=parent_ids.get(parent_code),
                    code=child_code,
                    name=child_name,
                    gdpr_class=gdpr_class,
                ))
                existing_cat_codes.add(child_code)
                created_cats += 1

        existing_meas = (await session.execute(select(SecurityMeasure.code))).scalars().all()
        existing_meas_codes = set(existing_meas)
        created_meas = 0
        for code, name, category in _SECURITY_MEASURES:
            if code in existing_meas_codes:
                continue
            session.add(SecurityMeasure(tenant_id=None, code=code, name=name, category=category))
            created_meas += 1

        if created_roles or created_basis or created_subjects or created_cats or created_meas:
            await session.commit()
            logger.info(
                f"PrivacyPA seed: {created_roles} ruoli, {created_basis} basi giuridiche, "
                f"{created_subjects} categorie interessati, {created_cats} categorie dati, "
                f"{created_meas} misure"
            )
        else:
            logger.debug("PrivacyPA seed: cataloghi già popolati")
