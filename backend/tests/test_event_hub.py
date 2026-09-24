from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.event_stream import ScenarioEventHub  # noqa: E402


class ScenarioEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_subscribers_share_one_runtime_watcher(self) -> None:
        calls = 0

        def provider() -> dict:
            nonlocal calls
            calls += 1
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
        await asyncio.sleep(0.035)

        self.assertEqual(first_event["type"], "snapshot")
        self.assertEqual(second_event["type"], "snapshot")
        self.assertGreaterEqual(calls, 2)
        self.assertLess(calls, 8)

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
