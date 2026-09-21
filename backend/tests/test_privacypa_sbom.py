"""Software Bill of Materials (§137): CycloneDX 1.5 e SPDX 2.3, componenti Python e npm."""
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from backend.models.privacypa_audit import AuditLog
from backend.models.privacypa_identity import PrivacyRoleCode as R
from backend.services import privacypa_sbom as sbom
from backend.tests.conftest import TA, _h
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
ADMIN = _h(TA, "root", "admin")


def _lock(tmp_path: Path) -> Path:
    lock = {"lockfileVersion": 3, "packages": {
        "": {"name": "frontend", "version": "1.0.0"},
        "node_modules/react": {"version": "18.3.1", "resolved": "https://registry.npmjs.org/react/-/react-18.3.1.tgz", "license": "MIT",
                                "integrity": "sha512-" + "QUJD" * 21 + "QQ==", "dependencies": {"loose-envify": "^1.1.0", "@mui/material": "^5"}},
        "node_modules/loose-envify": {"version": "1.4.0", "license": "MIT", "dependencies": {"js-tokens": "^4"}},
        "node_modules/js-tokens": {"version": "4.0.0", "license": "MIT"},
        "node_modules/@mui/material": {"version": "5.15.0", "license": "MIT", "dependencies": {"js-tokens": "^3"}},
        "node_modules/@mui/material/node_modules/js-tokens": {"version": "3.0.2", "license": "MIT"},
        "node_modules/other/node_modules/js-tokens": {"version": "4.0.0", "license": "MIT", "dev": True},     # stessa versione in un altro percorso
        "node_modules/typescript": {"version": "5.4.0", "license": "Apache-2.0", "dev": True},
        "node_modules/odd": {"version": "1.0.0", "license": "SEE LICENSE IN LICENSE.md"},
        "node_modules/nolicense": {"version": "0.1.0"},
    }}
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def _requirements(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "requirements.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ================================================================ raccolta

def test_python_components_come_from_the_installed_environment_with_transitive_dependencies(tmp_path):
    req = _requirements(tmp_path, "# commento", "fastapi==0.115.0  # con commento", "uvicorn[standard]==0.30.0", "-r altro.txt", "pacchetto-inesistente-xyz==1.0", "")
    got = {c["name"].lower(): c for c in sbom.collect(requirements=req, lockfile=tmp_path / "no.json")["components"]}
    fastapi = got["fastapi"]
    assert fastapi["ecosystem"] == "pypi" and fastapi["direct"] is True and fastapi["ref"] == f"pkg:pypi/fastapi@{fastapi['version']}"
    assert fastapi["pinned"] == "0.115.0"
    assert "starlette" in got and got["starlette"]["direct"] is False                                  # transitiva
    assert any(d.startswith("pkg:pypi/starlette@") for d in fastapi["depends"])
    assert got["pacchetto-inesistente-xyz"]["version"] is None and got["pacchetto-inesistente-xyz"]["ref"] == "pkg:pypi/pacchetto-inesistente-xyz"
    assert "uvicorn" in got and got["uvicorn"]["pinned"] == "0.30.0"
    assert "httptools" in got or "uvloop" in got or "watchfiles" in got                                  # gli extra dichiarati si seguono


def test_npm_components_resolve_dependencies_like_node_and_keep_scoped_purls(tmp_path):
    got = sbom.collect(requirements=tmp_path / "no.txt", lockfile=_lock(tmp_path))
    by_ref = {c["ref"]: c for c in got["components"]}
    assert got["sources"] == ["frontend/package-lock.json"]
    react = by_ref["pkg:npm/react@18.3.1"]
    assert react["scope"] == "required" and react["license"] == "MIT" and react["url"].endswith("react-18.3.1.tgz")
    assert react["hash"] == (b"ABC" * 21 + b"A").hex() and len(react["hash"]) == 128                        # SHA-512 in esadecimale
    assert "pkg:npm/%40mui/material@5.15.0" in by_ref                                                       # scope npm codificato
    assert sorted(react["depends"]) == ["pkg:npm/%40mui/material@5.15.0", "pkg:npm/loose-envify@1.4.0"]
    assert by_ref["pkg:npm/%40mui/material@5.15.0"]["depends"] == ["pkg:npm/js-tokens@3.0.2"]               # la copia annidata prevale
    assert by_ref["pkg:npm/loose-envify@1.4.0"]["depends"] == ["pkg:npm/js-tokens@4.0.0"]                   # quella in radice per gli altri
    assert by_ref["pkg:npm/typescript@5.4.0"]["scope"] == "optional"
    assert [c["ref"] for c in got["components"]].count("pkg:npm/js-tokens@4.0.0") == 1                      # stesso componente in due percorsi
    assert by_ref["pkg:npm/odd@1.0.0"]["license"] is None and by_ref["pkg:npm/odd@1.0.0"]["license_text"] == "SEE LICENSE IN LICENSE.md"
    assert by_ref["pkg:npm/nolicense@0.1.0"]["license"] is None and by_ref["pkg:npm/nolicense@0.1.0"]["license_text"] is None


# ================================================================ documenti

@pytest.fixture(scope="module")
def documents():
    collected = sbom.collect()
    return collected, sbom.cyclonedx(collected, "9.9.9"), sbom.spdx(collected, "9.9.9")


def test_cyclonedx_document_is_consistent(documents):
    collected, doc, _ = documents
    assert doc["bomFormat"] == "CycloneDX" and doc["specVersion"] == "1.5" and doc["version"] == 1
    uuid.UUID(doc["serialNumber"].removeprefix("urn:uuid:"))
    assert doc["metadata"]["component"] == {"type": "application", "bom-ref": "pkg:generic/privacypa@9.9.9", "name": "PrivacyPA", "version": "9.9.9"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["metadata"]["timestamp"])
    refs = [c["bom-ref"] for c in doc["components"]]
    assert len(refs) == len(set(refs)) and len(refs) == len(collected["components"]) > 100
    assert all(c["purl"] == c["bom-ref"] and c["purl"].startswith(("pkg:pypi/", "pkg:npm/")) and c["name"] and c["type"] == "library" for c in doc["components"])
    known = set(refs) | {doc["metadata"]["component"]["bom-ref"]}
    assert len({d["ref"] for d in doc["dependencies"]}) == len(doc["dependencies"])
    assert all(d["ref"] in known and set(d["dependsOn"]) <= known for d in doc["dependencies"])
    root = next(d for d in doc["dependencies"] if d["ref"] == "pkg:generic/privacypa@9.9.9")
    assert any(r.startswith("pkg:pypi/fastapi@") for r in root["dependsOn"]) and not any(r.startswith("pkg:npm/") for r in root["dependsOn"])
    assert any(hash_["alg"] == "SHA-512" and len(hash_["content"]) == 128 for c in doc["components"] for hash_ in c.get("hashes", []))
    assert any("licenses" in c for c in doc["components"])
    for lic in (entry for c in doc["components"] for entry in c.get("licenses", [])):
        assert "expression" in lic or "id" in lic.get("license", {}) or lic["license"].get("name")


def test_every_requirement_is_a_component_and_pin_drift_is_reported(documents):
    collected, doc, _ = documents
    names = {sbom._norm(c["name"]) for c in collected["components"] if c["ecosystem"] == "pypi"}
    for name, _, _ in sbom._requirements(ROOT / "requirements.txt"):
        assert sbom._norm(name) in names, name
    for c in doc["components"]:                                                                              # se l'ambiente si discosta, la SBOM lo dice
        props = {p["name"]: p["value"] for p in c.get("properties", [])}
        if "privacypa:pinned-version" in props:
            assert props["privacypa:pinned-version"] != c["version"]


def test_spdx_document_is_consistent(documents):
    collected, _, doc = documents
    assert doc["spdxVersion"] == "SPDX-2.3" and doc["dataLicense"] == "CC0-1.0" and doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert doc["documentNamespace"].startswith("https://spdx.org/spdxdocs/privacypa-9.9.9-") and doc["creationInfo"]["creators"] == ["Tool: privacypa-sbom 1.0"]
    ids = [p["SPDXID"] for p in doc["packages"]]
    assert len(ids) == len(set(ids)) == len(collected["components"]) + 1 and all(re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", i) for i in ids)
    known = set(ids) | {"SPDXRef-DOCUMENT"}
    assert all(r["spdxElementId"] in known and r["relatedSpdxElement"] in known for r in doc["relationships"])
    assert doc["relationships"][0] == {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-Package-Root"}
    licenses = {p["licenseDeclared"] for p in doc["packages"]}
    assert "NOASSERTION" in licenses and "MIT" in licenses and all(l == "NOASSERTION" or re.fullmatch(r"[A-Za-z0-9.+-]+( (OR|AND) [A-Za-z0-9.+-]+)*", l) for l in licenses)
    assert all(p["licenseConcluded"] == "NOASSERTION" for p in doc["packages"])                                # nessuna conclusione inventata
    assert all(ref["referenceType"] == "purl" for p in doc["packages"][1:] for ref in p["externalRefs"])
    assert any(c["algorithm"] == "SHA512" for p in doc["packages"] for c in p.get("checksums", []))


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        sbom.build("swid")


# ================================================================ endpoint e script

@pytest.mark.asyncio
async def test_sbom_endpoint_is_admin_only_audited_and_validates_the_format(env):
    c = env["client"]
    await env["grant"](TA, "mgr", R.PRIVACY_MANAGER)
    assert (await c.get("/api/privacypa/export/sbom", headers=_h(TA, "mgr"))).status_code == 403
    r = await c.get("/api/privacypa/export/sbom", headers=ADMIN)
    assert r.status_code == 200 and r.json()["bomFormat"] == "CycloneDX" and r.headers["content-disposition"].endswith('.cdx.json"')
    spdx = await c.get("/api/privacypa/export/sbom", params={"format": "spdx"}, headers=ADMIN)
    assert spdx.status_code == 200 and spdx.json()["spdxVersion"] == "SPDX-2.3" and spdx.headers["content-disposition"].endswith('.spdx.json"')
    assert (await c.get("/api/privacypa/export/sbom", params={"format": "xml"}, headers=ADMIN)).status_code == 422
    async with env["maker"]() as db:
        events = list((await db.execute(select(AuditLog).where(AuditLog.event_type == "system.sbom.export").order_by(AuditLog.event_time))).scalars().all())
    assert [e.event_metadata["format"] for e in events] == ["cyclonedx", "spdx"] and events[0].event_metadata["components"] > 100


def test_generator_script_writes_both_formats(tmp_path):
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_sbom.py"), "--version", "2.3.4", "--out", str(tmp_path)],
                         capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert run.returncode == 0, run.stderr
    cdx, spdx = json.loads((tmp_path / "privacypa-2.3.4.cdx.json").read_text("utf-8")), json.loads((tmp_path / "privacypa-2.3.4.spdx.json").read_text("utf-8"))
    assert cdx["metadata"]["component"]["version"] == "2.3.4" and spdx["name"] == "PrivacyPA-2.3.4" and len(cdx["components"]) == len(spdx["packages"]) - 1
