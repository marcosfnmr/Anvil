"""Shared WebSocket event streaming for scenario runtime state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool


StatusProvider = Callable[[], dict[str, Any]]
Event = dict[str, Any]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_payload(
    event_type: str,
    scenario_id: str,
    runtime: dict[str, Any],
) -> Event:
    return {
        "type": event_type,
        "scenario_id": scenario_id,
        "timestamp": _timestamp(),
        "status": runtime["status"],
        "containers": runtime.get("containers", []),
    }


def _runtime_signature(runtime: dict[str, Any]) -> tuple[Any, ...]:
    containers = tuple(
        sorted(
            (
                item.get("node_id"),
                item.get("name"),
                item.get("status"),
            )
            for item in runtime.get("containers", [])
        )
    )
    return runtime.get("status"), containers


@dataclass
class _ScenarioWatcher:
    provider: StatusProvider
    subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    latest_runtime: dict[str, Any] | None = None
    previous_signature: tuple[Any, ...] | None = None
    last_sent_at: float = 0.0


class ScenarioEventHub:
    """Poll each scenario once and fan state changes out to all clients."""

    def __init__(
        self,
        *,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self._watchers: dict[str, _ScenarioWatcher] = {}

    async def subscribe(
        self,
        scenario_id: str,
        status_provider: StatusProvider,
    ) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=8)
        watcher = self._watchers.get(scenario_id)

        if watcher is None or watcher.task is None or watcher.task.done():
            watcher = _ScenarioWatcher(provider=status_provider)
            self._watchers[scenario_id] = watcher
            watcher.task = asyncio.create_task(
                self._watch(scenario_id, watcher),
                name=f"anvil-events-{scenario_id}",
            )

        watcher.subscribers.add(queue)
        if watcher.latest_runtime is not None:
            queue.put_nowait(
                _event_payload("snapshot", scenario_id, watcher.latest_runtime)
            )
        return queue

    async def unsubscribe(
        self,
        scenario_id: str,
        queue: asyncio.Queue[Event],
    ) -> None:
        watcher = self._watchers.get(scenario_id)
        if watcher is None:
            return
        watcher.subscribers.discard(queue)
        if watcher.subscribers:
            return
        if watcher.task is not None:
            watcher.task.cancel()
        self._watchers.pop(scenario_id, None)

    async def stream(
        self,
        websocket: WebSocket,
        scenario_id: str,
        status_provider: StatusProvider,
    ) -> None:
        queue = await self.subscribe(scenario_id, status_provider)
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if event["type"] == "error":
                    await websocket.close(code=event.get("close_code", 1011))
                    return
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            await self.unsubscribe(scenario_id, queue)

    async def shutdown(self) -> None:
        watchers = list(self._watchers.values())
        self._watchers.clear()
        for watcher in watchers:
            if watcher.task is not None:
                watcher.task.cancel()
        if watchers:
            await asyncio.gather(
                *(watcher.task for watcher in watchers if watcher.task is not None),
                return_exceptions=True,
            )

    async def _watch(
        self,
        scenario_id: str,
        watcher: _ScenarioWatcher,
    ) -> None:
        try:
            while True:
                runtime = await run_in_threadpool(watcher.provider)
                signature = _runtime_signature(runtime)
                now = monotonic()
                watcher.latest_runtime = runtime

                if signature != watcher.previous_signature:
                    event_type = (
                        "snapshot" if watcher.previous_signature is None else "status"
                    )
                    self._broadcast(
                        watcher,
                        _event_payload(event_type, scenario_id, runtime),
                    )
                    watcher.previous_signature = signature
                    watcher.last_sent_at = now
                elif now - watcher.last_sent_at >= self.heartbeat_interval:
                    self._broadcast(
                        watcher,
                        _event_payload("heartbeat", scenario_id, runtime),
                    )
                    watcher.last_sent_at = now

                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._broadcast(
                watcher,
                {
                    "type": "error",
                    "scenario_id": scenario_id,
                    "timestamp": _timestamp(),
                    "detail": str(error),
                    "close_code": 4404 if isinstance(error, LookupError) else 1011,
                },
            )

    @staticmethod
    def _broadcast(watcher: _ScenarioWatcher, event: Event) -> None:
        for queue in tuple(watcher.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


scenario_event_hub = ScenarioEventHub()


async def stream_scenario_events(
    websocket: WebSocket,
    scenario_id: str,
    status_provider: StatusProvider,
) -> None:
    """Stream shared scenario events to one WebSocket connection."""

    await scenario_event_hub.stream(websocket, scenario_id, status_provider)
