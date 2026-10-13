from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT / "integration"))

from capacity_benchmark import main, scenario_payload  # noqa: E402
from models.scenario import ScenarioPayload  # noqa: E402


class CapacityBenchmarkTests(unittest.TestCase):
    def test_mixed_profile_builds_valid_s7_and_modbus_devices(self) -> None:
        payload = scenario_payload(1, 20, "172.30.240.0/24", "mixed")
        validated = ScenarioPayload.model_validate(payload)

        self.assertEqual(len(validated.devices), 20)
        self.assertEqual([item.device_type for item in validated.devices].count("plc-s7"), 10)
        self.assertEqual([item.device_type for item in validated.devices].count("modbus-tcp"), 10)

    def test_100_node_run_requires_explicit_isolated_daemon_opt_in(self) -> None:
        with patch.object(sys, "argv", ["capacity_benchmark.py", "--nodes", "100"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_large_run_requires_all_isolated_environment_identities(self) -> None:
        with patch.object(sys, "argv", [
            "capacity_benchmark.py", "--nodes", "100", "--allow-large-run",
        ]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_preflight_rejects_wrong_docker_identity_before_subnet_selection(self) -> None:
        with (
            patch.object(sys, "argv", [
                "capacity_benchmark.py", "--nodes", "20",
                "--expected-docker-server-version", "isolated-only",
            ]),
            patch("capacity_benchmark.docker.from_env") as from_env,
        ):
            from_env.return_value.info.return_value = {
                "Name": "shared-host", "ServerVersion": "29.8.1", "NCPU": 12,
                "MemTotal": 8 * 1024**3,
            }
            with patch("capacity_benchmark.request", return_value={"status": "ok"}):
                with patch("capacity_benchmark.choose_subnets") as choose_subnets:
                    self.assertEqual(main(), 2)
                    choose_subnets.assert_not_called()

    def test_preflight_failure_does_not_query_subnets_or_create_scenarios(self) -> None:
        with (
            patch.object(sys, "argv", ["capacity_benchmark.py", "--nodes", "20"]),
            patch("capacity_benchmark.docker.from_env") as from_env,
        ):
            from_env.return_value.info.side_effect = RuntimeError("daemon unavailable")
            with patch("capacity_benchmark.request") as request, \
                    patch("capacity_benchmark.choose_subnets") as choose_subnets:
                self.assertEqual(main(), 2)
                request.assert_not_called()
                choose_subnets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
