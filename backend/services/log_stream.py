"""Shared WebSocket streaming for bounded scenario container logs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool


LogProvider = Callable[[], dict[str, Any]]
LogEvent = dict[str, Any]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_payload(event_type: str, scenario_id: str, snapshot: dict[str, Any]) -> LogEvent:
    return {
        "type": event_type,
        "scenario_id": scenario_id,
        "timestamp": _timestamp(),
        "containers": snapshot.get("containers", []),
    }


def _log_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        (item.get("node_id"), item.get("name"), tuple(item.get("lines", [])))
        for item in snapshot.get("containers", [])
    )


@dataclass
class _LogWatcher:
    provider: LogProvider
    subscribers: set[asyncio.Queue[LogEvent]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    latest_snapshot: dict[str, Any] | None = None
    previous_signature: tuple[Any, ...] | None = None
    last_sent_at: float = 0.0


class ScenarioLogHub:
    """Poll Docker once per scenario and fan log snapshots out to clients."""

    def __init__(self, *, poll_interval: float = 2.0, heartbeat_interval: float = 15.0) -> None:
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self._watchers: dict[str, _LogWatcher] = {}

    async def subscribe(self, scenario_id: str, log_provider: LogProvider) -> asyncio.Queue[LogEvent]:
        queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=4)
        watcher = self._watchers.get(scenario_id)
        if watcher is None or watcher.task is None or watcher.task.done():
            watcher = _LogWatcher(provider=log_provider)
            self._watchers[scenario_id] = watcher
            watcher.task = asyncio.create_task(
                self._watch(scenario_id, watcher), name=f"anvil-logs-{scenario_id}"
            )
        watcher.subscribers.add(queue)
        if watcher.latest_snapshot is not None:
            queue.put_nowait(_log_payload("snapshot", scenario_id, watcher.latest_snapshot))
        return queue

    async def unsubscribe(self, scenario_id: str, queue: asyncio.Queue[LogEvent]) -> None:
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
        self, websocket: WebSocket, scenario_id: str, log_provider: LogProvider
    ) -> None:
        queue = await self.subscribe(scenario_id, log_provider)
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

    async def _watch(self, scenario_id: str, watcher: _LogWatcher) -> None:
        try:
            while True:
                snapshot = await run_in_threadpool(watcher.provider)
                signature = _log_signature(snapshot)
                now = monotonic()
                watcher.latest_snapshot = snapshot
                if signature != watcher.previous_signature:
                    event_type = "snapshot" if watcher.previous_signature is None else "logs"
                    self._broadcast(watcher, _log_payload(event_type, scenario_id, snapshot))
                    watcher.previous_signature = signature
                    watcher.last_sent_at = now
                elif now - watcher.last_sent_at >= self.heartbeat_interval:
                    self._broadcast(watcher, _log_payload("heartbeat", scenario_id, snapshot))
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
    def _broadcast(watcher: _LogWatcher, event: LogEvent) -> None:
        for queue in tuple(watcher.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


scenario_log_hub = ScenarioLogHub()


async def stream_scenario_logs(
    websocket: WebSocket, scenario_id: str, log_provider: LogProvider
) -> None:
    await scenario_log_hub.stream(websocket, scenario_id, log_provider)
