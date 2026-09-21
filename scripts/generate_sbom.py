#!/usr/bin/env python
"""
Genera la Software Bill of Materials (§137) di PrivacyPA: CycloneDX 1.5 e SPDX 2.3 in JSON.

    python scripts/generate_sbom.py --version 1.2.0 --out sbom

Da eseguire nell'ambiente che contiene le dipendenze installate (la SBOM riporta le versioni *installate*, e segnala
quelle diverse da requirements.txt) e con `frontend/package-lock.json` presente per includere il frontend.
Esce con codice 1 se un pacchetto di requirements.txt non è installato.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.privacypa_sbom import build, collect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=None, help="versione della release (default: APP_VERSION o 1.0.0)")
    parser.add_argument("--out", default="sbom", help="cartella di destinazione")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    collected = collect()
    missing = [c["name"] for c in collected["components"] if c["ecosystem"] == "pypi" and not c["version"]]
    drift = [f"{c['name']} {c['pinned']} -> {c['version']}" for c in collected["components"] if c.get("pinned") and c["pinned"] != c["version"]]
    for fmt, suffix in (("cyclonedx", "cdx"), ("spdx", "spdx")):
        document = build(fmt, args.version)
        version = args.version or (document["metadata"]["component"]["version"] if fmt == "cyclonedx" else document["name"].split("-", 1)[1])
        path = out / f"privacypa-{version}.{suffix}.json"
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{path}  ({len(collected['components'])} componenti da {', '.join(collected['sources'])})")
    if drift:
        print("ATTENZIONE: versioni installate diverse da requirements.txt:\n  " + "\n  ".join(drift), file=sys.stderr)
    if missing:
        print("ERRORE: pacchetti di requirements.txt non installati: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
