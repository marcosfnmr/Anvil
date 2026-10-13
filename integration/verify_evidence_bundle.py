#!/usr/bin/env python3
"""Verify file hashes and sizes in an Anvil evidence ZIP manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "services"))

from evidence_integrity import verify_evidence_bundle  # noqa: E402


def verify_file(path: str | Path, expected_manifest_sha256: str | None = None) -> dict[str, object]:
    """Verify a bundle on disk without requiring a running Anvil server."""
    try:
        with Path(path).open("rb") as bundle:
            result = verify_evidence_bundle(bundle)
        if result.get("valid"):
            with zipfile.ZipFile(path, "r") as archive, archive.open("manifest.json") as manifest:
                digest = hashlib.sha256()
                while chunk := manifest.read(64 * 1024):
                    digest.update(chunk)
                actual = digest.hexdigest()
            result["manifest_sha256"] = actual
            if expected_manifest_sha256 is not None:
                if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_manifest_sha256):
                    return {"valid": False, "checked_files": result["checked_files"],
                            "error": "invalid_expected_manifest_sha256", "manifest_sha256": actual}
                if actual.casefold() != expected_manifest_sha256.casefold():
                    return {"valid": False, "checked_files": result["checked_files"],
                            "error": "manifest_sha256_mismatch", "manifest_sha256": actual}
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as error:
        return {"valid": False, "checked_files": 0, "error": "invalid_archive", "detail": str(error)}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="path to evidence-bundle.zip")
    parser.add_argument("--expected-manifest-sha256", help="trusted manifest hash received separately")
    args = parser.parse_args(argv)
    result = verify_file(args.bundle, args.expected_manifest_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("valid") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
