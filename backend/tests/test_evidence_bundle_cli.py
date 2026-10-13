"""Tests for offline verification of exported evidence ZIPs."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from integration.verify_evidence_bundle import main, verify_file  # noqa: E402


def evidence_zip(payload: bytes = b"audit evidence", *, manifest_payload: bytes | None = None) -> bytes:
    digest = hashlib.sha256(manifest_payload if manifest_payload is not None else payload).hexdigest()
    manifest = {
        "format": "anvil-scenario-evidence",
        "schema_version": 1,
        "integrity": {"algorithm": "sha256", "files": [
            {"path": "audit/events.jsonl", "bytes": len(payload), "sha256": digest}
        ]},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("audit/events.jsonl", payload)
        archive.writestr("manifest.json", json.dumps(manifest))
    return output.getvalue()


def verify_file_from_bytes(payload: bytes) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "evidence.zip"
        path.write_bytes(payload)
        return verify_file(path)


class EvidenceBundleCliTests(unittest.TestCase):
    def test_offline_verifier_rejects_path_traversal_and_noncanonical_windows_paths(self) -> None:
        payload = b"evidence"
        digest = hashlib.sha256(payload).hexdigest()
        for unsafe_path in ("../outside.txt", r"audit\..\outside.txt", "/absolute.txt", "C:/outside.txt"):
            manifest = {
                "format": "anvil-scenario-evidence", "schema_version": 1,
                "integrity": {"algorithm": "sha256", "files": [
                    {"path": unsafe_path, "bytes": len(payload), "sha256": digest}
                ]},
            }
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(unsafe_path, payload)
                archive.writestr("manifest.json", json.dumps(manifest))
            result = verify_file_from_bytes(output.getvalue())
            self.assertFalse(result["valid"], unsafe_path)
            self.assertEqual(result["error"], "unsafe_archive_path")

    def test_offline_verifier_rejects_directories_and_invalid_inventory_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("audit/", b"")
                archive.writestr("manifest.json", json.dumps({
                    "integrity": {"algorithm": "sha256", "files": []},
                }))
            self.assertEqual(verify_file(path)["error"], "unsafe_archive_path")

            invalid = evidence_zip()
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as target:
                target.writestr("audit/events.jsonl", b"audit evidence")
                target.writestr("manifest.json", json.dumps({
                    "integrity": {"algorithm": "sha256", "files": [
                        {"path": "audit/events.jsonl", "bytes": True,
                         "sha256": hashlib.sha256(b"audit evidence").hexdigest()}
                    ]},
                }))
            path.write_bytes(output.getvalue())
            self.assertEqual(verify_file(path)["error"], "invalid_integrity_inventory")

    def test_offline_verifier_accepts_intact_bundle_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.zip"
            path.write_bytes(evidence_zip())
            self.assertTrue(verify_file(path)["valid"])
            path.write_bytes(evidence_zip(b"tampered", manifest_payload=b"audit evidence"))
            result = verify_file(path)
            self.assertFalse(result["valid"])
            self.assertEqual(result["error"], "file_integrity_mismatch")

    def test_cli_can_check_a_manifest_hash_kept_outside_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.zip"
            path.write_bytes(evidence_zip())
            with zipfile.ZipFile(path) as archive:
                trusted_hash = hashlib.sha256(archive.read("manifest.json")).hexdigest()
            result = verify_file(path, trusted_hash)
            self.assertTrue(result["valid"])
            self.assertEqual(result["manifest_sha256"], trusted_hash)
            mismatch = verify_file(path, "0" * 64)
            self.assertFalse(mismatch["valid"])
            self.assertEqual(mismatch["error"], "manifest_sha256_mismatch")

    def test_cli_returns_success_only_for_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.zip"
            path.write_bytes(evidence_zip())
            self.assertEqual(main([str(path)]), 0)
            path.write_bytes(b"not a zip")
            self.assertEqual(main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
