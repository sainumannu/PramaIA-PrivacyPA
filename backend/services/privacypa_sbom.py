"""
PrivacyPA — Software Bill of Materials (§137) in CycloneDX 1.5 e SPDX 2.3 (JSON).

Componenti Python: i pacchetti di `requirements.txt` e le loro dipendenze transitive, con la versione **installata**
(`importlib.metadata`). Componenti del frontend: `frontend/package-lock.json`, se presente (l'immagine Docker contiene solo il
backend). Le licenze sono quelle dichiarate dai pacchetti; se non sono un identificativo SPDX noto restano «non dichiarate»
(nome libero in CycloneDX, NOASSERTION in SPDX): nessun campo viene inventato.

Non sostituisce un'analisi delle vulnerabilità (§139): elenca cosa c'è, non se è sicuro.
"""
import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "PrivacyPA"
TOOL = "privacypa-sbom 1.0"
FORMATS = ("cyclonedx", "spdx")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")

# Identificativi SPDX riconosciuti (classifier PyPI o testo del campo License → id).
_SPDX = {
    "mit": "MIT", "mit license": "MIT", "bsd": "BSD-3-Clause", "bsd license": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause", "3-clause bsd license": "BSD-3-Clause", "2-clause bsd license": "BSD-2-Clause",
    "apache software license": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache-2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "isc": "ISC", "isc license (iscl)": "ISC", "python software foundation license": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "mpl-2.0": "MPL-2.0", "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "lgpl-3.0": "LGPL-3.0-only", "gnu general public license v3 (gplv3)": "GPL-3.0-only", "0bsd": "0BSD", "unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0", "cc-by-4.0": "CC-BY-4.0", "blueoak-1.0.0": "BlueOak-1.0.0", "python-2.0": "Python-2.0",
    "apache-2.0 or bsd-3-clause": "Apache-2.0 OR BSD-3-Clause", "bsd-3-clause or apache-2.0": "BSD-3-Clause OR Apache-2.0",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _license(candidates: list[str]) -> str | None:
    for text in candidates:
        found = _SPDX.get((text or "").strip().lower())
        if found:
            return found
    return None


# ---------------------------------------------------------------- Python

def _requirements(path: Path) -> list[tuple[str, set[str], str | None]]:
    """(nome, extra richiesti, versione fissata con `==`) di ogni riga di un requirements.txt (es. `uvicorn[standard]==0.30.0`)."""
    from packaging.requirements import InvalidRequirement, Requirement

    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            req = Requirement(line)
            pins = [sp.version for sp in req.specifier if sp.operator == "=="]
            out.append((req.name, set(req.extras), pins[0] if pins else None))
        except InvalidRequirement:
            match = _NAME.match(line)
            if match:
                out.append((match.group(0), set(), None))
    return out


def _python_packages(requirements: Path) -> dict[str, dict]:
    """{nome normalizzato: {name, version, license, depends, direct}} per requirements e transitive installate."""
    from packaging.requirements import Requirement

    found: dict[str, dict] = {}
    declared = _requirements(requirements)
    direct = {_norm(n) for n, _, _ in declared}
    pinned = {_norm(n): pin for n, _, pin in declared if pin}
    queue = [(n, True, extras) for n, extras, _ in declared]
    while queue:
        name, is_direct, extras = queue.pop()
        key = _norm(name)
        if key in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            found[key] = {"name": name, "version": None, "license": None, "depends": [], "direct": is_direct, "missing": True}
            continue
        meta = dist.metadata
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        depends = []
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in (extras or {""})):
                continue
            depends.append(req.name)
            queue.append((req.name, False, set(req.extras)))
        found[key] = {
            "name": meta["Name"] or name, "version": dist.version, "direct": key in direct, "pinned": pinned.get(key), "depends": sorted({_norm(d) for d in depends}),
            "license": _license([meta.get("License-Expression") or "", *classifiers, meta.get("License") or ""]),
            "license_text": (meta.get("License-Expression") or (classifiers[0] if classifiers else None)),
        }
    return found


