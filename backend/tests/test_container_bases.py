"""Ensure every first-party container uses an immutable base image by default."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = (
    "backend/Dockerfile",
    "docker_proxy/Dockerfile",
    "plc/Dockerfile",
    "plc/Dockerfile.capture",
    "plc/Dockerfile.field",
    "plc/Dockerfile.egress",
    "plc/Dockerfile.modbus",
    "plc/Dockerfile.scada",
)


class ContainerBaseImageTests(unittest.TestCase):
    def test_dockerfiles_use_digest_pinned_default_base_images(self) -> None:
        for relative_path in DOCKERFILES:
            with self.subTest(dockerfile=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                args: dict[str, str] = {}
                for name, default in re.findall(
                    r"^ARG\s+([A-Z0-9_]+)=([^\s]+)", content, re.MULTILINE
                ):
                    args[name] = default

                from_lines = re.findall(r"^FROM\s+([^\s]+)", content, re.MULTILINE)
                self.assertTrue(from_lines, "Dockerfile must declare a base image")
                for image in from_lines:
                    if image.startswith("$"):
                        image = args[image.removeprefix("${").removesuffix("}")]
                    self.assertRegex(
                        image,
                        r"@sha256:[0-9a-f]{64}$",
                        f"{relative_path} has a mutable default base image: {image}",
                    )


if __name__ == "__main__":
    unittest.main()
