"""Physical field simulation. Internal HTTP transport represents signal wiring."""
import hashlib
import json
import math
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIELD_RUNTIME_CONTRACT_VERSION = 1


def field_runtime_capabilities():
    """Describe the internal runtime contract used by the Anvil backend."""
    return {
        "contract": "anvil-field-runtime",
        "contract_version": FIELD_RUNTIME_CONTRACT_VERSION,
        "endpoints": ["/snapshot", "/exchange", "/control"],
    }


class FieldEngine:
    def __init__(self, config, clock=time.monotonic):
        contract_version = config.get("contract_version", FIELD_RUNTIME_CONTRACT_VERSION)
        if type(contract_version) is not int or contract_version != FIELD_RUNTIME_CONTRACT_VERSION:
            raise ValueError(f"unsupported field runtime contract version: {contract_version}")
        self.clock = clock
        self.started = clock()
        self.objects = {item["object_id"]: item for item in config["objects"]}
        self.connections = config.get("connections", [])
        self.process_connections = config.get("process_connections", [])
        self._ordered_process_connections, self._process_graph_acyclic = self._order_process_connections()
        self.lock = threading.RLock()
        self.last_seen = {}
        self.states = {}
        self.last_step = self.started
        for oid, item in self.objects.items():
            initial = item.get("initial_speed_pct", item.get("initial_opening_pct", item.get("initial_state", False)))
            self.states[oid] = {"command": initial, "state": initial, "fault": False,
                                "override": None, "changed": self.started, "value": 0.0, "flow": 0.0,
                                "pressure_kpa": 0.0, "pressure_in_kpa": 0.0, "pressure_drop_kpa": 0.0,
                                "opening_pct": initial if item["type"] == "proportional_valve" else 0.0,
                                "speed_pct": initial if item["type"] == "variable_speed_drive" else 0.0,
                                "demand_lpm": item.get("demand_flow_lpm", 0.0),
                                "leak_rate_lpm": item.get("leak_flow_lpm", 0.0),
                                "leak_lpm": 0.0,
                                "split_ratio_pct": item.get("split_ratio_pct", 50.0),
                                "volume_liters": item.get("initial_level_liters", 0.0),
                                "process_in": 0.0, "process_out": 0.0, "process_in_a": 0.0, "process_in_b": 0.0,
                                "process_out_a": 0.0, "process_out_b": 0.0,
                                "inflow_lpm": 0.0, "outflow_lpm": 0.0}
        self.sensor_random = {}
        for oid, item in self.objects.items():
            if item["type"] != "analog_sensor":
                continue
            seed = item.get("simulation", {}).get("seed")
            if seed is not None:
                material = f"anvil-sensor-noise:{seed}:{oid}".encode("utf-8")
                seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            self.sensor_random[oid] = random.Random(seed)
        self.step()

    def _order_process_connections(self):
        """Order acyclic process edges and detect recirculation for fallback."""
        outgoing = {object_id: set() for object_id in self.objects}
        indegree = {object_id: 0 for object_id in self.objects}
        for link in self.process_connections:
            source_id = link["source"]["object_id"]
            target_id = link["target"]["object_id"]
            if source_id not in outgoing or target_id not in indegree:
                raise ValueError("process connection references an unknown field object")
            if target_id not in outgoing[source_id]:
                outgoing[source_id].add(target_id)
                indegree[target_id] += 1

        object_order = {object_id: index for index, object_id in enumerate(self.objects)}
        ready = [object_id for object_id in self.objects if indegree[object_id] == 0]
        result = []
        while ready:
            source_id = ready.pop(0)
            result.append(source_id)
            for target_id in sorted(outgoing[source_id], key=object_order.__getitem__):
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
        if len(result) != len(self.objects):
            # Preserve supported recirculating scenarios. They use the
            # established bounded iterative propagation below; acyclic graphs
            # use the dependency order and require only one pass.
            return list(self.process_connections), False
        rank = {object_id: index for index, object_id in enumerate(result)}
        return sorted(self.process_connections, key=lambda link: (
            rank[link["source"]["object_id"]],
            rank[link["target"]["object_id"]],
        )), True

    def _path_flow_capacity(self, endpoint, outgoing, head_kpa, flow_budget, visited=None):
        """Return a series path's flow capacity and recursively size splitters."""
        seen = set() if visited is None else set(visited)
        current = endpoint
        if current in seen:
            return 0.0
        resistance, capacity = 0.0, max(0.0, flow_budget)
        while current not in seen:
            seen.add(current)
            target_id, _ = current
            target = self.objects[target_id]
            if target["type"] == "pipe":
                maximum = target["max_flow_lpm"]
                capacity = min(capacity, maximum)
                resistance += target.get("pressure_drop_kpa", 0.0) / (maximum * maximum)
                source = (target_id, "process_out")
            elif target["type"] in {"valve", "proportional_valve"}:
                capacity = min(capacity, self._valve_flow_capacity(target_id))
                source = (target_id, "process_out")
            elif target["type"] == "flow_splitter":
                branch_head = max(0.0, head_kpa - resistance * flow_budget * flow_budget)
                branch_caps = []
                for port in ("process_out_a", "process_out_b"):
                    links = outgoing.get((target_id, port), [])
                    branch_cap = (self._path_flow_capacity(
                        links[0][1], outgoing, branch_head, flow_budget, seen
                    ) if len(links) == 1 else 0.0)
                    branch_caps.append(branch_cap)
                self._splitter_capacities[target_id] = tuple(branch_caps)
                capacity = min(capacity, sum(branch_caps))
                break
            else:
                break

            links = outgoing.get(source, [])
            if len(links) != 1:
                break
            current = links[0][1]

        if resistance > 0.0:
            capacity = min(capacity, math.sqrt(max(0.0, head_kpa) / resistance))
        return max(0.0, capacity)

    def _valve_flow_capacity(self, object_id):
        """Maximum flow an actuator valve can pass at its current position."""
        item, state = self.objects[object_id], self.states[object_id]
        if item["type"] == "valve":
            return item["flow_rate_lpm"] if state["state"] else 0.0
        return item["flow_rate_lpm"] * max(0.0, min(100.0, state["opening_pct"])) / 100.0

    @staticmethod
    def _split_flow(total, ratio_pct, capacities):
        """Apply the configured split and move unmet flow to available capacity."""
        cap_a, cap_b = capacities
        total = min(max(0.0, total), cap_a + cap_b)
        flow_a = min(total * ratio_pct / 100.0, cap_a)
        flow_b = min(total - total * ratio_pct / 100.0, cap_b)
        remainder = max(0.0, total - flow_a - flow_b)
        extra_a = min(max(0.0, cap_a - flow_a), remainder)
        flow_a += extra_a
        remainder -= extra_a
        flow_b += min(max(0.0, cap_b - flow_b), remainder)
        return flow_a, flow_b

    def _apply_series_pressure_limits(self):
        """Limit series flow and conserve flow across pump/drive branches."""
        outgoing = {}
        for link in self.process_connections:
            source = (link["source"]["object_id"], link["source"]["port"])
            target = (link["target"]["object_id"], link["target"]["port"])
            if target[1] in {"process_in", "inlet"}:
                outgoing.setdefault(source, []).append((link, target))

        self._branch_flows = {}
        self._splitter_capacities = {}

        for oid, item in self.objects.items():
            if item["type"] in {"pump", "variable_speed_drive"}:
                state = self.states[oid]
                free_flow = max(0.0, state["flow"])
                if item["type"] == "pump":
                    head_kpa = item.get("max_head_kpa", 100.0) if state["state"] and not state["fault"] else 0.0
                    source_port = "flow"
                else:
                    head_kpa = item.get("max_head_kpa", 100.0) * (state["speed_pct"] / 100.0) ** 2 if not state["fault"] else 0.0
                    source_port = "flow"
            elif item["type"] == "tank":
                state = self.states[oid]
                free_flow = item["outlet_flow_lpm"] if state["volume_liters"] > 0 else 0.0
                head_kpa = state["pressure_kpa"]
                source_port = "outlet"
            else:
                continue

            source = (oid, source_port)
            branches = outgoing.get(source, [])
            if len(branches) > 1 and item["type"] in {"pump", "variable_speed_drive"}:
                capacities = []
                for first_link, first_target in branches:
                    capacity = self._path_flow_capacity(first_target, outgoing, head_kpa, self._flow_capacity_budget())
                    capacities.append((first_link["connection_id"], max(0.0, capacity)))

                total_capacity = sum(capacity for _, capacity in capacities)
                total_flow = min(free_flow, total_capacity)
                for connection_id, capacity in capacities:
                    branch_flow = total_flow * capacity / total_capacity if total_capacity > 0 else 0.0
                    self._branch_flows[connection_id] = branch_flow
                self.states[oid]["flow"] = total_flow
                self.states[oid]["pressure_kpa"] = max(0.0, head_kpa)
                continue

            if len(branches) == 1:
                # Preserve legacy behavior for a simple series path: pipe
                # element limits apply to the pipe, while pump flow is only
                # pressure-limited. A splitter is the exception because its
                # downstream branch capacities must constrain the source.
                flow_limit = free_flow
                current = branches[0][1]
                seen = set()
                resistance = 0.0
                while current not in seen:
                    seen.add(current)
                    target_id, _ = current
                    target = self.objects[target_id]
                    if target["type"] == "pipe":
                        maximum = target["max_flow_lpm"]
                        resistance += target.get("pressure_drop_kpa", 0.0) / (maximum * maximum)
                        source_port = (target_id, "process_out")
                    elif target["type"] == "flow_splitter":
                        capacity = self._path_flow_capacity(current, outgoing, head_kpa, self._flow_capacity_budget())
                        flow_limit = min(flow_limit, capacity)
                        break
                    elif target["type"] in {"valve", "proportional_valve"}:
                        flow_limit = min(flow_limit, self._valve_flow_capacity(target_id))
                        source_port = (target_id, "process_out")
                    else:
                        break
                    links = outgoing.get(source_port, [])
                    if len(links) != 1:
                        break
                    current = links[0][1]
                if resistance > 0.0:
                    flow_limit = min(flow_limit, math.sqrt(max(0.0, head_kpa) / resistance))
            else:
                flow_limit = free_flow
            self.states[oid]["flow"] = max(0.0, flow_limit)
            self.states[oid]["pressure_kpa"] = max(0.0, head_kpa)

    def _flow_capacity_budget(self):
        """Use component ratings as a branch-sizing budget, not source flow."""
        ratings = [self.states[oid]["flow"] for oid, item in self.objects.items()
                   if item["type"] in {"pump", "variable_speed_drive"}]
        ratings.extend(item.get("max_flow_lpm", item.get("flow_rate_lpm", 0.0))
                       for item in self.objects.values()
                       if item["type"] in {"pipe", "valve", "proportional_valve"})
        return max(ratings, default=0.0)

    def step(self):
        with self.lock:
            now = self.clock()
            dt = max(0.0, now - self.last_step)
            self.last_step = now
            for oid, item in self.objects.items():
                state = self.states[oid]
                if item["type"] == "analog_sensor":
                    lo, hi = item.get("min_value", 0), item.get("max_value", 100)
                    simulation = item.get("simulation", {})
                    ratio = (1 + math.sin(2 * math.pi * (now - self.started) / simulation["period_seconds"])) / 2 if simulation.get("mode") == "sine_wave" else .5
                    if not state["fault"] and simulation.get("mode") != "process":
                        noise = simulation.get("noise", 0)
                        perturbation = self.sensor_random[oid].uniform(-1, 1) * noise * (hi - lo) if noise else 0.0
                        value = lo + (hi - lo) * ratio + perturbation
                        state["value"] = state["override"] if state["override"] is not None else min(hi, max(lo, value))
                elif item["type"] in {"digital_input", "alarm"}:
                    if not state["fault"]:
                        state["state"] = state["override"] if state["override"] is not None else item.get("initial_state", False)
                elif item["type"] == "tank":
                    capacity = item["capacity_liters"]
                    state["value"] = 100.0 * state["volume_liters"] / capacity
                    state["pressure_kpa"] = 9.80665 * (state["volume_liters"] / capacity) * item.get("height_m", 2.0)
                elif item["type"] == "proportional_valve":
                    if not state["fault"] and now - state["changed"] >= item.get("response_seconds", 0):
                        state["opening_pct"] = state["command"]
                    state["value"] = state["opening_pct"]
                    state["flow"] = 0.0
                elif item["type"] == "variable_speed_drive":
                    if not state["fault"] and now - state["changed"] >= item.get("response_seconds", 0):
                        state["speed_pct"] = state["command"]
                    state["state"] = state["speed_pct"] > 0
                    state["value"] = state["speed_pct"]
                    state["flow"] = item["max_flow_lpm"] * state["speed_pct"] / 100.0 if not state["fault"] else 0.0
                else:
                    if not state["fault"] and now - state["changed"] >= item.get("response_seconds", 0):
                        state["state"] = state["command"]
                    if item["type"] == "pump":
                        state["flow"] = (item.get("flow_min", 0) + item.get("flow_max", 100)) / 2 if state["state"] else 0.0

            # Limit each source against quadratic losses; pump/drive branch
            # flows are split here and conserved at each downstream tank.
            self._apply_series_pressure_limits()

            # Calculate physical flows separately from controller signal links.
            flows = {}
            for oid, item in self.objects.items():
                state = self.states[oid]
                if item["type"] in {"pump", "variable_speed_drive"}:
                    flows[(oid, "flow")] = state["flow"]
                    if item["type"] == "pump":
                        state["pressure_kpa"] = item.get("max_head_kpa", 100.0) if state["state"] and not state["fault"] else 0.0
                    else:
                        state["pressure_kpa"] = item.get("max_head_kpa", 100.0) * (state["speed_pct"] / 100.0) ** 2 if not state["fault"] else 0.0
                    flows[(oid, "pressure")] = state["pressure_kpa"]
                elif item["type"] == "pipe":
                    state["flow"] = 0.0
                    state["process_in"] = 0.0
                    state["process_out"] = 0.0
                    state["pressure_in_kpa"] = 0.0
                    state["pressure_kpa"] = 0.0
                    state["pressure_drop_kpa"] = 0.0
                    flows[(oid, "process_out")] = 0.0
                    flows[(oid, "pressure")] = 0.0
                elif item["type"] == "flow_splitter":
                    state["process_in"] = state["process_out_a"] = state["process_out_b"] = 0.0
                    state["pressure_kpa"] = 0.0
                    flows[(oid, "process_out_a")] = 0.0
                    flows[(oid, "process_out_b")] = 0.0
                    flows[(oid, "pressure")] = 0.0
                elif item["type"] == "flow_mixer":
                    state["process_in_a"] = state["process_in_b"] = state["flow"] = 0.0
                    state["pressure_kpa"] = 0.0
                    flows[(oid, "process_out")] = 0.0
                    flows[(oid, "pressure")] = 0.0
                elif item["type"] == "proportional_valve":
                    flows[(oid, "process_out")] = 0.0
                elif item["type"] == "tank":
                    flows[(oid, "outlet")] = state["flow"] if state["volume_liters"] > 0 else 0.0
                    flows[(oid, "pressure")] = state["pressure_kpa"]

            incoming = {oid: {} for oid, item in self.objects.items() if item["type"] == "tank"}
            drained = {oid: 0.0 for oid in incoming}
            mixer_inputs = {oid: {} for oid, item in self.objects.items() if item["type"] == "flow_mixer"}
            propagation_passes = 1 if self._process_graph_acyclic else len(self._ordered_process_connections) + 1
            for _ in range(propagation_passes):
                for link in self._ordered_process_connections:
                    source = (link["source"]["object_id"], link["source"]["port"])
                    target_id, target_port = link["target"]["object_id"], link["target"]["port"]
                    if source not in flows:
                        continue
                    value = self._branch_flows.get(link["connection_id"], flows[source])
                    source_pressure = flows.get((source[0], "pressure"), 0.0)
                    target_item = self.objects[target_id]
                    if target_item["type"] in {"valve", "proportional_valve"} and target_port == "process_in":
                        valve = self.states[target_id]
                        valve["process_in"] = value
                        if target_item["type"] == "proportional_valve":
                            passed = min(value, self._valve_flow_capacity(target_id))
                        else:
                            passed = min(value, self._valve_flow_capacity(target_id))
                        valve["process_out"] = passed
                        valve["flow"] = passed
                        flows[(target_id, "process_out")] = passed
                        flows[(target_id, "pressure")] = source_pressure
                        source_item = self.objects[source[0]]
                        if source_item["type"] == "tank":
                            drained[source[0]] = passed
                    elif target_item["type"] == "pipe" and target_port == "process_in":
                        pipe = self.states[target_id]
                        pipe["process_in"] = value
                        passed = min(value, target_item["max_flow_lpm"])
                        pipe["process_out"] = passed
                        pipe["flow"] = passed
                        pipe["pressure_in_kpa"] = source_pressure
                        nominal_drop = target_item.get("pressure_drop_kpa", 0.0)
                        pipe["pressure_drop_kpa"] = nominal_drop * (passed / target_item["max_flow_lpm"]) ** 2
                        pipe["pressure_kpa"] = max(0.0, source_pressure - pipe["pressure_drop_kpa"])
                        flows[(target_id, "process_out")] = passed
                        flows[(target_id, "pressure")] = pipe["pressure_kpa"]
                    elif target_item["type"] in {"valve", "proportional_valve"} and target_port == "process_in" and source[1] == "pressure":
                        flows[(target_id, "pressure")] = value
                    elif target_item["type"] == "tank" and target_port == "inlet":
                        incoming[target_id][link["connection_id"]] = value
                    elif target_item["type"] == "flow_splitter" and target_port == "process_in":
                        splitter = self.states[target_id]
                        splitter["process_in"] = value
                        splitter["pressure_kpa"] = source_pressure
                        capacities = self._splitter_capacities.get(target_id, (value, value))
                        passed_a, passed_b = self._split_flow(value, splitter["split_ratio_pct"], capacities)
                        splitter["process_in"] = passed_a + passed_b
                        splitter["process_out_a"], splitter["process_out_b"] = passed_a, passed_b
                        flows[(target_id, "process_out_a")] = passed_a
                        flows[(target_id, "process_out_b")] = passed_b
                        flows[(target_id, "pressure")] = source_pressure
                    elif target_item["type"] == "flow_mixer" and target_port in {"process_in_a", "process_in_b"}:
                        mixer = self.states[target_id]
                        mixer_inputs[target_id][target_port] = value
                        mixer[target_port] = value
                        combined = sum(mixer_inputs[target_id].values())
                        mixer["flow"] = combined
                        pressures = [flows.get((link["source"]["object_id"], "pressure"), 0.0)
                                     for link in self.process_connections
                                     if link["target"]["object_id"] == target_id
                                     and link["target"]["port"] in mixer_inputs[target_id]
                                     and mixer_inputs[target_id][link["target"]["port"]] > 0]
                        mixer["pressure_kpa"] = min(pressures, default=source_pressure)
                        flows[(target_id, "process_out")] = combined
                        flows[(target_id, "pressure")] = mixer["pressure_kpa"]

            for oid, item in self.objects.items():
                if item["type"] != "tank":
                    continue
                state = self.states[oid]
                inflow = sum(incoming[oid].values())
                has_liquid = state["volume_liters"] > 0
                leak = state["leak_rate_lpm"] if has_liquid else 0.0
                demand = state["demand_lpm"] if has_liquid else 0.0
                outflow = drained[oid] + leak + demand
                state["inflow_lpm"], state["outflow_lpm"] = inflow, outflow
                state["leak_lpm"] = leak
                state["effective_demand_lpm"] = demand
                state["volume_liters"] = min(item["capacity_liters"], max(0.0,
                    state["volume_liters"] + (inflow - outflow) * dt / 60.0))
                state["value"] = 100.0 * state["volume_liters"] / item["capacity_liters"]
                state["pressure_kpa"] = 9.80665 * (state["volume_liters"] / item["capacity_liters"]) * item.get("height_m", 2.0)

            for link in self.process_connections:
                if link["target"]["port"] != "measurement":
                    continue
                source = link["source"]
                source_state = self.states[source["object_id"]]
                source_port = source["port"]
                measured = source_state["pressure_kpa"] if source_port == "pressure" else source_state["value"]
                sensor, item = self.states[link["target"]["object_id"]], self.objects[link["target"]["object_id"]]
                lo, hi = item["min_value"], item["max_value"]
                mapped = min(hi, max(lo, measured)) if source_port == "pressure" else lo + min(100.0, max(0.0, measured)) * (hi - lo) / 100.0
                if not sensor["fault"]:
                    sensor["value"] = sensor["override"] if sensor["override"] is not None else mapped

    def exchange(self, payload):
        node_id = payload["node_id"]
        with self.lock:
            # Validate the complete frame before accepting any command.
            commands = []
            for link in self.connections:
                if link["source"]["kind"] == "device" and link["source"]["node_id"] == node_id:
                    if link["connection_id"] in payload.get("outputs", {}):
                        value = payload["outputs"][link["connection_id"]]
                        target_type = self.objects[link["target"]["object_id"]]["type"] if link["target"].get("kind") == "field" else None
                        if link.get("conversion") and target_type in {"proportional_valve", "variable_speed_drive"}:
                            if type(value) is not int or not 0 <= value <= 65535:
                                raise ValueError("scaled Modbus setpoint must be a UINT16 value from 0 to 65535")
                        value = self.convert_signal_value(link, value)
                        target_item = self.objects[link["target"]["object_id"]]
                        if target_item["type"] in {"proportional_valve", "variable_speed_drive"}:
                            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                                raise ValueError("analog actuator command must be a finite percentage from 0 to 100")
                        elif type(value) is not bool:
                            raise ValueError("actuator command must be BOOL")
                        commands.append((link["target"]["object_id"], value))
            self.last_seen[node_id] = self.clock()
            for oid, value in commands:
                state = self.states[oid]
                if value != state["command"]:
                    state.update(command=value, changed=self.clock())
            self.step()
            return {"inputs": {
                link["connection_id"]: self.signal_value(link)
                for link in self.connections
                if link["source"]["kind"] == "field" and link["target"]["node_id"] == node_id
            }}

    def signal_value(self, link):
        value = self.states[link["source"]["object_id"]][link["source"]["port"]]
        return self.convert_signal_value(link, value)

    def convert_signal_value(self, link, value):
        scale = link.get("conversion")
        if scale:
            ratio = min(1, max(0, (value - scale["input_min"]) / (scale["input_max"] - scale["input_min"])))
            scaled = scale["output_min"] + ratio * (scale["output_max"] - scale["output_min"])
            target = self.objects.get(link["target"].get("object_id"))
            value = float(scaled) if target and target["type"] in {"proportional_valve", "variable_speed_drive"} else round(scaled)
        return value

    def control(self, payload):
        oid = payload["object_id"]
        with self.lock:
            item, state = self.objects[oid], self.states[oid]
            override = payload.get("override")
            if "fault" in payload:
                if item["type"] not in {"valve", "proportional_valve", "variable_speed_drive", "motor", "pump", "analog_sensor", "digital_input"} or type(payload["fault"]) is not bool:
                    raise ValueError("fault requires an actuator or sensor and a boolean")
            if "override" in payload:
                if item["type"] == "analog_sensor":
                    if override is not None and (type(override) not in (int, float) or not math.isfinite(override) or not item.get("min_value", 0) <= override <= item.get("max_value", 100)):
                        raise ValueError("sensor override is outside its range")
                elif item["type"] in {"digital_input", "alarm"}:
                    if override is not None and type(override) is not bool:
                        raise ValueError("digital override must be boolean")
                else:
                    raise ValueError("actuators receive commands through their controller")
            demand = payload.get("demand_flow_lpm")
            if "demand_flow_lpm" in payload:
                if item["type"] != "tank" or type(demand) not in (int, float) or not math.isfinite(demand) or not 0 <= demand <= 3.4e38:
                    raise ValueError("demand_flow_lpm requires a tank and a finite non-negative flow")
            leak_rate = payload.get("leak_flow_lpm")
            if "leak_flow_lpm" in payload:
                if item["type"] != "tank" or type(leak_rate) not in (int, float) or not math.isfinite(leak_rate) or not 0 <= leak_rate <= 3.4e38:
                    raise ValueError("leak_flow_lpm requires a tank and a finite non-negative flow")

            # Validate every field above before mutating state. A combined
            # request with one invalid control must not partially apply others.
            if "override" in payload:
                state["override"] = override
            if "demand_flow_lpm" in payload:
                state["demand_lpm"] = demand
            if "leak_flow_lpm" in payload:
                state["leak_rate_lpm"] = leak_rate
            if "fault" in payload:
                state["fault"] = payload["fault"]
            self.step()
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            self.step()
            now = self.clock()
            connections = []
            for link in self.connections:
                node = link["source"] if link["source"]["kind"] == "device" else link["target"]
                last = self.last_seen.get(node["node_id"])
                source_fault = (link["source"]["kind"] == "field" and
                                self.states[link["source"]["object_id"]]["fault"])
                quality = "fault" if source_fault else "waiting" if last is None else "stale" if now - last > 2 else "good"
                connections.append({"connection_id": link["connection_id"], "quality": quality, "age_ms": round((now-last)*1000) if last is not None else None})
            return {"objects": [{"object_id": oid, **{key: value for key, value in state.items() if key != "changed"}} for oid, state in self.states.items()], "connections": connections, "process_connections": [{"connection_id": item["connection_id"], "quality": "good"} for item in self.process_connections]}


def create_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/capabilities":
                self.respond(field_runtime_capabilities()); return
            if self.path != "/snapshot":
                self.send_error(404); return
            self.respond(engine.snapshot())

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1_048_576:
                    raise ValueError("invalid frame length")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/exchange": result = engine.exchange(payload)
                elif self.path == "/control": result = engine.control(payload)
                else: self.send_error(404); return
                self.respond(result)
            except (KeyError, ValueError, TypeError) as error:
                self.respond({"detail": str(error)}, 422)

        def respond(self, value, status=200):
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

    return Handler


def main():
    engine = FieldEngine(json.loads(os.environ["FIELD_CONFIG"]))

    print("Independent field simulator listening on internal TCP/8765", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8765), create_handler(engine)).serve_forever()


if __name__ == "__main__":
    main()