def _purl(ecosystem: str, name: str, version: str | None) -> str:
    if ecosystem == "npm" and name.startswith("@"):
        scope, _, rest = name.partition("/")
        return f"pkg:npm/{quote(scope, safe='')}/{quote(rest, safe='')}" + (f"@{version}" if version else "")
    return f"pkg:{ecosystem}/{quote(name, safe='')}" + (f"@{version}" if version else "")


# ---------------------------------------------------------------- npm

def _npm_packages(lock: Path) -> dict[str, dict]:
    """{percorso node_modules: {...}} dal lockfile (v2/v3). Le dipendenze si risolvono come Node: dal pacchetto verso l'alto."""
    data = json.loads(lock.read_text(encoding="utf-8"))
    packages = {p: v for p, v in (data.get("packages") or {}).items() if p.startswith("node_modules/") and v.get("version")}

    def resolve(owner: str, dep: str) -> str | None:
        base = owner
        while True:
            candidate = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
            if candidate in packages:
                return candidate
            if not base:
                return None
            base = base.rsplit("/node_modules/", 1)[0] if "/node_modules/" in base else ""

    out = {}
    for path, info in packages.items():
        name = info.get("name") or path.rsplit("node_modules/", 1)[1]
        deps = {**(info.get("dependencies") or {}), **(info.get("optionalDependencies") or {})}
        out[path] = {
            "name": name, "version": info["version"], "license": info.get("license"), "dev": bool(info.get("dev")),
            "integrity": info.get("integrity"), "resolved": info.get("resolved"),
            "depends": sorted({r for r in (resolve(path, d) for d in deps) if r}),
        }
    return out


def _sha512_hex(integrity: str | None) -> str | None:
    if integrity and integrity.startswith("sha512-"):
        try:
            return base64.b64decode(integrity[7:]).hex()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- raccolta

def collect(*, requirements: Path | None = None, lockfile: Path | None = None) -> dict:
    """Componenti e dipendenze di backend (Python) e, se disponibile, frontend (npm)."""
    requirements = requirements or ROOT / "requirements.txt"
    lockfile = lockfile or ROOT / "frontend" / "package-lock.json"
    components, sources = [], []
    if requirements.exists():
        py = _python_packages(requirements)
        sources.append("requirements.txt")
        for key, p in py.items():
            components.append({
                "ref": f"pkg:pypi/{key}@{p['version']}" if p["version"] else f"pkg:pypi/{key}", "ecosystem": "pypi", "name": p["name"], "version": p["version"],
                "license": p["license"], "license_text": p.get("license_text"), "scope": "required",
                "hash": None, "depends": [f"pkg:pypi/{d}@{py[d]['version']}" if d in py and py[d]["version"] else f"pkg:pypi/{d}" for d in p["depends"] if d in py],
                "direct": p["direct"], "url": None, "pinned": p.get("pinned"),
            })
    if lockfile.exists():
        npm = _npm_packages(lockfile)
        sources.append("frontend/package-lock.json")
        ref = lambda path: _purl("npm", npm[path]["name"], npm[path]["version"])
        seen = set()
        for path, p in sorted(npm.items()):
            purl = ref(path)
            if purl in seen:
                continue                                                # stessa versione in più percorsi: un solo componente
            seen.add(purl)
            components.append({
                "ref": purl, "ecosystem": "npm", "name": p["name"], "version": p["version"], "license": None, "license_text": p["license"] if isinstance(p["license"], str) else None,
                "scope": "optional" if p["dev"] else "required", "hash": _sha512_hex(p["integrity"]), "depends": sorted({ref(d) for d in p["depends"]}),
                "direct": False, "url": p["resolved"],
            })
            spdx = _SPDX.get((p["license"] or "").strip().lower()) if isinstance(p["license"], str) else None
            components[-1]["license"] = spdx
    return {"components": components, "sources": sources}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def cyclonedx(collected: dict, version: str) -> dict:
    root_ref = f"pkg:generic/{APP_NAME.lower()}@{version}"
    comps = []
    for c in collected["components"]:
        item = {"type": "library", "bom-ref": c["ref"], "name": c["name"], "scope": c["scope"], "purl": c["ref"]}
        if c["version"]:
            item["version"] = c["version"]
        if c["license"]:
            item["licenses"] = [{"expression": c["license"]} if " " in c["license"] else {"license": {"id": c["license"]}}]
        elif c["license_text"]:
            item["licenses"] = [{"license": {"name": str(c["license_text"])[:100]}}]
        if c["hash"]:
            item["hashes"] = [{"alg": "SHA-512", "content": c["hash"]}]
        if c.get("pinned") and c["pinned"] != c["version"]:      # l'ambiente non corrisponde a requirements.txt
            item["properties"] = [{"name": "privacypa:pinned-version", "value": c["pinned"]}]
        comps.append(item)
    refs = {c["ref"] for c in collected["components"]}
    deps = [{"ref": root_ref, "dependsOn": sorted(c["ref"] for c in collected["components"] if c["direct"])}]
    deps += [{"ref": c["ref"], "dependsOn": sorted(d for d in c["depends"] if d in refs)} for c in collected["components"]]
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
        "metadata": {
            "timestamp": _now(), "tools": {"components": [{"type": "application", "name": TOOL.split()[0], "version": TOOL.split()[1]}]},
            "component": {"type": "application", "bom-ref": root_ref, "name": APP_NAME, "version": version},
            "properties": [{"name": "privacypa:sources", "value": ", ".join(collected["sources"])}],
        },
        "components": comps, "dependencies": deps,
    }


