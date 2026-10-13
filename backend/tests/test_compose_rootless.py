"""Keep Compose profiles configurable for a dedicated rootless Docker daemon."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.preview.yml",
    "docker-compose.demo.yml",
)


class ComposeRootlessConfigurationTests(unittest.TestCase):
    def test_compose_profiles_allow_selecting_docker_socket_and_gateway_uid(self) -> None:
        for relative_path in COMPOSE_FILES:
            with self.subTest(compose=relative_path):
                compose = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
                gateway = compose["services"]["docker-api"]
                self.assertEqual(gateway.get("user"), "${ANVIL_DOCKER_API_USER:-65534:1001}")
                self.assertIn(
                    "${ANVIL_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock",
                    gateway["volumes"],
                )
                self.assertTrue(gateway["read_only"])
                self.assertEqual(gateway["cap_drop"], ["ALL"])
                self.assertIn("no-new-privileges:true", gateway["security_opt"])

                backend = compose["services"]["backend"]
                self.assertEqual(backend["cap_drop"], ["ALL"])
                self.assertEqual(backend["cap_add"], ["CHOWN", "FOWNER", "SETGID", "SETUID"])
                self.assertEqual(backend["environment"]["ANVIL_BACKEND_UID"], "${ANVIL_BACKEND_UID:-65534}")
                self.assertEqual(backend["environment"]["ANVIL_BACKEND_GID"], "${ANVIL_BACKEND_GID:-65534}")


if __name__ == "__main__":
    unittest.main()
