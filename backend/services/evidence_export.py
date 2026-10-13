"""Build bounded-memory, scenario-scoped ZIPs of durable operational evidence."""

from __future__ import annotations

import csv
import io
import hashlib
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlmodel import Session

from models.scada import ScenarioScadaSample
from services.audit import iter_audit_events, verify_audit_integrity
from services.evidence_integrity import MAX_EVIDENCE_UNCOMPRESSED_BYTES

EVIDENCE_SPOOL_MAX_MEMORY_BYTES = 8 * 1024 * 1024


class EvidenceBundleTooLarge(ValueError):
    """The uncompressed evidence bundle exceeds the supported export budget."""


class _EvidenceBudget:
    def __init__(self, limit: int = MAX_EVIDENCE_UNCOMPRESSED_BYTES) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, size: int) -> None:
        if size < 0 or self.used + size > self.limit:
            raise EvidenceBundleTooLarge(
                f"Evidence bundle exceeds the {self.limit // (1024 * 1024)} MiB uncompressed export limit"
            )
        self.used += size


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:80] or "node"


def _safe_csv_label(value: str) -> str:
    value = str(value or "")
    return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _new_evidence_spool() -> BinaryIO:
    """Create a bounded-memory, owner-only temporary spool for an evidence ZIP."""
    return tempfile.SpooledTemporaryFile(max_size=EVIDENCE_SPOOL_MAX_MEMORY_BYTES, mode="w+b")


def build_evidence_bundle(
    session: Session,
    scenario: Any,
    *,
    logs: dict[str, Any] | None = None,
    capture_id: str | None = None,
    pcap: bytes | None = None,
) -> BinaryIO:
    """Write audit, SCADA samples and an optional selected PCAP to a spooled ZIP."""
    if (capture_id is None) != (pcap is None):
        raise ValueError("capture_id and pcap must be supplied together")
    archive_file = _new_evidence_spool()
    entries: list[dict[str, Any]] = []
    budget = _EvidenceBudget()
    try:
        audit_integrity = verify_audit_integrity(session, scenario.id)
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            audit_name = "audit/events.jsonl"
            audit_count = 0
            with archive.open(audit_name, "w") as output:
                for event in iter_audit_events(session, scenario.id):
                    line = (event.model_dump_json() + "\n").encode("utf-8")
                    budget.consume(len(line))
                    output.write(line)
                    audit_count += 1
            entries.append({"path": audit_name, "kind": "audit_jsonl", "records": audit_count})

            if logs is not None:
                filename = "logs/containers.json"
                log_payload = (json.dumps(logs, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                budget.consume(len(log_payload))
                archive.writestr(filename, log_payload)
                entries.append({"path": filename, "kind": "container_logs", "containers": len(logs.get("containers", []))})

            for device in scenario.runtime_devices():
                if device["device_type"] != "scada":
                    continue
                node_id = device["node_id"]
                filename = f"scada/{_safe_filename(node_id)}.csv"
                statement = (
                    select(ScenarioScadaSample)
                    .where(ScenarioScadaSample.scenario_id == scenario.id,
                           ScenarioScadaSample.scada_node_id == node_id)
                    .order_by(ScenarioScadaSample.sampled_at, ScenarioScadaSample.id)
                )
                count = 0
                with archive.open(filename, "w") as raw:
                    row_buffer = io.StringIO(newline="")
                    writer = csv.writer(row_buffer)
                    header = ("timestamp", "target_node_id", "address", "name", "data_type", "unit", "value", "quality")
                    writer.writerow(header)
                    encoded = b"\xef\xbb\xbf" + row_buffer.getvalue().encode("utf-8")
                    budget.consume(len(encoded))
                    raw.write(encoded)
                    for row in session.exec(statement).scalars().yield_per(500):
                        row_buffer.seek(0)
                        row_buffer.truncate(0)
                        writer.writerow((
                            row.sampled_at, _safe_csv_label(row.target_node_id), _safe_csv_label(row.address),
                            _safe_csv_label(row.name), _safe_csv_label(row.data_type), _safe_csv_label(row.unit),
                            row.value_json, _safe_csv_label(row.quality),
                        ))
                        encoded = row_buffer.getvalue().encode("utf-8")
                        budget.consume(len(encoded))
                        raw.write(encoded)
                        count += 1
                entries.append({"path": filename, "kind": "scada_csv", "node_id": node_id, "records": count})

            if capture_id is not None and pcap is not None:
                filename = f"captures/{_safe_filename(capture_id)}.pcap"
                budget.consume(len(pcap))
                archive.writestr(filename, pcap)
                entries.append({"path": filename, "kind": "pcap", "capture_id": capture_id, "bytes": len(pcap)})

            inventory = []
            for filename in archive.namelist():
                digest = hashlib.sha256()
                size = 0
                with archive.open(filename, "r") as entry:
                    while chunk := entry.read(64 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                inventory.append({"path": filename, "bytes": size, "sha256": digest.hexdigest()})

            manifest = (json.dumps({
                "format": "anvil-scenario-evidence",
                "schema_version": 1,
                "scenario_id": scenario.id,
                "scenario_name": scenario.name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "audit_integrity": audit_integrity.model_dump(mode="json"),
                "files": entries,
                "integrity": {
                    "algorithm": "sha256",
                    "scope": "all archive entries except manifest.json",
                    "files": inventory,
                },
                "notes": [
                    "PCAP is included only when a capture_id was explicitly selected; exporting consumes that capture.",
                    "Container logs are a bounded snapshot; audit and SCADA history are durable. PCAP covers one selected node link, not a full segment.",
                ],
            }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            budget.consume(len(manifest))
            archive.writestr("manifest.json", manifest)
        archive_file.seek(0)
        return archive_file
    except Exception:
        archive_file.close()
        raise
