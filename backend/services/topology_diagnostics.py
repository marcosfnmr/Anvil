"""Actionable pre-deployment checks for stored scenario topologies."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def diagnose_scenario(
    scenario: Any,
    runtime_status: dict[str, Any] | None = None,
    runtime_routes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Revalidate the declarative topology and report common incomplete setups."""
    from models.scenario import ScenarioCreate

    payload = scenario.model_dump(mode="json", exclude={
        "id", "status", "created_at", "owner_user_id", "access_role",
    })
    try:
        ScenarioCreate.model_validate(payload)
        errors: list[dict[str, Any]] = []
    except ValidationError as exc:
        errors = [
            {"code": item["type"], "path": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in exc.errors(include_url=False)
        ]
    except ValueError as exc:
        errors = [{"code": "invalid_topology", "path": "", "message": str(exc)}]

    warnings: list[dict[str, str]] = []

    def warn(code: str, node_id: str, message: str) -> None:
        warnings.append({"code": code, "node_id": node_id, "message": message})

    devices_by_id = {item.node_id: item for item in scenario.devices}
    for device in scenario.devices:
        if not device.external_access:
            continue
        host_endpoint = f"{device.external_host_ip}:{device.external_port}"
        adapter = __import__("devices", fromlist=["DEVICE_REGISTRY"]).DEVICE_REGISTRY.get(device.device_type)
        target_port = adapter.external_port(device.config) if adapter is not None else None
        if device.external_host_ip == "127.0.0.1":
            warn("external_access_loopback", device.node_id,
                 f"«{device.name}» publica {host_endpoint} en loopback hacia el puerto OT {target_port}; solo acepta clientes del host. Para acceso remoto administrativo, usa un túnel SSH autenticado.")
        else:
            warn("external_access_non_loopback", device.node_id,
                 f"«{device.name}» publica {host_endpoint} hacia el puerto OT {target_port} en una interfaz del host. Anvil no configura el firewall ni filtra IPs de origen; limita este puerto a maestros autorizados en el firewall del host y no lo expongas a Internet.")
    if scenario.network.zones:
        for device in scenario.devices:
            if device.device_type in {"hmi", "scada"}:
                targets = [item.strip() for item in device.config.target_node_ids.split(",") if item.strip()]
                for target_id in targets:
                    target = devices_by_id.get(target_id)
                    if target is None or target.zone_id == device.zone_id:
                        continue
                    protocol = "s7" if target.device_type == "plc-s7" else "modbus-tcp"
                    route = next((item for item in scenario.network_routes
                                  if item.source_node_id == device.node_id
                                  and item.target_node_id == target_id
                                  and item.protocol == protocol), None)
                    if route is None or not route.enabled:
                        label = "S7" if protocol == "s7" else "Modbus TCP"
                        generic_route = any(item.source_node_id == device.node_id
                                            and item.target_node_id == target_id
                                            and item.protocol == "tcp" for item in scenario.network_routes)
                        if route is not None:
                            action = f"habilita la ruta {label}"
                            reason = ""
                        elif generic_route:
                            action = f"sustituye la ruta TCP genérica por una ruta {label}"
                            reason = "La ruta TCP genérica no configura el cliente de protocolo. "
                        else:
                            action = f"crea y habilita la ruta {label}"
                            reason = ""
                        warn("device_target_unreachable", device.node_id,
                             f"{reason}{device.name} no puede alcanzar {target.name} ({label}) entre zonas; {action}.")
            if device.device_type == "plc-s7":
                for component in device.config.components:
                    if component.type != "remote_input":
                        continue
                    target = devices_by_id.get(component.source_node_id)
                    if target is None or target.zone_id == device.zone_id:
                        continue
                    route = next((item for item in scenario.network_routes
                                  if item.source_node_id == device.node_id
                                  and item.target_node_id == target.node_id
                                  and item.protocol == "s7"), None)
                    if route is None or not route.enabled:
                        warn("s7_peer_unreachable", device.node_id,
                             f"{device.name} no puede leer {target.name} mediante remote_input entre zonas; crea/habilita la ruta S7.")

    connected_field_ports = {
        (endpoint.object_id, endpoint.port)
        for connection in scenario.signal_connections
        for endpoint in (connection.source, connection.target)
        if endpoint.kind == "field"
    }
    field_objects_by_id = {item.object_id: item for item in scenario.field_objects}
    connected_process_ports = {
        (endpoint.object_id, endpoint.port)
        for connection in scenario.process_connections
        for endpoint in (connection.source, connection.target)
    }
    flow_object_types = {
        "pump", "variable_speed_drive", "tank", "pipe", "valve",
        "proportional_valve", "flow_splitter", "flow_mixer",
    }
    flow_objects = {
        item.object_id: item for item in scenario.field_objects
        if item.type in flow_object_types
    }
    flow_outgoing = {object_id: set() for object_id in flow_objects}
    flow_incoming = {object_id: set() for object_id in flow_objects}
    for connection in scenario.process_connections:
        # A pressure/level sensor tap is a measurement edge, not a hydraulic
        # path segment, and must not make an otherwise isolated branch appear
        # connected to a tank.
        if connection.target.port == "measurement":
            continue
        source_id, target_id = connection.source.object_id, connection.target.object_id
        if source_id in flow_objects and target_id in flow_objects:
            flow_outgoing[source_id].add(target_id)
            flow_incoming[target_id].add(source_id)

    def reachable(starts: set[str], graph: dict[str, set[str]]) -> set[str]:
        reached = set(starts)
        pending = list(starts)
        while pending:
            current = pending.pop()
            for neighbor in graph.get(current, ()):
                if neighbor not in reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
        return reached

    flow_sources = {
        object_id for object_id, item in flow_objects.items()
        if item.type in {"pump", "variable_speed_drive"}
        or (item.type == "tank" and flow_outgoing[object_id])
    }
    flow_sinks = {
        object_id for object_id, item in flow_objects.items()
        if (item.type == "tank" and flow_incoming[object_id])
        or (item.type in {"valve", "proportional_valve"}
            and flow_incoming[object_id] and not flow_outgoing[object_id])
    }
    source_reachable = reachable(flow_sources, flow_outgoing)
    sink_reachable = reachable(flow_sinks, flow_incoming)
    complete_flow_path = source_reachable & sink_reachable

    for object_id, item in flow_objects.items():
        if not flow_incoming[object_id] and not flow_outgoing[object_id]:
            continue  # Existing per-object warnings explain fully isolated devices.
        if object_id in complete_flow_path:
            continue
        if object_id in source_reachable:
            issue = "no llega a la entrada de ningún depósito"
        elif object_id in sink_reachable:
            issue = "no recibe caudal de ninguna bomba, variador o salida de tanque"
        else:
            issue = "no forma parte de un camino continuo entre una fuente y un depósito"
        warn("field_process_path_incomplete", object_id,
             f"«{item.name}» {issue}; conecta sus ramas de proceso hasta cerrar el recorrido hidráulico.")

    for link in scenario.signal_connections:
        if link.route_enabled is not False:
            continue
        field_endpoint = link.source if link.source.kind == "field" else link.target
        device_endpoint = link.target if link.target.kind == "device" else link.source
        field_object = field_objects_by_id.get(field_endpoint.object_id)
        device = devices_by_id.get(device_endpoint.node_id)
        if field_object is not None and device is not None and field_object.zone_id != device.zone_id:
            warn("field_signal_route_blocked", device.node_id,
                 f"La señal «{link.connection_id}» de {field_object.name} a {device.name} está bloqueada; habilita la ruta al runtime de campo.")
    for item in scenario.field_objects:
        if item.type in {"valve", "motor", "pump"} and (item.object_id, "command") not in connected_field_ports:
            warn("actuator_without_command", item.object_id,
                 f"Conecta la entrada de mando de «{item.name}» a una salida de PLC, o controla el objeto desde la API de campo.")
        if item.type == "analog_sensor" and (item.object_id, "value") not in connected_field_ports:
            warn("sensor_without_consumer", item.object_id,
                 f"«{item.name}» no envía su medida a ningún dispositivo.")
        if item.type != "tank" and not any(
            endpoint.object_id == item.object_id
            for connection in scenario.process_connections
            for endpoint in (connection.source, connection.target)
        ) and item.type in {"pump", "valve", "analog_sensor"}:
            warn("field_process_unconnected", item.object_id,
                 f"«{item.name}» no participa en una conexión de proceso físico; su estado no alterará el modelo hidráulico.")
        if item.type == "tank":
            missing = [port for port in ("inlet", "outlet") if (item.object_id, port) not in connected_process_ports]
            if missing:
                warn("tank_process_incomplete", item.object_id,
                     f"«{item.name}» no tiene conexión de proceso en: {', '.join(missing)}.")
        if item.type == "flow_splitter":
            required = ("process_in", "process_out_a", "process_out_b")
            missing = [port for port in required if (item.object_id, port) not in connected_process_ports]
            if missing:
                warn("flow_splitter_incomplete", item.object_id,
                     f"«{item.name}» necesita una entrada y sus dos ramas conectadas; faltan: {', '.join(missing)}.")
        if item.type == "flow_mixer":
            required = ("process_in_a", "process_in_b", "process_out")
            missing = [port for port in required if (item.object_id, port) not in connected_process_ports]
            if missing:
                warn("flow_mixer_incomplete", item.object_id,
                     f"«{item.name}» necesita sus dos entradas y una salida conectadas; faltan: {', '.join(missing)}.")

    for device in scenario.devices:
        if device.device_type == "hmi" and not device.config.target_node_ids.strip():
            warn("hmi_without_targets", device.node_id,
                 f"Selecciona al menos un PLC S7 o dispositivo Modbus para que «{device.name}» pueda mostrar señales.")
        if device.device_type == "plc-s7":
            # Standalone field actuators are bound to explicit S7 output
            # channels.  Warn only when one of those channels is absent from
            # every supported logic-block output. Manual HMI/API writes are
            # still valid, so this is an advisory rather than a validation error.
            linked_device_endpoints = [
                endpoint
                for link in scenario.signal_connections
                for endpoint in (link.source, link.target)
                if endpoint.kind == "device"
                and endpoint.node_id == device.node_id
                and any(
                    field_endpoint.kind == "field"
                    and field_endpoint.port == "command"
                    and field_objects_by_id.get(field_endpoint.object_id) is not None
                    and field_objects_by_id[field_endpoint.object_id].type in {"valve", "motor", "pump"}
                    for field_endpoint in (link.source, link.target)
                )
            ]
            linked_output_channels = {endpoint.channel_id for endpoint in linked_device_endpoints if endpoint.channel_id}
            linked_output_addresses = {endpoint.address for endpoint in linked_device_endpoints}
            plc_adapter = __import__("devices", fromlist=["DEVICE_REGISTRY"]).DEVICE_REGISTRY["plc-s7"]()
            runtime_device = plc_adapter.build_runtime_device(device)
            output_names = {
                point["component_name"] for point in plc_adapter.build_io_map(runtime_device)
                if point["component_type"] == "io_channel"
                and point.get("direction") == "output"
                and (point.get("channel_id") in linked_output_channels
                     or point["address"] in linked_output_addresses)
            }
            logic_outputs = {
                rule.output_component for rule in (
                    *device.config.control_rules,
                    *device.config.boolean_rules,
                    *device.config.timers,
                    *device.config.latches,
                    *device.config.counters,
                )
            }
            logic_outputs.update(
                output for sequence in device.config.sequences for output in sequence.steps
            )
            standalone_uncontrolled = output_names - logic_outputs
            legacy_outputs = {
                component.name for component in device.config.components
                if component.type in {"valve", "motor", "pump"}
            }
            for output_name in sorted(legacy_outputs - logic_outputs):
                warn("plc_without_control_logic", device.node_id,
                     f"La salida heredada «{output_name}» de «{device.name}» no está asignada a un bloque de control; solo responderá a operaciones manuales.")
            for output_name in sorted(standalone_uncontrolled):
                warn("plc_output_without_control_logic", device.node_id,
                     f"La salida «{output_name}» de «{device.name}» está conectada a un actuador independiente, pero ningún bloque de lógica la gobierna; el actuador solo responderá a mandos manuales.")
    if len(scenario.network.zones) > 1:
        warn("zones_are_isolated", "", "Las zonas Purdue se despliegan aisladas por defecto. Solo se permiten los flujos S7/Modbus, campo HTTP o TCP por puerto declarados y habilitados; no hay routing L3 ni ACL entre equipos de una misma zona.")
    for route in scenario.network_routes:
        if not route.enabled:
            protocol = "TCP" if route.protocol == "tcp" else route.protocol.upper()
            warn("network_route_blocked", route.source_node_id,
                 f"La ruta {protocol} «{route.route_id}» está bloqueada; su proxy no se incluirá en el despliegue y ese flujo no podrá comunicarse.")

    runtime: dict[str, Any] = {"checked": runtime_status is not None, "nodes": []}
    if runtime_status is not None:
        declared_ids = set(devices_by_id)
        observed = {
            str(item.get("node_id")): item
            for item in runtime_status.get("containers", [])
            if str(item.get("node_id", "")) in declared_ids
        }
        runtime_nodes: list[dict[str, Any]] = []
        for device in scenario.devices:
            state = observed.get(device.node_id)
            if state is None or state.get("status") == "missing":
                runtime_nodes.append({"node_id": device.node_id, "status": "missing", "health_status": None})
                resource = getattr(__import__("devices", fromlist=["DEVICE_REGISTRY"]).DEVICE_REGISTRY.get(device.device_type), "network_resource", False)
                warn("runtime_node_missing", device.node_id,
                     f"No hay una red Docker activa para «{device.name}»; vuelve a desplegar la zona." if resource else
                     f"No hay un contenedor activo observado para «{device.name}»; revisa el despliegue o recrea el dispositivo.")
                continue
            status = str(state.get("status") or "unknown")
            health = state.get("health_status")
            runtime_nodes.append({"node_id": device.node_id, "status": status, "health_status": health})
            detail = state.get("health_error") or state.get("runtime_error")
            if status not in {"running", "paused"} or health == "unhealthy":
                explanation = f" Detalle: {str(detail)[:300]}" if detail else ""
                warn("runtime_node_unhealthy", device.node_id,
                     f"«{device.name}» está {status} (healthcheck: {health or 'sin estado'}). Revisa los logs y reinicia o recrea el dispositivo.{explanation}")
        route_results = runtime_routes or []
        for route in route_results:
            if route.get("status") != "reachable":
                details = f" Detalle: {str(route.get('detail'))[:250]}" if route.get("detail") else ""
                warn("runtime_route_unreachable", str(route.get("source_node_id") or ""),
                     f"La ruta «{route.get('route_id')}» ({route.get('protocol')}) no respondió desde el nodo origen «{route.get('source_node_id')}»; comprueba listener, servicio destino y configuración.{details}")
        protocol_routes = [item for item in route_results if item.get("protocol") in {"s7", "modbus-tcp", "field-http", "field-binding"}]
        runtime = {
            "checked": True,
            "status": runtime_status.get("status", "unknown"),
            "nodes": runtime_nodes,
            "routes_checked": len(route_results),
            "routes": route_results,
            "protocol_transactions_verified": bool(protocol_routes) and all(
                item.get("status") == "reachable" for item in protocol_routes
            ),
        }

    return {
        "scenario_id": str(scenario.id),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "runtime": runtime,
        "summary": {"error_count": len(errors), "warning_count": len(warnings)},
    }
