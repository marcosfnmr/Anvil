"""Structural tests for generated container SPDX inventories."""

from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from integration.container_sbom import make_spdx_document


class ContainerSbomTests(unittest.TestCase):
    def test_spdx_document_describes_image_base_and_observed_packages(self) -> None:
        document = make_spdx_document(
            image_ref="anvil/modbus-tcp:ci",
            image_id="sha256:" + "a" * 64,
            base_image_ref="docker.io/library/python:3.11-slim@sha256:" + "b" * 64,
            os_packages=[("libc6:amd64", "2.36-9+deb12u10")],
            python_packages=[("PyModbus", "3.11.4")],
        )

        self.assertEqual(document["spdxVersion"], "SPDX-2.3")
        self.assertEqual(len(document["documentDescribes"]), 1)
        packages = document["packages"]
        self.assertEqual(len(packages), 4)
        self.assertIn("sha256:" + "b" * 64, {package["versionInfo"] for package in packages})
        purls = {
            ref["referenceLocator"]
            for package in packages
            for ref in package.get("externalRefs", [])
        }
        self.assertTrue(any("pkg:pypi/pymodbus@3.11.4" in purl for purl in purls))
        self.assertTrue(any("pkg:deb/debian/libc6@2.36-9%2Bdeb12u10" in purl for purl in purls))
        self.assertEqual(len(document["relationships"]), 3)
        self.assertTrue(all(item["relationshipType"] == "DEPENDS_ON" for item in document["relationships"]))


if __name__ == "__main__":
    unittest.main()
