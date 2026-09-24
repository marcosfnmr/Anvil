"""WebSocket event streaming for scenario runtime state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool


StatusProvider = Callable[[], dict[str, Any]]


def _event_payload(
    event_type: str,
    scenario_id: str,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": event_type,
        "scenario_id": scenario_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


async def stream_scenario_events(
    websocket: WebSocket,
    scenario_id: str,
    status_provider: StatusProvider,
    *,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
) -> None:
    """Send an initial snapshot, changes, and quiet-period heartbeats."""

    previous_signature: tuple[Any, ...] | None = None
    last_sent_at = 0.0

    try:
        while True:
            runtime = await run_in_threadpool(status_provider)
            signature = _runtime_signature(runtime)
            now = monotonic()

            if signature != previous_signature:
                event_type = "snapshot" if previous_signature is None else "status"
                await websocket.send_json(
                    _event_payload(event_type, scenario_id, runtime)
                )
                previous_signature = signature
                last_sent_at = now
            elif now - last_sent_at >= heartbeat_interval:
                await websocket.send_json(
                    _event_payload("heartbeat", scenario_id, runtime)
                )
                last_sent_at = now

            await asyncio.sleep(poll_interval)
    except (WebSocketDisconnect, RuntimeError):
        return
