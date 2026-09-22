from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from devices.plc_s7 import PlcS7Device  # noqa: E402
from models.scenario import DeviceInput  # noqa: E402


class PlcS7DeviceTests(unittest.TestCase):
    def test_offsets_are_calculated_and_not_required_from_user(self) -> None:
        device = DeviceInput.model_validate(
            {
                "node_id": "plc-1",
                "device_type": "plc-s7",
                "name": "PLC principal",
                "ip": "172.30.10.10",
                "config": {
                    "db_size": 32,
                    "components": [
                        {"type": "valve", "name": "v1", "db": 1},
                        {"type": "valve", "name": "v2", "db": 1},
                        {
                            "type": "analog_sensor",
                            "name": "pressure",
                            "db": 1,
                            "min_value": 0,
                            "max_value": 10,
                        },
                    ],
                },
            }
        )

        runtime = PlcS7Device().build_runtime_device(device)
        components = runtime["config"]["components"]

        self.assertEqual((components[0]["byte_offset"], components[0]["bit_offset"]), (0, 0))
        self.assertEqual((components[1]["byte_offset"], components[1]["bit_offset"]), (0, 1))
        self.assertEqual(components[2]["byte_offset"], 2)

    def test_container_receives_runtime_config_only_through_environment(self) -> None:
        device = DeviceInput.model_validate(
            {
                "node_id": "plc-1",
                "device_type": "plc-s7",
                "name": "PLC principal",
                "ip": "172.30.10.10",
                "config": {"components": [{"type": "valve", "name": "v1"}]},
            }
        )
        runtime = PlcS7Device().build_runtime_device(device)
        config = PlcS7Device().build_container_config("scenario-id", runtime)

        self.assertNotIn("ports", config)
        self.assertEqual(json.loads(config["environment"]["PLC_CONFIG"]), runtime["config"])
        self.assertEqual(config["labels"]["anvil.node_id"], "plc-1")


if __name__ == "__main__":
    unittest.main()
