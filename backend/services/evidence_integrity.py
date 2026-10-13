"""Standard-library verification for Anvil evidence archive inventories."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any, BinaryIO


MAX_EVIDENCE_FILES = 10_000
MAX_EVIDENCE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _safe_archive_path(path: str) -> bool:
    if not path or "\\" in path or "\x00" in path or path.startswith("/") or ":" in path:
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def verify_evidence_bundle(bundle: BinaryIO | bytes) -> dict[str, Any]:
    """Check the SHA-256 inventory in an evidence ZIP.

    This verifies internal consistency only. A separately trusted manifest hash
    or signature is required to prove that the manifest itself was not replaced.
    """
    source: Any = io.BytesIO(bundle) if isinstance(bundle, bytes) else bundle
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names.count("manifest.json") != 1 or len(names) != len(set(names)):
                return {"valid": False, "checked_files": 0, "error": "invalid_archive_entries"}
            if len(names) - 1 > MAX_EVIDENCE_FILES:
                return {"valid": False, "checked_files": 0, "error": "bundle_resource_limit"}
            if any(info.is_dir() or not _safe_archive_path(info.filename) for info in infos):
                return {"valid": False, "checked_files": 0, "error": "unsafe_archive_path"}
            if sum(info.file_size for info in infos) > MAX_EVIDENCE_UNCOMPRESSED_BYTES:
                return {"valid": False, "checked_files": 0, "error": "bundle_resource_limit"}
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {"valid": False, "checked_files": 0, "error": "invalid_manifest"}
            inventory = manifest.get("integrity") if isinstance(manifest, dict) else None
            if not isinstance(inventory, dict) or inventory.get("algorithm") != "sha256":
                return {"valid": False, "checked_files": 0, "error": "unsupported_integrity_manifest"}
            records = inventory.get("files")
            if not isinstance(records, list):
                return {"valid": False, "checked_files": 0, "error": "invalid_integrity_inventory"}
            declared: dict[str, dict[str, Any]] = {}
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                    return {"valid": False, "checked_files": 0, "error": "invalid_integrity_inventory"}
                path = record["path"]
                if not _safe_archive_path(path):
                    return {"valid": False, "checked_files": 0, "error": "unsafe_archive_path"}
                if (path == "manifest.json" or path in declared
                        or type(record.get("bytes")) is not int or record["bytes"] < 0
                        or not isinstance(record.get("sha256"), str)
                        or not _SHA256_RE.fullmatch(record["sha256"])):
                    return {"valid": False, "checked_files": 0, "error": "invalid_integrity_inventory"}
                declared[path] = record
            actual = set(names) - {"manifest.json"}
            if set(declared) != actual:
                return {"valid": False, "checked_files": 0, "error": "inventory_mismatch"}
            checked = 0
            for path, record in declared.items():
                digest = hashlib.sha256()
                size = 0
                with archive.open(path, "r") as entry:
                    while chunk := entry.read(64 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                checked += 1
                if record.get("sha256") != digest.hexdigest() or record.get("bytes") != size:
                    return {"valid": False, "checked_files": checked,
                            "error": "file_integrity_mismatch", "path": path}
            return {"valid": True, "checked_files": checked, "error": None}
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return {"valid": False, "checked_files": 0, "error": "invalid_archive"}