def spdx(collected: dict, version: str) -> dict:
    ids = {c["ref"]: f"SPDXRef-Package-{i}" for i, c in enumerate(collected["components"], start=1)}
    root = "SPDXRef-Package-Root"
    packages = [{"SPDXID": root, "name": APP_NAME, "versionInfo": version, "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
                 "licenseConcluded": "NOASSERTION", "licenseDeclared": "NOASSERTION", "copyrightText": "NOASSERTION"}]
    for c in collected["components"]:
        pkg = {
            "SPDXID": ids[c["ref"]], "name": c["name"], "downloadLocation": c["url"] or "NOASSERTION", "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION", "licenseDeclared": c["license"] or "NOASSERTION", "copyrightText": "NOASSERTION",
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": c["ref"]}],
        }
        if c["version"]:
            pkg["versionInfo"] = c["version"]
        if c["hash"]:
            pkg["checksums"] = [{"algorithm": "SHA512", "checksumValue": c["hash"]}]
        packages.append(pkg)
    rels = [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": root}]
    rels += [{"spdxElementId": root, "relationshipType": "DEPENDS_ON", "relatedSpdxElement": ids[c["ref"]]} for c in collected["components"] if c["direct"]]
    for c in collected["components"]:
        rels += [{"spdxElementId": ids[c["ref"]], "relationshipType": "DEPENDS_ON", "relatedSpdxElement": ids[d]} for d in c["depends"] if d in ids]
    return {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT", "name": f"{APP_NAME}-{version}",
        "documentNamespace": f"https://spdx.org/spdxdocs/{APP_NAME.lower()}-{version}-{uuid.uuid4()}",
        "creationInfo": {"created": _now(), "creators": [f"Tool: {TOOL}"], "comment": "Sorgenti: " + ", ".join(collected["sources"])},
        "packages": packages, "relationships": rels,
    }


def build(fmt: str, version: str | None = None, **sources) -> dict:
    if fmt not in FORMATS:
        raise ValueError(f"Formato non supportato: {fmt} (ammessi: {', '.join(FORMATS)})")
    version = version or os.getenv("APP_VERSION", "1.0.0")
    collected = collect(**sources)
    return cyclonedx(collected, version) if fmt == "cyclonedx" else spdx(collected, version)
