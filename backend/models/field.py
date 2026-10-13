"""Protocol-independent field objects and explicit signal connections."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


class FieldSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["constant", "sine_wave", "process"] = "constant"
    period_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    noise: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    seed: StrictInt | None = Field(default=None, ge=0, le=4294967295)

    @model_validator(mode="after")
    def valid_period(self):
        if self.mode == "sine_wave" and self.period_seconds is None:
            raise ValueError("period_seconds is required for sine_wave simulation")
        return self


class FieldPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)


class FieldBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=256)
    zone_id: str = Field(default="default", min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    position: FieldPosition | None = None


class DigitalField(FieldBase):
    type: Literal["digital_input", "alarm"]
    initial_state: StrictBool = False


class ActuatorField(FieldBase):
    type: Literal["valve", "motor"]
    initial_state: StrictBool = False
    response_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    flow_rate_lpm: float = Field(default=50, gt=0, le=3.4e38, allow_inf_nan=False)


class AnalogField(FieldBase):
    type: Literal["analog_sensor"]
    unit: str = ""
    min_value: float = Field(default=0, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    max_value: float = Field(default=100, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    simulation: FieldSimulation = Field(default_factory=FieldSimulation)

    @model_validator(mode="after")
    def validate_range(self):
        if self.max_value <= self.min_value:
            raise ValueError("max_value must be greater than min_value")
        return self


class PumpField(ActuatorField):
    type: Literal["pump"]
    flow_min: float = Field(default=0, ge=0, le=3.4e38, allow_inf_nan=False)
    flow_max: float = Field(default=100, ge=0, le=3.4e38, allow_inf_nan=False)
    max_head_kpa: float = Field(default=100, ge=0, le=3.4e38, allow_inf_nan=False)
    unit: str = "L/min"

    @model_validator(mode="after")
    def validate_flow_range(self):
        if self.flow_max <= self.flow_min:
            raise ValueError("flow_max must be greater than flow_min")
        return self


class VariableSpeedDriveField(FieldBase):
    """Variable-frequency drive with a REAL speed setpoint and process flow."""
    type: Literal["variable_speed_drive"]
    initial_speed_pct: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    response_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    max_flow_lpm: float = Field(default=100, gt=0, le=3.4e38, allow_inf_nan=False)
    max_head_kpa: float = Field(default=100, ge=0, le=3.4e38, allow_inf_nan=False)


class ProportionalValveField(FieldBase):
    type: Literal["proportional_valve"]
    initial_opening_pct: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    response_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    flow_rate_lpm: float = Field(default=50, gt=0, le=3.4e38, allow_inf_nan=False)


class TankField(FieldBase):
    type: Literal["tank"]
    capacity_liters: float = Field(default=1000, gt=0, le=3.4e38, allow_inf_nan=False)
    height_m: float = Field(default=2, gt=0, le=1000, allow_inf_nan=False)
    initial_level_liters: float = Field(default=500, ge=0, le=3.4e38, allow_inf_nan=False)
    outlet_flow_lpm: float = Field(default=50, ge=0, le=3.4e38, allow_inf_nan=False)
    leak_flow_lpm: float = Field(default=0, ge=0, le=3.4e38, allow_inf_nan=False)
    demand_flow_lpm: float = Field(default=0, ge=0, le=3.4e38, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_capacity(self):
        if self.initial_level_liters > self.capacity_liters:
            raise ValueError("initial tank level cannot exceed capacity")
        return self


class PipeField(FieldBase):
    """Process pipe with a maximum flow and nominal head loss at that flow."""
    type: Literal["pipe"]
    max_flow_lpm: float = Field(default=100, gt=0, le=3.4e38, allow_inf_nan=False)
    pressure_drop_kpa: float = Field(default=0, ge=0, le=3.4e38, allow_inf_nan=False)


class FlowSplitterField(FieldBase):
    """Configured process distributor with a conserved two-way split."""
    type: Literal["flow_splitter"]
    split_ratio_pct: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)


class FlowMixerField(FieldBase):
    """Two-inlet process collector which sums incoming flow."""
    type: Literal["flow_mixer"]


FieldObject = Annotated[DigitalField | ActuatorField | AnalogField | PumpField | VariableSpeedDriveField | ProportionalValveField | TankField | PipeField | FlowSplitterField | FlowMixerField, Field(discriminator="type")]


class FieldEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["field"]
    object_id: str = Field(min_length=1)
    port: str = Field(min_length=1)


class DeviceEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["device"]
    node_id: str = Field(min_length=1)
    address: str = Field(min_length=1)
    channel_id: str | None = Field(default=None, min_length=1)


Endpoint = Annotated[FieldEndpoint | DeviceEndpoint, Field(discriminator="kind")]


class SignalScale(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_min: float = Field(allow_inf_nan=False)
    input_max: float = Field(allow_inf_nan=False)
    output_min: int = Field(ge=0, le=65535)
    output_max: int = Field(ge=0, le=65535)

    @model_validator(mode="after")
    def valid_range(self):
        if self.input_max <= self.input_min or self.output_max <= self.output_min:
            raise ValueError("conversion ranges must increase")
        return self


class SignalConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=128)
    source: Endpoint
    target: Endpoint
    conversion: SignalScale | None = None
    route_enabled: bool | None = None


class ProcessEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str = Field(min_length=1)
    port: str = Field(min_length=1)


class ProcessConnection(BaseModel):
    """Physical flow or measurement link, separate from controller wiring."""
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=128)
    source: ProcessEndpoint
    target: ProcessEndpoint


class FieldControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fault: StrictBool | None = None
    override: StrictBool | StrictInt | StrictFloat | None = None
    demand_flow_lpm: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=3.4e38, allow_inf_nan=False)
    leak_flow_lpm: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=3.4e38, allow_inf_nan=False)


def field_ports(item: FieldBase) -> list[dict]:
    """Directions are relative to the field object, independent of protocols."""
    if item.type == "analog_sensor":
        return [{"port": "value", "direction": "output", "data_type": "REAL", "unit": item.unit}]
    if item.type in {"digital_input", "alarm"}:
        return [{"port": "state", "direction": "output", "data_type": "BOOL", "unit": ""}]
    if item.type == "tank":
        return []
    if item.type == "proportional_valve":
        return [
            {"port": "command", "direction": "input", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
            {"port": "opening", "direction": "output", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
            {"port": "fault", "direction": "output", "data_type": "BOOL", "unit": ""},
        ]
    if item.type == "variable_speed_drive":
        return [
            {"port": "command", "direction": "input", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
            {"port": "speed", "direction": "output", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
            {"port": "flow", "direction": "output", "data_type": "REAL", "unit": "L/min", "min_value": 0, "max_value": item.max_flow_lpm},
            {"port": "fault", "direction": "output", "data_type": "BOOL", "unit": ""},
        ]
    if item.type in {"pipe", "flow_splitter", "flow_mixer"}:
        return []
    ports = [
        {"port": "command", "direction": "input", "data_type": "BOOL", "unit": ""},
        {"port": "state", "direction": "output", "data_type": "BOOL", "unit": ""},
        {"port": "fault", "direction": "output", "data_type": "BOOL", "unit": ""},
    ]
    if item.type == "pump":
        ports.append({"port": "flow", "direction": "output", "data_type": "REAL", "unit": item.unit})
    return ports


def process_ports(item: FieldBase) -> list[dict]:
    if item.type == "flow_splitter":
        return [
            {"port": "process_in", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out_a", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out_b", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "flow_mixer":
        return [
            {"port": "process_in_a", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_in_b", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "pipe":
        return [
            {"port": "process_in", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "pump":
        return [
            {"port": "flow", "direction": "output", "data_type": "REAL", "unit": item.unit},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "variable_speed_drive":
        return [
            {"port": "flow", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "valve":
        return [
            {"port": "process_in", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out", "direction": "output", "data_type": "REAL", "unit": "L/min"},
        ]
    if item.type == "proportional_valve":
        return [
            {"port": "process_in", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "process_out", "direction": "output", "data_type": "REAL", "unit": "L/min"},
        ]
    if item.type == "tank":
        return [
            {"port": "inlet", "direction": "input", "data_type": "REAL", "unit": "L/min"},
            {"port": "outlet", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            {"port": "level", "direction": "output", "data_type": "REAL", "unit": "%"},
            {"port": "pressure", "direction": "output", "data_type": "REAL", "unit": "kPa"},
        ]
    if item.type == "analog_sensor":
        return [{"port": "measurement", "direction": "input", "data_type": "REAL", "unit": item.unit}]
    return []


def validate_process_connections(objects, connections):
    fields = {item.object_id: item for item in objects}
    ids, inputs, outgoing_tank, flow_sources = set(), set(), set(), set()
    for link in connections:
        if link.connection_id in ids:
            raise ValueError("duplicate process connection_id")
        ids.add(link.connection_id)
        source_item, target_item = fields.get(link.source.object_id), fields.get(link.target.object_id)
        if source_item is None or target_item is None or source_item.object_id == target_item.object_id:
            raise ValueError("process connection references an unknown or identical field object")
        source = next((port for port in process_ports(source_item) if port["port"] == link.source.port), None)
        target = next((port for port in process_ports(target_item) if port["port"] == link.target.port), None)
        if source is None or source["direction"] != "output" or target is None or target["direction"] != "input":
            raise ValueError("process links must join a physical output to a compatible physical input")
        if source["data_type"] != target["data_type"] or source["unit"] != target["unit"]:
            raise ValueError("process connection data types and units must match")
        identity = (link.target.object_id, link.target.port)
        if identity in inputs:
            raise ValueError("a physical process input can have only one source")
        inputs.add(identity)
        if link.source.port == "outlet":
            if link.source.object_id in outgoing_tank:
                raise ValueError("a tank outlet may feed only one valve in this process model")
            outgoing_tank.add(link.source.object_id)
        if link.source.port in {"flow", "process_out"}:
            # Pumps and drives may feed parallel process branches. Their
            # aggregate flow is conserved by the field runtime. Intermediate
            # components still have a single outlet to avoid implicit splits.
            can_branch = link.source.port == "flow" and source_item.type in {"pump", "variable_speed_drive"}
            identity = (link.source.object_id, link.source.port)
            if identity in flow_sources and not can_branch:
                raise ValueError("only pump or drive flow outputs can feed parallel process branches")
            flow_sources.add(identity)
        if target_item.type == "analog_sensor" and target_item.simulation.mode != "process":
            raise ValueError("an analog sensor with a process connection must use process simulation")
        if link.source.port in {"level", "pressure"} and target_item.type != "analog_sensor":
            raise ValueError("tank level or pressure can connect only to an analog sensor")

    connected_sensors = {link.target.object_id for link in connections if link.target.port == "measurement"}
    for item in objects:
        if item.type == "analog_sensor" and item.simulation.mode == "process" and item.object_id not in connected_sensors:
            raise ValueError(f"process sensor '{item.object_id}' needs a physical measurement connection")


def validate_connections(objects, connections, runtime_devices):
    from devices import DEVICE_REGISTRY

    fields = {item.object_id: item for item in objects}
    if len(fields) != len(objects):
        raise ValueError("duplicate field object_id")
    nodes = {item["node_id"] for item in runtime_devices}
    if nodes & fields.keys():
        raise ValueError("field object_id must differ from device node_id")
    points = {
        (device["node_id"], point["address"]): point
        for device in runtime_devices
        for point in DEVICE_REGISTRY[device["device_type"]]().build_io_map(device)
    }
    ids, targets = set(), set()
    for link in connections:
        if link.connection_id in ids:
            raise ValueError("duplicate signal connection_id")
        ids.add(link.connection_id)
        if link.source.kind == link.target.kind:
            raise ValueError("a field connection must join a field port and a device point")
        types, units = [], []
        source_range = target_range = None
        for endpoint, direction in ((link.source, "output"), (link.target, "input")):
            if endpoint.kind == "field":
                item = fields.get(endpoint.object_id)
                ports = {port["port"]: port for port in field_ports(item)} if item else {}
                port = ports.get(endpoint.port)
                if port is None or port["direction"] != direction:
                    raise ValueError(f"unknown or invalid {direction} field port '{endpoint.object_id}.{endpoint.port}'")
                types.append(port["data_type"])
                units.append(port["unit"])
                if "min_value" in port and "max_value" in port:
                    if direction == "output":
                        source_range = (port["min_value"], port["max_value"])
                    else:
                        target_range = (port["min_value"], port["max_value"])
                identity = ("field", endpoint.object_id, endpoint.port)
            else:
                if endpoint.channel_id is not None:
                    channel = next((p for (node_id, _), p in points.items() if node_id == endpoint.node_id and p.get('channel_id') == endpoint.channel_id), None)
                    if channel is None:
                        raise ValueError("referenced I/O channel no longer exists")
                    endpoint.address = channel['address']
                point = points.get((endpoint.node_id, endpoint.address))
                if point is None:
                    raise ValueError(f"unknown device point '{endpoint.node_id}:{endpoint.address}'")
                if point["component_type"] == "remote_input":
                    raise ValueError("remote_input is already driven by its S7 peer")
                if point.get('direction') is not None and point['direction'] != direction:
                    raise ValueError("field connection direction differs from the I/O channel direction")
                if point.get("area") == "I" and direction == "output":
                    raise ValueError("S7 input area I cannot be used as an actuator output")
                program_output = point.get("direction") == "output"
                if direction == "output" and point["access"] != "read_write" and not program_output:
                    raise ValueError("actuator commands require a writable device point")
                types.append(point["data_type"])
                units.append(point.get("unit") or "")
                if "min_value" in point and "max_value" in point:
                    if direction == "output":
                        source_range = (point["min_value"], point["max_value"])
                    else:
                        target_range = (point["min_value"], point["max_value"])
                identity = ("device", endpoint.node_id, endpoint.address)
            if direction == "input":
                if identity in targets:
                    raise ValueError("a signal input can have only one source")
                targets.add(identity)
        if link.conversion is not None and types not in (["REAL", "UINT16"], ["UINT16", "REAL"]):
            raise ValueError("conversion supports REAL/UINT16 field signal scaling only")
        if types[0] != types[1] and link.conversion is None:
            raise ValueError(f"incompatible signal types {types[0]} and {types[1]}; explicit conversion is required")
        if units[0] and units[1] and units[0] != units[1] and link.conversion is None:
            raise ValueError("incompatible signal units; explicit conversion is required")
        if source_range and target_range and link.conversion is None:
            if source_range[0] < target_range[0] or source_range[1] > target_range[1]:
                raise ValueError("source signal range exceeds the connected input range")
        if link.conversion is not None and types == ["UINT16", "REAL"]:
            if link.conversion.input_min < 0 or link.conversion.input_max > 65535:
                raise ValueError("UINT16 conversion input range must stay within 0..65535")
            target = link.target
            target_object = fields.get(target.object_id) if target.kind == "field" else None
            target_port = next((port for port in field_ports(target_object) if port["port"] == target.port), None) if target_object else None
            if target_port and "min_value" in target_port:
                if link.conversion.output_min < target_port["min_value"] or link.conversion.output_max > target_port["max_value"]:
                    raise ValueError("conversion output range exceeds the connected field input range")
