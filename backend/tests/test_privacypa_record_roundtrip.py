"""Il PATCH sostituisce il record: le pagine rimandano i campi che il modulo non mostra, così come li hanno letti."""
import pytest
import pytest_asyncio

from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.tests.conftest import TA, _h, ifm


@pytest_asyncio.fixture
async def mgr(env):
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    return _h(TA, "mgr")


@pytest.mark.asyncio
async def test_asset_sent_back_as_read_keeps_the_fields_outside_the_form(env, mgr):
    c = env["client"]
    created = await c.post("/api/privacypa/assets", json={
        "name": "Protocollo", "mfa_supported": True, "encryption_at_rest": True,
        "authentication_type": "SPID", "go_live_date": "2024-01-15", "hosting_country": None,
    }, headers=mgr)
    assert created.status_code == 201, created.text
    asset = created.json()

    # Come AssetsPage: il record letto (con id, codice, date di sistema) più i campi del modulo
    r = await c.patch(f"/api/privacypa/assets/{asset['id']}", json={**asset, "name": "Protocollo informatico"},
                      headers=ifm(asset["row_version"], mgr))
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["name"] == "Protocollo informatico"
    assert (saved["mfa_supported"], saved["encryption_at_rest"]) == (True, True)
    assert (saved["authentication_type"], saved["go_live_date"], saved["hosting_country"]) == ("SPID", "2024-01-15", None)


@pytest.mark.asyncio
async def test_processor_profile_sent_back_as_read_is_not_reset(env, mgr):
    c = env["client"]
    created = await c.post("/api/privacypa/parties", json={
        "legal_name": "Cloud Srl", "party_type": "PROCESSOR", "tax_id": "01234567897", "vat_id": "IT01234567897", "website": "https://cloud.example",
        "processor_profile": {"status": "ACTIVE", "appointment_end": "2099-12-31", "dpo_contact": "dpo@cloud.example", "next_assessment_date": "2027-03-01"},
    }, headers=mgr)
    assert created.status_code == 201, created.text
    party = created.json()

    # Come PartiesPage: il profilo letto senza party_id, P.IVA e sito rimandati com'erano
    profile = {k: v for k, v in party["processor_profile"].items() if k != "party_id"}
    body = {"legal_name": "Cloud S.r.l.", "party_type": "PROCESSOR", "tax_id": party["tax_id"], "vat_id": party["vat_id"],
            "website": party["website"], "processor_profile": profile}
    r = await c.patch(f"/api/privacypa/parties/{party['id']}", json=body, headers=ifm(party["row_version"], mgr))
    assert r.status_code == 200, r.text
    saved = r.json()
    assert (saved["vat_id"], saved["website"]) == ("01234567897", "https://cloud.example")
    assert saved["processor_profile"]["status"] == "ACTIVE"
    assert saved["processor_profile"]["dpo_contact"] == "dpo@cloud.example"
    assert saved["processor_profile"]["next_assessment_date"] == "2027-03-01"
