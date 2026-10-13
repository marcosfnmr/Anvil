#!/usr/bin/env python3
"""Create SPDX SBOMs and build provenance for the locally built Anvil images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
IMAGES = (
    ("backend", "anvil/backend:ci", "backend/Dockerfile", ("backend/requirements.lock",)),
    ("docker-api", "anvil/docker-api-gate:ci", "docker_proxy/Dockerfile", ()),
    ("plc-s7", "anvil/plc-s7:ci", "plc/Dockerfile", ("plc/requirements.lock",)),
    ("capture", "anvil/capture:ci", "plc/Dockerfile.capture", ()),
    ("field", "anvil/field:ci", "plc/Dockerfile.field", ()),
    ("egress-proxy", "anvil/egress-proxy:ci", "plc/Dockerfile.egress", ()),
    ("modbus-tcp", "anvil/modbus-tcp:ci", "plc/Dockerfile.modbus", ("plc/requirements.modbus.lock",)),
    (
        "scada",
        "anvil/scada:ci",
        "plc/Dockerfile.scada",
        ("plc/requirements.lock", "plc/requirements.modbus.lock"),
    ),
)


def _spdx_id(prefix: str, value: str) -> str:
    return f"SPDXRef-{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _purl(kind: str, name: str, version: str, namespace: str | None = None) -> str:
    path = f"{quote(namespace, safe='.-_~')}/" if namespace else ""
    return f"pkg:{kind}/{path}{quote(name, safe='.-_~')}@{quote(version, safe='.-_~')}"


def _package(
    *, name: str, version: str, purl: str | None = None, supplier: str = "NOASSERTION"
) -> dict[str, Any]:
    package: dict[str, Any] = {
        "name": name,
        "SPDXID": _spdx_id("Package", f"{purl or name}@{version}"),
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "supplier": supplier,
    }
    if purl:
        package["externalRefs"] = [
            {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": purl}
        ]
    return package


def make_spdx_document(
    *,
    image_ref: str,
    image_id: str,
    base_image_ref: str,
    os_packages: list[tuple[str, str]],
    python_packages: list[tuple[str, str]],
    distro: str = "debian-12",
) -> dict[str, Any]:
    root = _package(name=image_ref, version=image_id)
    base = _package(
        name=base_image_ref.split("@", 1)[0],
        version=base_image_ref.split("@", 1)[1],
    )
    packages = [root, base]
    dependencies = [base["SPDXID"]]

    for name, version in sorted(os_packages, key=lambda item: (item[0].lower(), item[1])):
        package_name = name.split(":", 1)[0]
        architecture = name.split(":", 1)[1] if ":" in name else None
        qualifiers = f"?distro={quote(distro, safe='.-_~')}"
        if architecture:
            qualifiers += f"&arch={quote(architecture, safe='.-_~')}"
        purl = _purl("deb", package_name, version, "debian") + qualifiers
        package = _package(name=name, version=version, purl=purl, supplier="Organization: Debian")
        packages.append(package)
        dependencies.append(package["SPDXID"])

    for name, version in sorted(python_packages, key=lambda item: (item[0].lower(), item[1])):
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        package = _package(
            name=name,
            version=version,
            purl=_purl("pypi", normalized, version),
        )
        packages.append(package)
        dependencies.append(package["SPDXID"])

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"SBOM for {image_ref}",
        "documentNamespace": f"https://anvil.invalid/spdx/{uuid.uuid4()}",
        "creationInfo": {"creators": ["Tool: anvil-container-sbom/1.0"], "created": now},
        "documentDescribes": [root["SPDXID"]],
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": root["SPDXID"],
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
            for package_id in dependencies
        ],
    }


def _run(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout


def _base_image(dockerfile: Path) -> str:
    text = dockerfile.read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG\s+([A-Z0-9_]+)=([^\s]+)", text, re.MULTILINE))
    match = re.search(r"^FROM\s+\$\{([A-Z0-9_]+)\}", text, re.MULTILINE)
    if not match or match.group(1) not in args:
        raise ValueError(f"Cannot resolve pinned base image from {dockerfile.relative_to(ROOT)}")
    base = args[match.group(1)]
    if not re.search(r"@sha256:[0-9a-f]{64}$", base):
        raise ValueError(f"Base image is not digest pinned: {base}")
    return base


def _image_inventory(
    image_ref: str, *, has_python: bool
) -> tuple[str, dict[str, str], list[tuple[str, str]], list[tuple[str, str]], str]:
    image_data = json.loads(_run("docker", "image", "inspect", image_ref))[0]
    image_id = image_data["Id"]
    os_version = _run(
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", image_ref, "/etc/os-release"
    )
    distro_values: dict[str, str] = {}
    for match in re.finditer(r"^(ID|VERSION_ID)=(?:\"([^\"]*)\"|([^\n]*))$", os_version, re.MULTILINE):
        distro_values[match.group(1)] = match.group(2) or match.group(3)
    distro = f"{distro_values.get('ID', 'unknown')}-{distro_values.get('VERSION_ID', 'unknown')}"

    dpkg_output = _run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "dpkg-query",
        image_ref,
        "-W",
        "-f=${binary:Package}\t${Version}\n",
    )
    os_packages = [tuple(line.split("\t", 1)) for line in dpkg_output.splitlines() if "\t" in line]
    python_packages: list[tuple[str, str]] = []
    if has_python:
        inspect = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image_ref, "-m", "pip", "inspect", "--local"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if inspect.returncode == 0:
            pip_data = json.loads(inspect.stdout)
            python_packages = [
                (item["metadata"]["name"], item["metadata"]["version"])
                for item in pip_data.get("installed", [])
                if item.get("metadata", {}).get("name") and item.get("metadata", {}).get("version")
            ]
        elif "No module named pip" not in inspect.stderr:
            raise RuntimeError(f"pip inventory failed for {image_ref}: {inspect.stderr.strip()}")
    return image_id, image_data, os_packages, python_packages, distro


def generate(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    revision = os.environ.get("GITHUB_SHA") or _run("git", "rev-parse", "HEAD").strip()
    status = _run("git", "status", "--porcelain", "--untracked-files=normal")
    generated: list[Path] = []
    provenance: dict[str, Any] = {
        "schema": "https://anvil.invalid/provenance/v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": {
            "repository": os.environ.get("GITHUB_REPOSITORY", "local-checkout"),
            "revision": revision,
            "workflow_run": os.environ.get("GITHUB_RUN_ID"),
            "working_tree_clean": not bool(status.strip()),
        },
        "images": [],
    }

    for artifact_name, image_ref, dockerfile_name, lockfiles in IMAGES:
        dockerfile = ROOT / dockerfile_name
        base_image_ref = _base_image(dockerfile)
        image_id, image_data, os_packages, python_packages, distro = _image_inventory(
            image_ref,
            has_python=base_image_ref.startswith("docker.io/library/python:"),
        )
        document = make_spdx_document(
            image_ref=image_ref,
            image_id=image_id,
            base_image_ref=base_image_ref,
            os_packages=os_packages,
            python_packages=python_packages,
            distro=distro,
        )
        sbom_path = output_dir / f"{artifact_name}.spdx.json"
        sbom_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        generated.append(sbom_path)
        input_hashes = {}
        for relative in (dockerfile_name, *lockfiles):
            input_hashes[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        provenance["images"].append(
            {
                "name": image_ref,
                "image_id": image_id,
                "platform": f"{image_data['Os']}/{image_data['Architecture']}",
                "base_image": base_image_ref,
                "dockerfile": dockerfile_name,
                "input_sha256": input_hashes,
                "sbom": sbom_path.name,
                "sbom_sha256": hashlib.sha256(sbom_path.read_bytes()).hexdigest(),
                "inventory_counts": {"debian_packages": len(os_packages), "python_packages": len(python_packages)},
            }
        )

    provenance_path = output_dir / "provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    generated.append(provenance_path)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = generate(args.output_dir)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"container SBOM generation failed: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
