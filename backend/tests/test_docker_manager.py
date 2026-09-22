from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from docker.errors import APIError


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.docker_manager import DockerManager, DockerManagerError  # noqa: E402


class DockerManagerTests(unittest.TestCase):
    def test_overlapping_subnet_becomes_a_useful_domain_error(self) -> None:
        client = Mock()
        client.ping.return_value = True
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        client.networks.create.side_effect = APIError(
            "invalid pool request: Pool overlaps with other one on this address space"
        )
        manager = DockerManager(client=client)

        with self.assertRaisesRegex(
            DockerManagerError,
            "subnet overlaps an existing Docker network",
        ):
            manager.deploy("scenario-id", "172.30.100.0/24", [])

    def test_failed_container_start_removes_container_and_network(self) -> None:
        client = Mock()
        client.ping.return_value = True
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        network = client.networks.create.return_value
        container = client.containers.create.return_value
        container.start.side_effect = APIError("start failed")
        manager = DockerManager(client=client)
        runtime_device = {
            "node_id": "plc-1",
            "device_type": "plc-s7",
            "name": "PLC",
            "ip": "172.30.110.10",
            "config": {"components": []},
        }

        with self.assertRaises(DockerManagerError):
            manager.deploy("scenario-id", "172.30.110.0/24", [runtime_device])

        container.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
