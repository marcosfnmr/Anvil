"""Scenario persistence and topology normalization."""

from __future__ import annotations

import json
import hashlib
import re
from ipaddress import ip_network

from sqlmodel import Session, delete, select

from devices import DEVICE_REGISTRY
from models.scenario import ScenarioCreate, ScenarioRead, ScenarioRecord, ScenarioUpdate, ScenarioOperationLease
from models.audit import ScenarioAuditArchive, ScenarioFieldAuditState, ScenarioNodeRuntimeState, ScenarioProtocolTraceCursor
from models.revision import ScenarioRevision, ScenarioRevisionDetail, ScenarioRevisionRead
from models.auth import ScenarioMembership, TerminalSessionLease
from models.exercise import ExerciseRun
from models.scada import ScenarioScadaAlarmPending, ScenarioScadaAlarmState, ScenarioScadaSample
from services.audit import record_audit_event


class ScenarioNotFoundError(LookupError):
    pass


class ScenarioConflictError(RuntimeError):
    pass


class ScenarioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, payload: ScenarioCreate, *, owner_user_id: str | None = None) -> ScenarioRead:
        runtime_devices = self._build_runtime_devices(payload)
        record = ScenarioRecord(
            name=payload.name,
            network_json=json.dumps(payload.network.model_dump(mode="json")),
            devices_json=json.dumps(
                [device.model_dump(mode="json") for device in payload.devices]
            ),
            runtime_devices_json=json.dumps(runtime_devices),
            canvas_json=json.dumps(payload.canvas),
            field_objects_json=json.dumps([item.model_dump(mode="json") for item in payload.field_objects]),
            signal_connections_json=json.dumps([item.model_dump(mode="json") for item in payload.signal_connections]),
            process_connections_json=json.dumps([item.model_dump(mode="json") for item in payload.process_connections]),
            network_routes_json=json.dumps([item.model_dump(mode="json") for item in payload.network_routes]),
            network_connections_json=json.dumps([item.model_dump(mode="json") for item in payload.network_connections]),
            owner_user_id=owner_user_id,
        )
        self.session.add(record)
        self._append_revision(record.id, self._snapshot(payload))
        self.session.commit()
        self.session.refresh(record)
        record_audit_event(self.session, record.id, "scenario.created", {
            "name": record.name,
            "device_count": len(payload.devices),
            "field_object_count": len(payload.field_objects),
        }, actor="api-client")
        return record.to_read_model()

    def list(self, *, user_id: str | None = None, admin: bool = False) -> list[ScenarioRead]:
        statement = select(ScenarioRecord)
        memberships_by_scenario: dict[str, str] = {}
        if user_id is not None and not admin:
            memberships = select(ScenarioMembership.scenario_id).where(ScenarioMembership.user_id == user_id)
            statement = statement.where((ScenarioRecord.owner_user_id == user_id) | ScenarioRecord.id.in_(memberships))
            memberships_by_scenario = {
                item.scenario_id: item.role
                for item in self.session.exec(select(ScenarioMembership).where(ScenarioMembership.user_id == user_id)).all()
            }
        records = self.session.exec(statement.order_by(ScenarioRecord.created_at.desc())).all()
        result: list[ScenarioRead] = []
        for record in records:
            scenario = record.to_read_model()
            access_role = "admin" if admin else "owner" if record.owner_user_id == user_id else memberships_by_scenario.get(record.id)
            result.append(scenario.model_copy(update={"access_role": access_role}))
        return result

    def get_record(self, scenario_id: str) -> ScenarioRecord:
        record = self.session.get(ScenarioRecord, scenario_id)
        if record is None:
            raise ScenarioNotFoundError(f"Scenario '{scenario_id}' was not found")
        return record

    def get(self, scenario_id: str) -> ScenarioRead:
        return self.get_record(scenario_id).to_read_model()

    def network_route_endpoints(self, scenario_id: str) -> list[dict]:
        record = self.get_record(scenario_id)
        by_id = {item["node_id"]: item for item in record.runtime_devices()}
        result = []
        for route in record.to_read_model().network_routes:
            source = by_id.get(route.source_node_id, {})
            config = source.get("config", {})
            endpoint = (config.get("tcp_routes", {}).get(route.route_id) if route.protocol == "tcp"
                        else config.get("network_routes", {}).get(route.target_node_id))
            result.append({
                "route_id": route.route_id, "protocol": route.protocol,
                "source_node_id": route.source_node_id, "target_node_id": route.target_node_id,
                "latency_ms": route.latency_ms,
                "target_port": route.target_port if route.protocol == "tcp" else
                               102 if route.protocol == "s7" else
                               int(by_id.get(route.target_node_id, {}).get("config", {}).get("port", 502)),
                "enabled": route.enabled,
                "listen_host": endpoint.get("host") if endpoint else None,
                "listen_port": endpoint.get("port", route.listen_port) if endpoint else route.listen_port,
            })
        return result

    def list_revisions(self, scenario_id: str) -> list[ScenarioRevisionRead]:
        self.get_record(scenario_id)
        records = self.session.exec(
            select(ScenarioRevision)
            .where(ScenarioRevision.scenario_id == scenario_id)
            .order_by(ScenarioRevision.version.desc())
        ).all()
        return [record.to_read_model() for record in records]

    def get_revision(self, scenario_id: str, revision_id: int) -> ScenarioRevisionDetail:
        self.get_record(scenario_id)
        record = self.session.get(ScenarioRevision, revision_id)
        if record is None or record.scenario_id != scenario_id:
            raise ScenarioNotFoundError(f"Revision '{revision_id}' was not found for this scenario")
        summary = record.to_read_model()
        return ScenarioRevisionDetail(
            **summary.model_dump(),
            snapshot=json.loads(record.snapshot_json),
        )

    def restore_revision(self, scenario_id: str, revision_id: int) -> ScenarioRead:
        detail = self.get_revision(scenario_id, revision_id)
        payload = ScenarioUpdate.model_validate(detail.snapshot)
        restored = self.update(scenario_id, payload)
        latest = self.session.exec(
            select(ScenarioRevision)
            .where(ScenarioRevision.scenario_id == scenario_id)
            .order_by(ScenarioRevision.version.desc())
        ).first()
        record_audit_event(self.session, scenario_id, "scenario.revision_restored", {
            "revision_id": revision_id,
            "restored_from_version": detail.version,
            "created_version": latest.version if latest else None,
        }, actor="api-client")
        return restored

    def clone(self, scenario_id: str, name: str | None = None, *, owner_user_id: str | None = None) -> ScenarioRead:
        source = self.get_record(scenario_id)
        model = source.to_read_model()
        payload = ScenarioCreate.model_validate({
            "name": name or f"{model.name} (copia)",
            "network": model.network.model_dump(mode="json"),
            "devices": [device.model_dump(mode="json") for device in model.devices],
            "canvas": model.canvas,
            "field_objects": [item.model_dump(mode="json") for item in model.field_objects],
            "signal_connections": [item.model_dump(mode="json") for item in model.signal_connections],
            "process_connections": [item.model_dump(mode="json") for item in model.process_connections],
            "network_routes": [item.model_dump(mode="json") for item in model.network_routes],
            "network_connections": [item.model_dump(mode="json") for item in model.network_connections],
        })
        return self.create(payload, owner_user_id=owner_user_id)

    def clone_with_field_migration(self, scenario_id: str, name: str | None = None,
                                   *, owner_user_id: str | None = None) -> ScenarioRead:
        source = self.get_record(scenario_id)
        scenario = source.to_read_model()
        from services.field_migration import build_field_migration_copy

        payload = build_field_migration_copy(
            scenario, source.runtime_devices(), name=name,
        )
        return self.create(payload, owner_user_id=owner_user_id)

    def update(self, scenario_id: str, payload: ScenarioUpdate) -> ScenarioRead:
        record = self.get_record(scenario_id)
        if record.status != "stopped":
            raise ScenarioConflictError("Stop the scenario before updating its topology")

        # Older clients omit these fields. Preserve the stored topology rather
        # than silently deleting independent objects when saving legacy forms.
        existing = record.to_read_model()
        if "zones" not in payload.network.model_fields_set:
            payload.network.zones = existing.network.zones
        if "egress_rules" not in payload.network.model_fields_set:
            payload.network.egress_rules = existing.network.egress_rules
        previous_devices = {item.node_id: item for item in existing.devices}
        for device in payload.devices:
            previous = previous_devices.get(device.node_id)
            if previous is not None:
                if "zone_id" not in device.model_fields_set:
                    device.zone_id = previous.zone_id
                if "hostname" not in device.model_fields_set:
                    device.hostname = previous.hostname
        previous_fields = {item.object_id: item for item in existing.field_objects}
        for item in payload.field_objects:
            if "zone_id" not in item.model_fields_set and item.object_id in previous_fields:
                item.zone_id = previous_fields[item.object_id].zone_id
        if "field_objects" not in payload.model_fields_set:
            payload.field_objects = existing.field_objects
        if "signal_connections" not in payload.model_fields_set:
            payload.signal_connections = existing.signal_connections
        if "process_connections" not in payload.model_fields_set:
            payload.process_connections = existing.process_connections
        if "network_routes" not in payload.model_fields_set:
            payload.network_routes = existing.network_routes
        if "network_connections" not in payload.model_fields_set:
            payload.network_connections = existing.network_connections
        payload = ScenarioUpdate.model_validate(payload.model_dump(mode="json"))
        runtime_devices = self._build_runtime_devices(payload)
        record.name = payload.name
        record.network_json = json.dumps(payload.network.model_dump(mode="json"))
        record.devices_json = json.dumps(
            [device.model_dump(mode="json") for device in payload.devices]
        )
        record.runtime_devices_json = json.dumps(runtime_devices)
        record.canvas_json = json.dumps(payload.canvas)
        record.field_objects_json = json.dumps([item.model_dump(mode="json") for item in payload.field_objects])
        record.signal_connections_json = json.dumps([item.model_dump(mode="json") for item in payload.signal_connections])
        record.process_connections_json = json.dumps([item.model_dump(mode="json") for item in payload.process_connections])
        record.network_routes_json = json.dumps([item.model_dump(mode="json") for item in payload.network_routes])
        record.network_connections_json = json.dumps([item.model_dump(mode="json") for item in payload.network_connections])
        self.session.exec(
            delete(ScenarioNodeRuntimeState).where(ScenarioNodeRuntimeState.scenario_id == scenario_id)
        )
        snapshot = self._snapshot(payload)
        self._append_revision(record.id, snapshot)
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        record_audit_event(self.session, record.id, "scenario.topology_updated", {
            "name": record.name,
            "device_count": len(payload.devices),
            "field_object_count": len(payload.field_objects),
        }, actor="api-client")
        return record.to_read_model()

    def delete(self, scenario_id: str, *, lease_owner: str) -> None:
        record = self.get_record(scenario_id)
        if record.status != "stopped":
            raise ScenarioConflictError("Stop the scenario before deleting it")
        # Consume the caller's exclusive lifecycle lease in the same transaction
        # that removes the scenario. This closes the gap where a concurrent
        # deploy could acquire the lease after it was released but before the
        # scenario row was deleted.
        from time import time

        lease = self.session.exec(delete(ScenarioOperationLease).where(
            ScenarioOperationLease.scenario_id == scenario_id,
            ScenarioOperationLease.operation == "delete",
            ScenarioOperationLease.owner_id == lease_owner,
            ScenarioOperationLease.expires_at > time(),
        ))
        if lease.rowcount != 1:
            self.session.rollback()
            raise ScenarioConflictError("Scenario deletion lease was lost; retry the operation")
        memberships = self.session.exec(select(ScenarioMembership).where(
            ScenarioMembership.scenario_id == scenario_id
        )).all()
        self.session.merge(ScenarioAuditArchive(
            scenario_id=scenario_id,
            scenario_name=record.name,
            owner_user_id=record.owner_user_id,
            memberships_json=json.dumps({item.user_id: item.role for item in memberships}, separators=(",", ":")),
        ))
        record_audit_event(self.session, scenario_id, "scenario.deleted", {
            "scenario_name": record.name,
            "membership_count": len(memberships),
        }, actor="api-client", commit=False)
        self.session.exec(delete(ScenarioNodeRuntimeState).where(ScenarioNodeRuntimeState.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioProtocolTraceCursor).where(ScenarioProtocolTraceCursor.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioFieldAuditState).where(ScenarioFieldAuditState.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioRevision).where(ScenarioRevision.scenario_id == scenario_id))
        self.session.exec(delete(ExerciseRun).where(ExerciseRun.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioMembership).where(ScenarioMembership.scenario_id == scenario_id))
        self.session.exec(delete(TerminalSessionLease).where(TerminalSessionLease.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioScadaSample).where(ScenarioScadaSample.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioScadaAlarmState).where(ScenarioScadaAlarmState.scenario_id == scenario_id))
        self.session.exec(delete(ScenarioScadaAlarmPending).where(ScenarioScadaAlarmPending.scenario_id == scenario_id))
        self.session.delete(record)
        self.session.commit()

    def set_status(self, scenario_id: str, status: str) -> None:
        record = self.get_record(scenario_id)
        previous = record.status
        if previous == status:
            return
        record.status = status
        self.session.add(record)
        self.session.commit()
        record_audit_event(self.session, scenario_id, "scenario.status_changed", {
            "previous": previous,
            "status": status,
        })

    @staticmethod
    def _snapshot(payload: ScenarioCreate | ScenarioUpdate) -> dict:
        return {
            "name": payload.name,
            "network": payload.network.model_dump(mode="json"),
            "devices": [device.model_dump(mode="json") for device in payload.devices],
            "canvas": payload.canvas,
            "field_objects": [item.model_dump(mode="json") for item in payload.field_objects],
            "signal_connections": [item.model_dump(mode="json") for item in payload.signal_connections],
            "process_connections": [item.model_dump(mode="json") for item in payload.process_connections],
            "network_routes": [item.model_dump(mode="json") for item in payload.network_routes],
            "network_connections": [item.model_dump(mode="json") for item in payload.network_connections],
        }

    def _append_revision(self, scenario_id: str, snapshot: dict) -> None:
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        latest = self.session.exec(
            select(ScenarioRevision)
            .where(ScenarioRevision.scenario_id == scenario_id)
            .order_by(ScenarioRevision.version.desc())
        ).first()
        if latest and latest.content_hash == digest:
            return
        self.session.add(ScenarioRevision(
            scenario_id=scenario_id,
            version=(latest.version + 1) if latest else 1,
            content_hash=digest,
            snapshot_json=serialized,
        ))

    @staticmethod
    def _build_runtime_devices(payload: ScenarioCreate | ScenarioUpdate) -> list[dict]:
        runtime_devices: list[dict] = []
        for device in payload.devices:
            device_class = DEVICE_REGISTRY.get(device.device_type)
            if device_class is None:
                raise ValueError(f"Unsupported device type '{device.device_type}'")
            runtime = device_class().build_runtime_device(device)
            runtime["hostname"] = device.hostname
            runtime["network_mode"] = device_class().network_mode(runtime)
            zone = next((item for item in payload.network.zones if item.zone_id == device.zone_id), None)
            runtime.update(zone_id=device.zone_id,
                           zone_subnet=zone.subnet if zone else payload.network.subnet,
                           zoned=bool(payload.network.zones))
            runtime_devices.append(runtime)
        by_id = {device["node_id"]: device for device in runtime_devices}
        route_side_ips: dict[str, set[str]] = {}
        for route in payload.network_routes:
            if not route.enabled:
                continue
            source = by_id[route.source_node_id]
            target = by_id[route.target_node_id]
            expected_type = "plc-s7" if route.protocol == "s7" else "modbus-tcp"
            if route.protocol != "tcp" and (source["device_type"] not in {"scada", "hmi", "plc-s7"}
                                              or target["device_type"] != expected_type):
                continue
            route_host = source["ip"]
            source_network = ip_network(source["zone_subnet"])
            source_used = {item["ip"] for item in runtime_devices if item.get("zone_id") == source["zone_id"]}
            route_host = next((str(source_network.network_address + offset)
                               for offset in range(2, source_network.num_addresses - 1)
                               if str(source_network.network_address + offset) not in source_used), None)
            if route_host is None:
                raise ValueError(f"zone '{source['zone_id']}' has no free address for route '{route.route_id}'")
            route_port = route.listen_port
            endpoint = {"host": route_host, "port": route_port, "protocol": route.protocol,
                        "target_port": route.target_port, "target_node_id": target["node_id"]}
            if route.protocol == "tcp":
                source["config"].setdefault("tcp_routes", {})[route.route_id] = endpoint
            else:
                source["config"].setdefault("network_routes", {})[target["node_id"]] = endpoint
            # Gateway runtime is materialized below; the source device only talks
            # to its local listener and never connects directly across zones.
            target_zone = target["zone_id"]
            target_network = ip_network(target["zone_subnet"])
            occupied_target_ips = route_side_ips.setdefault(
                target_zone,
                {item["ip"] for item in runtime_devices if item.get("zone_id") == target_zone},
            )
            target_interface_ip = next((str(target_network.network_address + offset)
                                        for offset in range(2, target_network.num_addresses - 1)
                                        if str(target_network.network_address + offset) not in occupied_target_ips), None)
            if target_interface_ip is None:
                raise ValueError(f"zone '{target_zone}' has no free address for route '{route.route_id}'")
            occupied_target_ips.add(target_interface_ip)
            gateway_id = f"__anvil_route_{route.route_id}"
            if any(item["node_id"] == gateway_id for item in runtime_devices):
                raise ValueError("reserved internal network route node ID")
            gateway = {
                "node_id": gateway_id, "device_type": "tcp-gateway", "name": f"Ruta {route.route_id}",
                "ip": route_host, "hostname": f"route-{hashlib.sha256(route.route_id.encode()).hexdigest()[:12]}",
                "target_ip": target["ip"],
                "external_access": False, "external_port": None, "network_mode": None,
                "zone_id": source["zone_id"], "zone_subnet": source["zone_subnet"], "zoned": source["zoned"],
                "config": {"role": "tcp-gateway", "summary": f"{route.protocol} to {route.target_node_id}",
                           "listen_port": route_port, "source_node_id": source["node_id"],
                           "source_ip": source["ip"], "target_ip": target["ip"],
                           "target_port": (route.target_port if route.protocol == "tcp" else
                                           102 if route.protocol == "s7" else int(target["config"]["port"])),
                           "target_interface_ip": target_interface_ip,
                           "target_zone_id": target_zone, "target_zone_subnet": target["zone_subnet"],
                           "target_hostname": target["hostname"], "target_node_id": target["node_id"],
                           "protocol": route.protocol, "latency_ms": route.latency_ms,
                           "inspect_protocol": (
                               "modbus-tcp" if target["device_type"] == "modbus-tcp"
                               and target["config"].get("transport", "tcp") == "tcp"
                               and (route.protocol == "modbus-tcp"
                                    or route.protocol == "tcp"
                                    and route.target_port == int(target["config"].get("port", 502)))
                               else None
                           )},
            }
            runtime_devices.append(gateway)
        egress_by_source: dict[str, list[dict[str, Any]]] = {}
        for rule in payload.network.egress_rules:
            if rule.enabled:
                egress_by_source.setdefault(rule.source_node_id, []).append({
                    "hostname": rule.hostname, "tcp_port": rule.tcp_port,
                })
        for source_id, rules in egress_by_source.items():
            source = by_id[source_id]
            source_network = ip_network(source["zone_subnet"])
            occupied = {item["ip"] for item in runtime_devices if item.get("zone_id") == source["zone_id"]}
            gateway_ip = next((str(source_network.network_address + offset)
                               for offset in range(2, source_network.num_addresses - 1)
                               if str(source_network.network_address + offset) not in occupied), None)
            if gateway_ip is None:
                raise ValueError(f"zone '{source['zone_id']}' has no free address for the egress gateway")
            suffix = hashlib.sha256(source_id.encode()).hexdigest()[:12]
            gateway_id = f"__anvil_egress_{source_id}"
            source["egress_proxy_url"] = f"http://egress-{suffix}:3128"
            runtime_devices.append({
                "node_id": gateway_id,
                "device_type": "egress-gateway",
                "name": f"Salida controlada · {source['name']}",
                "ip": gateway_ip,
                "hostname": f"egress-{suffix}",
                "external_access": False,
                "external_port": None,
                "network_mode": None,
                "zone_id": source["zone_id"],
                "zone_subnet": source["zone_subnet"],
                "zoned": source["zoned"],
                "config": {"source_node_id": source_id, "source_ip": source["ip"], "rules": rules},
            })
        ingress_devices = [item for item in runtime_devices if item.get("external_access")]
        for target in ingress_devices:
            if target["device_type"] not in {"plc-s7", "modbus-tcp"}:
                raise ValueError("External protocol access is supported only for S7 and Modbus TCP devices")
            target_port = 102 if target["device_type"] == "plc-s7" else int(target["config"]["port"])
            protocol = "s7" if target["device_type"] == "plc-s7" else "modbus-tcp"
            external_port = target["external_port"]
            external_host_ip = target.get("external_host_ip", "127.0.0.1")
            zone_network = ip_network(target["zone_subnet"])
            occupied = {item["ip"] for item in runtime_devices if item.get("zone_id") == target["zone_id"]}
            private_ip = next((str(zone_network.network_address + offset)
                               for offset in range(2, zone_network.num_addresses - 1)
                               if str(zone_network.network_address + offset) not in occupied), None)
            if private_ip is None:
                raise ValueError(f"zone '{target['zone_id']}' has no free address for protocol ingress")
            target["external_access"] = False
            target["external_port"] = None
            target["external_host_ip"] = "127.0.0.1"
            runtime_devices.append({
                "node_id": f"__anvil_ingress_{target['node_id']}",
                "device_type": "protocol-ingress",
                "name": f"Ingreso {protocol} · {target['name']}",
                "ip": private_ip,
                "hostname": f"ingress-{hashlib.sha256(target['node_id'].encode()).hexdigest()[:12]}",
                "external_access": True,
                "external_host_ip": external_host_ip,
                "external_port": external_port,
                "zone_id": target["zone_id"], "zone_subnet": target["zone_subnet"],
                "zoned": target["zoned"], "network_mode": None,
                "config": {"listen_ip": private_ip, "listen_port": 25000,
                            "target_ip": target["ip"], "target_port": target_port,
                            "target_node_id": target["node_id"], "protocol": protocol},
            })
        for device in runtime_devices:
            if device["device_type"] not in {"scada", "hmi"}:
                continue
            targets = [item.strip() for item in device["config"]["target_node_ids"].split(",") if item.strip()]
            if device["device_type"] == "hmi":
                # HMI operations run from this container, preserving zone isolation.
                continue
            acquired = []
            available: set[tuple[str, str]] = set()
            for target_id in targets:
                target = by_id[target_id]
                route = device["config"].get("network_routes", {}).get(target_id)
                adapter = DEVICE_REGISTRY[target["device_type"]]()
                tags = adapter.build_io_map(target)
                for tag in tags:
                    available.add((target_id, tag["address"]))
                acquired.append({
                    "node_id": target_id,
                    "device_type": target["device_type"],
                    "host": route["host"] if route else target["ip"],
                    "port": route["port"] if route else (102 if target["device_type"] == "plc-s7" else target["config"]["port"]),
                    "rack": target["config"].get("rack", 0),
                    "slot": target["config"].get("slot", 1),
                    "unit_id": target["config"].get("unit_id", 1),
                    "transport": target["config"].get("transport", "tcp"),
                    "baudrate": target["config"].get("baudrate", 19200),
                    "bytesize": target["config"].get("bytesize", 8),
                    "parity": target["config"].get("parity", "N"),
                    "stopbits": target["config"].get("stopbits", 1),
                    "tags": [{"address": tag["address"], "name": tag["component_name"],
                              "data_type": tag["data_type"], "access": tag.get("access", "read"),
                              **({key: tag[key] for key in ("min_value", "max_value") if key in tag})}
                             for tag in tags],
                })
            for alarm in device["config"].get("alarms", []):
                if (alarm["target_node_id"], alarm["address"]) not in available:
                    raise ValueError(f"SCADA alarm address '{alarm['address']}' is not mapped on target '{alarm['target_node_id']}'")
            device["config"]["targets"] = acquired
        from services.s7_links import resolve_s7_links
        resolve_s7_links(runtime_devices)
        from services.field_runtime import add_field_runtime
        return add_field_runtime(payload, runtime_devices)
