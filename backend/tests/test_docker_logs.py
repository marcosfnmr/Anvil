from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.docker_manager import DockerManager  # noqa: E402


class DockerLogTests(unittest.TestCase):
    def test_logs_are_decoded_bounded_and_identified(self) -> None:
        client = Mock()
        client.ping.return_value = True
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "anvil-plc-1"
        container.logs.return_value = b"line one\nline two\n"
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)

        result = manager.logs("scenario-id", tail=25)

        self.assertEqual(result["scenario_id"], "scenario-id")
        self.assertEqual(result["containers"][0]["lines"], ["line one", "line two"])
        container.logs.assert_called_once_with(
            stdout=True, stderr=True, tail=25, timestamps=True
        )

    def test_since_cursor_uses_all_lines_for_incremental_reads(self) -> None:
        client = Mock()
        client.ping.return_value = True
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "anvil-plc-1"
        container.logs.return_value = b"line one\n"
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)

        manager.logs("scenario-id", tail=1000, since_by_node={"plc-1": 1_791_510_673.5})

        container.logs.assert_called_once_with(
            stdout=True, stderr=True, tail="all", timestamps=True,
            since=1_791_510_673.5,
        )


if __name__ == "__main__":
    unittest.main()
