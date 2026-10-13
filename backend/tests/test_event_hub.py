from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.event_stream import ScenarioEventHub, _event_payload  # noqa: E402


class ScenarioEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_encodes_docker_metric_timestamps_as_json(self) -> None:
        sampled_at = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        event = _event_payload("snapshot", "scenario", {
            "status": "running",
            "containers": [{"node_id": "plc", "resource_metrics_sampled_at": sampled_at}],
        })

        decoded = json.loads(json.dumps(event))
        self.assertEqual(decoded["containers"][0]["resource_metrics_sampled_at"], sampled_at.isoformat())

    async def test_resource_metric_changes_are_streamed_as_runtime_updates(self) -> None:
        calls = 0

        def provider() -> dict:
            nonlocal calls
            calls += 1
            return {
                "scenario_id": "scenario-metrics",
                "status": "running",
                "containers": [{"node_id": "plc", "name": "plc", "status": "running", "cpu_percent": calls}],
            }

        hub = ScenarioEventHub(poll_interval=0.01, heartbeat_interval=1)
        queue = await hub.subscribe("scenario-metrics", provider)
        snapshot = await asyncio.wait_for(queue.get(), timeout=1)
        update = await asyncio.wait_for(queue.get(), timeout=1)

        self.assertEqual(snapshot["type"], "snapshot")
        self.assertEqual(update["type"], "status")
        self.assertGreater(update["containers"][0]["cpu_percent"], snapshot["containers"][0]["cpu_percent"])
        await hub.unsubscribe("scenario-metrics", queue)
        await hub.shutdown()

    async def test_healthcheck_and_metric_quality_changes_are_streamed_immediately(self) -> None:
        calls = 0

        def provider() -> dict:
            nonlocal calls
            calls += 1
            unhealthy = calls >= 2
            unavailable = calls >= 3
            return {
                "scenario_id": "scenario-health",
                "status": "running",
                "containers": [{
                    "node_id": "plc", "name": "plc", "status": "running",
                    "health_status": "unhealthy" if unhealthy else "healthy",
                    "health_error": "S7 listener stopped" if unhealthy else None,
                    "resource_metrics_status": "unavailable" if unavailable else "good",
                    "resource_metrics_sampled_at": f"sample-{calls}",
                }],
            }

        hub = ScenarioEventHub(poll_interval=0.01, heartbeat_interval=1)
        queue = await hub.subscribe("scenario-health", provider)
        snapshot = await asyncio.wait_for(queue.get(), timeout=1)
        health_change = await asyncio.wait_for(queue.get(), timeout=1)
        metrics_change = await asyncio.wait_for(queue.get(), timeout=1)

        self.assertEqual(snapshot["type"], "snapshot")
        self.assertEqual(snapshot["containers"][0]["health_status"], "healthy")
        self.assertEqual(health_change["type"], "status")
        self.assertEqual(health_change["containers"][0]["health_error"], "S7 listener stopped")
        self.assertEqual(metrics_change["type"], "status")
        self.assertEqual(metrics_change["containers"][0]["resource_metrics_status"], "unavailable")
        await hub.unsubscribe("scenario-health", queue)
        await hub.shutdown()

    async def test_multiple_subscribers_share_one_runtime_watcher(self) -> None:
        calls = 0
        loop = asyncio.get_running_loop()
        second_poll = asyncio.Event()

        def provider() -> dict:
            nonlocal calls
            calls += 1
            if calls == 2:
                loop.call_soon_threadsafe(second_poll.set)
            return {
                "scenario_id": "scenario-1",
                "status": "stopped",
                "containers": [],
            }

        hub = ScenarioEventHub(poll_interval=0.01, heartbeat_interval=1)
        first = await hub.subscribe("scenario-1", provider)
        second = await hub.subscribe("scenario-1", provider)

        first_event = await asyncio.wait_for(first.get(), timeout=1)
        second_event = await asyncio.wait_for(second.get(), timeout=1)
        await asyncio.wait_for(second_poll.wait(), timeout=2)

        self.assertEqual(first_event["type"], "snapshot")
        self.assertEqual(second_event["type"], "snapshot")
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(len(hub._watchers), 1)

        await hub.unsubscribe("scenario-1", first)
        await hub.unsubscribe("scenario-1", second)
        await hub.shutdown()

    async def test_provider_failure_is_broadcast_as_an_error(self) -> None:
        def provider() -> dict:
            raise LookupError("scenario disappeared")

        hub = ScenarioEventHub(poll_interval=0.01)
        queue = await hub.subscribe("scenario-2", provider)

        event = await asyncio.wait_for(queue.get(), timeout=1)

        self.assertEqual(event["type"], "error")
        self.assertEqual(event["close_code"], 4404)
        await hub.unsubscribe("scenario-2", queue)
        await hub.shutdown()
