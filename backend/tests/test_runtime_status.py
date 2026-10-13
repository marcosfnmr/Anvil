from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.runtime_status import include_missing_runtime_nodes  # noqa: E402


class RuntimeStatusTests(unittest.TestCase):
    def test_missing_planned_device_marks_a_live_scenario_partial(self) -> None:
        runtime = {"status": "running", "containers": [
            {"node_id": "plc-2", "name": "PLC 2", "status": "running"},
        ]}
        result = include_missing_runtime_nodes(runtime, [
            {"node_id": "plc-1", "name": "PLC 1"},
            {"node_id": "plc-2", "name": "PLC 2"},
        ], "running")

        self.assertEqual(result["status"], "partial")
        missing = next(item for item in result["containers"] if item["node_id"] == "plc-1")
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["resource_metrics_status"], "missing")
        self.assertIsNone(missing["resource_metrics_sampled_at"])
        self.assertIsNotNone(missing["runtime_error"])
        self.assertEqual(runtime["containers"], [{"node_id": "plc-2", "name": "PLC 2", "status": "running"}])

    def test_all_planned_devices_missing_marks_active_scenario_error(self) -> None:
        result = include_missing_runtime_nodes(
            {"status": "stopped", "containers": []},
            [{"node_id": "plc-1", "name": "PLC 1"}],
            "running",
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["containers"][0]["status"], "missing")

    def test_stopped_plan_does_not_report_expected_containers_as_missing(self) -> None:
        runtime = {"status": "stopped", "containers": []}
        self.assertIs(include_missing_runtime_nodes(
            runtime, [{"node_id": "plc-1", "name": "PLC 1"}], "stopped"
        ), runtime)

    def test_incomplete_deploy_is_reconciled_after_deployment_returns(self) -> None:
        result = include_missing_runtime_nodes(
            {"status": "running", "containers": [
                {"node_id": "plc-1", "name": "PLC 1", "status": "running"},
            ]},
            [{"node_id": "plc-1", "name": "PLC 1"}, {"node_id": "plc-2", "name": "PLC 2"}],
            "deploying",
        )
        self.assertEqual(result["status"], "running")
        reconciled = include_missing_runtime_nodes(
            result,
            [{"node_id": "plc-1", "name": "PLC 1"}, {"node_id": "plc-2", "name": "PLC 2"}],
            "deploying",
            reconcile_deploying=True,
        )
        self.assertEqual(reconciled["status"], "partial")
        self.assertEqual(reconciled["containers"][-1]["node_id"], "plc-2")


if __name__ == "__main__":
    unittest.main()
