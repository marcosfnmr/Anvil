"""Persist bounded SCADA samples outside short-lived runtime containers."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from models.scada import ScenarioScadaAlarmPending, ScenarioScadaAlarmState, ScenarioScadaSample
from models.scenario import ScenarioRecord
from services.docker_manager import DockerManager
from services.scada_operations import scada_operation
from services.audit import record_protocol_read_transition


LOGGER = logging.getLogger("anvil.scada_historian")
_DEFAULT_INTERVAL_SECONDS = 5.0
_MAX_ROWS_PER_SCENARIO = 1_000_000


def persist_scada_alarms(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    alarms: list[dict[str, Any]],
    *,
    ready: bool = True,
) -> int:
    """Persist active alarm state and clear it once the source reports normal."""
    if not ready:
        return session.execute(select(func.count()).select_from(ScenarioScadaAlarmState).where(
            ScenarioScadaAlarmState.scenario_id == scenario_id,
            ScenarioScadaAlarmState.scada_node_id == scada_node_id,
        )).scalar_one()
    incoming: dict[tuple[str, str], dict[str, Any]] = {}
    pending: dict[tuple[str, str], str] = {}
    preserve_pending: set[tuple[str, str]] = set()
    unknown_keys: set[tuple[str, str]] = set()
    for alarm in alarms:
        if not isinstance(alarm, dict):
            continue
        target, address = alarm.get("target_node_id"), alarm.get("address")
        if not isinstance(target, str) or not isinstance(address, str):
            continue
        quality = alarm.get("quality")
        if quality is None:
            quality = "good" if alarm.get("active") is True or alarm.get("value") is not None else "unknown"
        if quality != "good":
            unknown_keys.add((target, address))
            if quality == "unknown" and alarm.get("pending") is True:
                condition_since = _validated_timestamp(alarm.get("condition_since"))
                if condition_since is not None:
                    pending[(target, address)] = condition_since
                    preserve_pending.add((target, address))
            continue
        condition_since = _validated_timestamp(alarm.get("condition_since"))
        if alarm.get("pending") is True and condition_since is not None:
            pending[(target, address)] = condition_since
        if alarm.get("active") is not True:
            continue
        triggered_at = _validated_timestamp(alarm.get("triggered_at"))
        if (not isinstance(target, str) or not target or len(target) > 128
                or not isinstance(address, str) or not address or len(address) > 128
                or triggered_at is None):
            continue
        incoming[(target, address)] = alarm

    existing = session.execute(select(ScenarioScadaAlarmState).where(
        ScenarioScadaAlarmState.scenario_id == scenario_id,
        ScenarioScadaAlarmState.scada_node_id == scada_node_id,
    )).scalars().all()
    active_count = len(incoming)
    for row in existing:
        key = (row.target_node_id, row.address)
        if key not in incoming and key not in unknown_keys:
            session.delete(row)
    for (target, address), alarm in incoming.items():
        statement = sqlite_insert(ScenarioScadaAlarmState).values(
            scenario_id=scenario_id,
            scada_node_id=scada_node_id,
            target_node_id=target,
            address=address,
            triggered_at=_validated_timestamp(alarm.get("triggered_at")) or alarm["triggered_at"],
            acknowledged=alarm.get("acknowledged") is True,
            acknowledged_at=_validated_timestamp(alarm.get("acknowledged_at")),
        )
        session.execute(statement.on_conflict_do_update(
            index_elements=["scenario_id", "scada_node_id", "target_node_id", "address"],
            set_={
                "acknowledged": or_(ScenarioScadaAlarmState.acknowledged, statement.excluded.acknowledged),
                "acknowledged_at": func.coalesce(
                    ScenarioScadaAlarmState.acknowledged_at, statement.excluded.acknowledged_at,
                ),
            },
        ))
    session.commit()
    # A bad-quality sample resets a pending delay, matching the collector's
    # evaluation rule. Active alarms remain preserved by ScenarioScadaAlarmState.
    persist_scada_alarm_pending(
        session, scenario_id, scada_node_id, pending, preserve_keys=preserve_pending,
    )
    return active_count


def persist_scada_alarm_pending(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    pending: dict[tuple[str, str], str],
    *,
    preserve_keys: set[tuple[str, str]] | None = None,
) -> None:
    """Replace pending starts, retaining restored values until the first sample."""
    preserve = preserve_keys or set()
    existing = session.execute(select(ScenarioScadaAlarmPending).where(
        ScenarioScadaAlarmPending.scenario_id == scenario_id,
        ScenarioScadaAlarmPending.scada_node_id == scada_node_id,
    )).scalars().all()
    by_key = {(row.target_node_id, row.address): row for row in existing}
    for key, row in by_key.items():
        if key not in pending and key not in preserve:
            session.delete(row)
    for (target, address), condition_since in pending.items():
        statement = sqlite_insert(ScenarioScadaAlarmPending).values(
            scenario_id=scenario_id, scada_node_id=scada_node_id,
            target_node_id=target, address=address, condition_since=condition_since,
        )
        session.execute(statement.on_conflict_do_update(
            index_elements=["scenario_id", "scada_node_id", "target_node_id", "address"],
            set_={"condition_since": statement.excluded.condition_since},
        ))
    session.commit()


def read_scada_alarm_pending(
    session: Session, scenario_id: str, scada_node_id: str,
) -> list[dict[str, str]]:
    rows = session.execute(select(ScenarioScadaAlarmPending).where(
        ScenarioScadaAlarmPending.scenario_id == scenario_id,
        ScenarioScadaAlarmPending.scada_node_id == scada_node_id,
    )).scalars().all()
    return [{"target_node_id": row.target_node_id, "address": row.address,
             "condition_since": row.condition_since} for row in rows]


def persist_scada_alarm_acknowledgement(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    alarm: dict[str, Any],
) -> None:
    """Persist a successful runtime acknowledgement immediately."""
    target, address = alarm.get("target_node_id"), alarm.get("address")
    if not isinstance(target, str) or not target or not isinstance(address, str) or not address:
        return
    triggered_at = _validated_timestamp(alarm.get("triggered_at"))
    acknowledged_at = _validated_timestamp(alarm.get("acknowledged_at")) or datetime.now(timezone.utc).isoformat()
    statement = sqlite_insert(ScenarioScadaAlarmState).values(
        scenario_id=scenario_id,
        scada_node_id=scada_node_id,
        target_node_id=target[:128],
        address=address[:128],
        triggered_at=triggered_at or acknowledged_at,
        acknowledged=True,
        acknowledged_at=acknowledged_at,
    )
    session.execute(statement.on_conflict_do_update(
        index_elements=["scenario_id", "scada_node_id", "target_node_id", "address"],
        set_={"acknowledged": True, "acknowledged_at": statement.excluded.acknowledged_at},
    ))
    session.commit()


def merge_persisted_scada_alarms(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    alarms: list[dict[str, Any]],
    *,
    ready: bool = True,
) -> list[dict[str, Any]]:
    states = session.execute(select(ScenarioScadaAlarmState).where(
        ScenarioScadaAlarmState.scenario_id == scenario_id,
        ScenarioScadaAlarmState.scada_node_id == scada_node_id,
    )).scalars().all()
    by_key = {(item.target_node_id, item.address): item for item in states}
    seen: set[tuple[Any, Any]] = set()
    merged = []
    for alarm in alarms:
        key = (alarm.get("target_node_id"), alarm.get("address"))
        seen.add(key)
        state = by_key.get(key)
        if state is not None:
            quality = alarm.get("quality", "good")
            if alarm.get("active") is True and quality == "good":
                alarm = {**alarm, "triggered_at": state.triggered_at,
                         "acknowledged": state.acknowledged or alarm.get("acknowledged") is True,
                         "acknowledged_at": state.acknowledged_at or alarm.get("acknowledged_at")}
            elif not ready or quality != "good":
                alarm = {**alarm, "active": True, "value": None, "stale": True,
                         "triggered_at": state.triggered_at, "acknowledged": state.acknowledged,
                         "acknowledged_at": state.acknowledged_at}
        merged.append(alarm)
    if not ready:
        for key, state in by_key.items():
            if key not in seen:
                merged.append({"target_node_id": state.target_node_id, "address": state.address,
                               "active": True, "value": None, "quality": "unknown", "stale": True,
                               "triggered_at": state.triggered_at, "acknowledged": state.acknowledged,
                               "acknowledged_at": state.acknowledged_at})
    return merged


def _validated_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def persist_scada_samples(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    samples: list[dict[str, Any]],
) -> int:
    """Insert valid observations once; return the number of new point rows."""
    rows: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sampled_at = _validated_timestamp(sample.get("timestamp"))
        values = sample.get("values")
        if sampled_at is None or not isinstance(values, list):
            continue
        for point in values:
            if not isinstance(point, dict):
                continue
            target = point.get("node_id")
            address = point.get("address")
            value = point.get("value")
            if (not isinstance(target, str) or not target or len(target) > 128
                    or not isinstance(address, str) or not address or len(address) > 128
                    or (type(value) not in (bool, int, float) and value is not None)):
                continue
            try:
                value_json = json.dumps(value, allow_nan=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            if len(value_json) > 128:
                continue
            quality = point.get("quality", "unknown")
            if not isinstance(quality, str):
                quality = "unknown"
            rows.append({
                "scenario_id": scenario_id,
                "scada_node_id": scada_node_id,
                "sampled_at": sampled_at,
                "target_node_id": target,
                "address": address,
                "name": str(point.get("name") or "")[:256],
                "data_type": str(point.get("data_type") or "")[:16],
                "unit": str(point.get("unit") or "")[:32],
                "value_json": value_json,
                "quality": quality[:16],
            })
    if not rows:
        return 0

    result = session.exec(
        sqlite_insert(ScenarioScadaSample)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[
            "scenario_id", "scada_node_id", "sampled_at", "target_node_id", "address",
        ])
    )
    inserted = max(0, int(result.rowcount or 0))
    if inserted:
        count = session.execute(
            select(func.count()).select_from(ScenarioScadaSample)
            .where(ScenarioScadaSample.scenario_id == scenario_id)
        ).scalar_one()
        if count > _MAX_ROWS_PER_SCENARIO:
            excess = count - _MAX_ROWS_PER_SCENARIO
            oldest_ids = select(ScenarioScadaSample.id).where(
                ScenarioScadaSample.scenario_id == scenario_id
            ).order_by(ScenarioScadaSample.sampled_at, ScenarioScadaSample.id).limit(excess)
            session.exec(delete(ScenarioScadaSample).where(ScenarioScadaSample.id.in_(oldest_ids)))
    session.commit()
    return inserted


def read_scada_samples(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    sample_times = session.execute(
        select(ScenarioScadaSample.sampled_at)
        .where(ScenarioScadaSample.scenario_id == scenario_id,
               ScenarioScadaSample.scada_node_id == scada_node_id)
        .distinct()
        .order_by(ScenarioScadaSample.sampled_at.desc())
        .limit(max(1, min(int(limit), 1000)))
    ).scalars().all()
    if not sample_times:
        return []
    rows = session.execute(
        select(ScenarioScadaSample)
        .where(ScenarioScadaSample.scenario_id == scenario_id,
               ScenarioScadaSample.scada_node_id == scada_node_id,
               ScenarioScadaSample.sampled_at.in_(sample_times))
        .order_by(ScenarioScadaSample.sampled_at, ScenarioScadaSample.id)
    ).scalars().all()
    grouped: dict[str, dict[str, Any]] = {}
    for row in reversed(rows):
        sample = grouped.setdefault(row.sampled_at, {"timestamp": row.sampled_at, "values": []})
        sample["values"].append({
            "node_id": row.target_node_id,
            "address": row.address,
            "name": row.name,
            "data_type": row.data_type,
            "unit": row.unit,
            "value": json.loads(row.value_json),
            "quality": row.quality,
            "timestamp": row.sampled_at,
        })
    return list(grouped.values())


def read_scada_aggregates(
    session: Session,
    scenario_id: str,
    scada_node_id: str,
    start: datetime,
    end: datetime,
    bucket_seconds: int,
    target_node_ids: set[str],
    *,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Aggregate persisted points into bounded time buckets without treating bad reads as values."""
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    start_text, end_text = start_utc.isoformat(), end_utc.isoformat()
    query = (select(ScenarioScadaSample)
             .where(ScenarioScadaSample.scenario_id == scenario_id,
                    ScenarioScadaSample.scada_node_id == scada_node_id,
                    ScenarioScadaSample.target_node_id.in_(target_node_ids),
                    ScenarioScadaSample.sampled_at >= start_text,
                    ScenarioScadaSample.sampled_at < end_text)
             .order_by(ScenarioScadaSample.sampled_at, ScenarioScadaSample.id)
             .limit(max_rows + 1))
    rows = session.execute(query).scalars().all()
    if len(rows) > max_rows:
        raise ValueError(f"aggregation range contains more than {max_rows} point observations; narrow the time range")
    width = max(1, int(bucket_seconds))
    series: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.target_node_id not in target_node_ids:
            continue
        try:
            timestamp = datetime.fromisoformat(row.sampled_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            value = json.loads(row.value_json)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        elapsed = max(0.0, (timestamp - start_utc).total_seconds())
        bucket_index = int(elapsed // width)
        bucket_start = start_utc + timedelta(seconds=bucket_index * width)
        key = (row.target_node_id, row.address)
        item = series.setdefault(key, {
            "node_id": row.target_node_id, "address": row.address, "name": row.name,
            "data_type": row.data_type, "unit": row.unit, "buckets": {},
        })
        bucket = item["buckets"].setdefault(bucket_index, {
            "timestamp": bucket_start.isoformat(), "samples": 0, "good_samples": 0,
            "bad_samples": 0, "min": None, "max": None, "average": None,
            "last_value": None, "quality": row.quality, "_numeric": [],
            "_true_count": 0, "_false_count": 0,
        })
        bucket["samples"] += 1
        bucket["last_value"] = value
        bucket["quality"] = row.quality
        if row.quality != "good":
            bucket["bad_samples"] += 1
            continue
        bucket["good_samples"] += 1
        if type(value) is bool:
            bucket["_true_count" if value else "_false_count"] += 1
        elif type(value) in (int, float):
            try:
                numeric_value = float(value)
            except OverflowError:
                continue
            if math.isfinite(numeric_value):
                bucket["_numeric"].append(numeric_value)
    output_series = []
    for item in series.values():
        buckets = []
        for bucket in item["buckets"].values():
            numeric = bucket.pop("_numeric")
            bucket["true_samples"] = bucket.pop("_true_count")
            bucket["false_samples"] = bucket.pop("_false_count")
            if numeric:
                bucket["min"] = min(numeric)
                bucket["max"] = max(numeric)
                bucket["average"] = sum(numeric) / len(numeric)
            buckets.append(bucket)
        item["buckets"] = buckets
        output_series.append(item)
    return {
        "start": start_text, "end": end_text, "bucket_seconds": width,
        "observations": len(rows), "series": output_series,
    }


def collect_scada_history_once(database_engine: Any, manager: Any | None = None) -> int:
    """Fetch recent runtime samples for every deployed SCADA and persist them."""
    targets: list[tuple[str, str, int, dict[str, str]]] = []
    with Session(database_engine) as session:
        scenarios = session.execute(
            select(ScenarioRecord).where(ScenarioRecord.status.in_(("running", "partial")))
        ).scalars().all()
        for scenario in scenarios:
            runtime_devices = scenario.runtime_devices()
            devices_by_id = {device["node_id"]: device for device in runtime_devices}
            for device in runtime_devices:
                if device.get("device_type") != "scada":
                    continue
                selected = {item.strip() for item in device.get("config", {}).get("target_node_ids", "").split(",") if item.strip()}
                protocols = {
                    target_id: "modbus-tcp" if devices_by_id.get(target_id, {}).get("device_type") == "modbus-tcp" else "s7"
                    for target_id in selected
                    if devices_by_id.get(target_id, {}).get("device_type") in {"plc-s7", "modbus-tcp"}
                }
                targets.append((
                    scenario.id,
                    device["node_id"],
                    max(1, min(10_000, int(device.get("config", {}).get("history_capacity", 600)))),
                    protocols,
                ))
    if not targets:
        return 0

    owns_manager = manager is None
    docker_manager = manager or DockerManager()
    inserted = 0
    try:
        for scenario_id, node_id, capacity, protocols in targets:
            buffer_cursor: int | None = None
            try:
                try:
                    history = scada_operation(
                        docker_manager, scenario_id, node_id, "history/pending", {"limit": 10_000}
                    )
                    if type(history.get("cursor")) is int:
                        buffer_cursor = history["cursor"]
                except ValueError as error:
                    if "not found" not in str(error).casefold():
                        raise
                    # Older pinned SCADA images expose only the bounded in-memory history API.
                    history = scada_operation(
                        docker_manager, scenario_id, node_id, "history", {"limit": capacity}
                    )
            except Exception as error:
                LOGGER.debug("SCADA sample collection failed for %s/%s: %s",
                             scenario_id, node_id, error)
                try:
                    with Session(database_engine) as session:
                        for target_id, protocol in protocols.items():
                            record_protocol_read_transition(
                                session, scenario_id, node_id, target_id, protocol, "failed", 0
                            )
                except Exception:
                    LOGGER.exception("Could not persist SCADA read failure for %s/%s", scenario_id, node_id)
                continue

            samples = history.get("samples", [])
            with Session(database_engine) as session:
                inserted += persist_scada_samples(session, scenario_id, node_id, samples)
                latest_by_point: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
                for sample in samples:
                    if not isinstance(sample, dict):
                        continue
                    timestamp = str(sample.get("timestamp") or "")
                    for value in sample.get("values", []):
                        if not isinstance(value, dict) or value.get("node_id") not in protocols:
                            continue
                        key = (value["node_id"], str(value.get("address", "")))
                        if key not in latest_by_point or timestamp >= latest_by_point[key][0]:
                            latest_by_point[key] = (timestamp, value)
                latest_by_target: dict[str, list[dict[str, Any]]] = {}
                for (target_id, _address), (_timestamp, value) in latest_by_point.items():
                    latest_by_target.setdefault(target_id, []).append(value)
                for target_id, protocol in protocols.items():
                    values = latest_by_target.get(target_id, [])
                    quality = "good"
                    if not values or any(str(value.get("quality", "unknown")).lower() != "good" for value in values):
                        quality = "degraded"
                    record_protocol_read_transition(
                        session, scenario_id, node_id, target_id, protocol, quality, len(values)
                    )
            if buffer_cursor is not None:
                try:
                    scada_operation(docker_manager, scenario_id, node_id, "history/acknowledge", {"cursor": buffer_cursor})
                except Exception as error:
                    # Samples are deduplicated in SQLite, so a failed ack is safe to retry next cycle.
                    LOGGER.warning("SCADA history acknowledgement failed for %s/%s at %s: %s",
                                   scenario_id, node_id, buffer_cursor, error)
            try:
                alarm_result = scada_operation(docker_manager, scenario_id, node_id, "alarms")
                with Session(database_engine) as session:
                    persist_scada_alarms(session, scenario_id, node_id,
                                         alarm_result.get("alarms", []), ready=alarm_result.get("ready", True))
            except Exception as error:
                LOGGER.debug("SCADA alarm collection failed for %s/%s: %s",
                             scenario_id, node_id, error)
    finally:
        if owns_manager:
            docker_manager.client.close()
    return inserted


async def scada_historian_scheduler(
    stop_event: asyncio.Event,
    database_engine: Any,
    interval_seconds: float | None = None,
) -> None:
    interval = interval_seconds or float(os.environ.get(
        "ANVIL_SCADA_HISTORIAN_INTERVAL_SECONDS", _DEFAULT_INTERVAL_SECONDS
    ))
    interval = max(1.0, min(interval, 60.0))
    while not stop_event.is_set():
        try:
            await run_in_threadpool(collect_scada_history_once, database_engine)
        except Exception:
            LOGGER.exception("SCADA historian collection cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
