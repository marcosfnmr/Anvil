from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.log_stream import ScenarioLogHub  # noqa: E402


class ScenarioLogHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_subscribers_share_one_log_watcher(self) -> None:
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
                "containers": [
                    {"node_id": "plc-1", "name": "anvil-plc", "lines": ["ready"]}
                ],
            }

        hub = ScenarioLogHub(poll_interval=0.01, heartbeat_interval=1)
        first = await hub.subscribe("scenario-1", provider)
        second = await hub.subscribe("scenario-1", provider)

        first_event = await asyncio.wait_for(first.get(), timeout=1)
        second_event = await asyncio.wait_for(second.get(), timeout=1)
        await asyncio.wait_for(second_poll.wait(), timeout=2)

        self.assertEqual(first_event["type"], "snapshot")
        self.assertEqual(second_event["containers"][0]["lines"], ["ready"])
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(len(hub._watchers), 1)
        await hub.unsubscribe("scenario-1", first)
        await hub.unsubscribe("scenario-1", second)
        await hub.shutdown()

    async def test_provider_failure_is_broadcast(self) -> None:
        def provider() -> dict:
            raise RuntimeError("docker failed")

        hub = ScenarioLogHub(poll_interval=0.01)
        queue = await hub.subscribe("scenario-2", provider)
        event = await asyncio.wait_for(queue.get(), timeout=1)

        self.assertEqual(event["type"], "error")
        self.assertIn("docker failed", event["detail"])
        self.assertEqual(event["close_code"], 1011)
        await hub.unsubscribe("scenario-2", queue)
        await hub.shutdown()


if __name__ == "__main__":
    unittest.main()
