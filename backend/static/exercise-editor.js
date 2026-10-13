function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

const operators = ["eq", "ne", "gt", "gte", "lt", "lte"];
const fieldProperties = {
  tank: ["value", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"],
  analog_sensor: ["value", "fault"], digital_input: ["state", "fault"], alarm: ["state"],
  valve: ["state", "flow", "fault"], motor: ["state", "flow", "fault"],
  pump: ["state", "flow", "fault"],
  proportional_valve: ["opening_pct", "flow", "fault"],
  variable_speed_drive: ["speed_pct", "flow", "fault"],
};
const isBooleanFieldProperty = (property) => property === "state" || property === "fault";
const fieldPropertyLabels = {
  state: "Estado", fault: "Fallo activo", value: "Medida", speed_pct: "Velocidad (%)",
  opening_pct: "Apertura (%)", flow: "Caudal", pressure_kpa: "Presión (kPa)",
  volume_liters: "Volumen (L)", inflow_lpm: "Caudal de entrada (L/min)",
  outflow_lpm: "Caudal de salida (L/min)",
};
const fieldEventTypes = new Set(["tank", "analog_sensor", "digital_input", "alarm", "valve", "proportional_valve", "motor", "pump"]);

function numericValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error("Introduce un valor numérico válido.");
  return number;
}

function typedValue(value, boolean) {
  return boolean ? value === "true" : numericValue(value);
}

function valueControl(value, boolean, label, attributes = "") {
  if (boolean) return `<label>${escapeHtml(label)}<select ${attributes}><option value="true" ${value === true ? "selected" : ""}>Verdadero / activo</option><option value="false" ${value === false ? "selected" : ""}>Falso / inactivo</option></select></label>`;
  return `<label>${escapeHtml(label)}<input type="number" step="any" value="${escapeHtml(value ?? 0)}" ${attributes} required></label>`;
}

function options(items, selected, emptyLabel = "Selecciona…") {
  const content = items.map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
  return `<option value="">${escapeHtml(emptyLabel)}</option>${content}`;
}

function newId(prefix) {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  return `${prefix}-${random}`;
}

export class ExerciseEditor {
  constructor(root) {
    this.root = root;
    this.eventsRoot = root.querySelector("[data-exercise-events]");
    this.objectivesRoot = root.querySelector("[data-exercise-objectives]");
    this.name = root.querySelector("[data-exercise-name]");
    this.description = root.querySelector("[data-exercise-description]");
    this.successRule = root.querySelector("[data-exercise-success-rule]");
    this.passingScore = root.querySelector("[data-exercise-passing-score]");
    this.points = [];
    this.devices = [];
    this.fields = [];
    this.routes = [];
    this.events = [];
    this.objectives = [];
    root.querySelector("[data-exercise-add-event]")?.addEventListener("click", () => this.addEvent());
    root.querySelector("[data-exercise-add-objective]")?.addEventListener("click", () => this.addObjective());
    root.addEventListener("click", (event) => this.onClick(event));
    root.addEventListener("change", (event) => this.onChange(event));
    root.addEventListener("input", (event) => this.onInput(event));
  }

  setContext({ points = [], devices = [], fields = [], routes = [] } = {}) {
    this.points = points;
    this.devices = devices;
    this.fields = fields;
    this.routes = routes.filter((route) => route.enabled !== false);
    this.render();
  }

  load(definition) {
    this.name.value = definition.name || "";
    this.description.value = definition.description || "";
    this.successRule.value = definition.success_rule || "all";
    this.passingScore.value = definition.passing_score ?? 100;
    this.events = (definition.events || []).map((item) => ({ ...item }));
    this.objectives = (definition.objectives || []).map((item) => ({
      ...item,
      ...(item.kind === "audit_event" ? {
        details_match_text: JSON.stringify(item.details_match || {}, null, 2),
      } : {}),
      ...(item.kind === "protocol_write_policy" ? {
        allowed_source_ips_text: (item.allowed_source_ips || []).join("\n"),
      } : {}),
    }));
    this.render();
  }

  addEvent() {
    if (this.events.length >= 64) return;
    const point = this.points.find((item) => item.access === "read_write");
    const device = this.devices[0];
    const field = this.fields.find((item) => fieldEventTypes.has(item.type));
    const kind = point ? "write_point" : device ? "node_lifecycle" : "field_control";
    this.events.push({
      event_id: newId("event"), at_seconds: 0, kind,
      ...(kind === "write_point" ? { node_id: point.node_id, address: point.address, value: point.data_type === "BOOL" ? false : 0 } : {}),
      ...(kind === "node_lifecycle" ? { node_id: device.node_id, action: "restart" } : {}),
      ...(kind === "field_control" && field ? this.defaultFieldControl(field) : {}),
    });
    this.render();
  }

  defaultFieldControl(field) {
    if (["valve", "proportional_valve", "motor", "pump"].includes(field.type)) return { object_id: field.object_id, fault: false };
    if (field.type === "analog_sensor") return { object_id: field.object_id, override: 0 };
    if (["digital_input", "alarm"].includes(field.type)) return { object_id: field.object_id, override: false };
    if (field.type === "tank") return { object_id: field.object_id, demand_flow_lpm: 0 };
    return { object_id: field.object_id, fault: false };
  }

  addObjective() {
    if (this.objectives.length >= 32) return;
    const point = this.points[0];
    const field = this.fields.find((item) => (fieldProperties[item.type] || []).length);
    const kind = point ? "point_value" : field ? "field_value" : "audit_event";
    this.objectives.push({
      objective_id: newId("objective"), description: "Condición de éxito", weight: 1, kind,
      ...(kind === "point_value" ? { node_id: point.node_id, address: point.address, operator: "eq", value: point.data_type === "BOOL" ? true : 0, duration_seconds: 0 } : {}),
      ...(kind === "field_value" ? { object_id: field.object_id, property: fieldProperties[field.type][0], operator: "eq", value: fieldProperties[field.type][0] === "state" ? true : 0, duration_seconds: 0 } : {}),
      ...(kind === "audit_event" ? { event_type: "operator.field_control" } : {}),
    });
    this.render();
  }

  onClick(event) {
    const removeEvent = event.target.closest("[data-exercise-remove-event]");
    if (removeEvent) {
      this.events.splice(Number(removeEvent.dataset.exerciseRemoveEvent), 1);
      this.render();
    }
    const removeObjective = event.target.closest("[data-exercise-remove-objective]");
    if (removeObjective) {
      this.objectives.splice(Number(removeObjective.dataset.exerciseRemoveObjective), 1);
      this.render();
    }
    const addStep = event.target.closest("[data-exercise-add-step]");
    if (addStep) {
      const objective = this.objectives[Number(addStep.dataset.exerciseAddStep)];
      const first = objective?.steps?.[0];
      if (objective?.kind === "field_sequence" && objective.steps.length < 16 && first) {
        objective.steps.push({ ...first });
        this.render();
      }
    }
    const removeStep = event.target.closest("[data-exercise-remove-step]");
    if (removeStep) {
      const [objectiveIndex, stepIndex] = removeStep.dataset.exerciseRemoveStep.split(":").map(Number);
      const objective = this.objectives[objectiveIndex];
      if (objective?.steps?.length > 2) objective.steps.splice(stepIndex, 1);
      this.render();
    }
  }

  onInput(event) {
    const target = event.target;
    if (target.matches("[data-exercise-event-field]")) this.updateEvent(target);
    if (target.matches("[data-exercise-objective-field]")) this.updateObjective(target);
    if (target.matches("[data-exercise-sequence-step-field]")) this.updateSequenceStep(target);
  }

  onChange(event) {
    const target = event.target;
    if (target.matches("[data-exercise-event-field]")) {
      this.updateEvent(target);
      if (["kind", "target", "control", "source_node_id", "protocol_target", "interception_target", "interception_function", "interception_phase", "route_id", "action"].includes(target.dataset.exerciseEventField)) this.render();
    }
    if (target.matches("[data-exercise-objective-field]")) {
      this.updateObjective(target);
      if (["kind", "target", "property", "expectation"].includes(target.dataset.exerciseObjectiveField)) this.render();
    }
    if (target.matches("[data-exercise-sequence-step-field]")) {
      this.updateSequenceStep(target);
      if (["target", "property"].includes(target.dataset.exerciseSequenceStepField)) this.render();
    }
  }

  updateEvent(control) {
    const index = Number(control.dataset.exerciseEventIndex);
    const key = control.dataset.exerciseEventField;
    const item = this.events[index];
    if (!item) return;
    if (key === "kind") {
      const eventId = item.event_id;
      const atSeconds = item.at_seconds;
      const replaceEvent = (values) => {
        Object.keys(item).forEach((field) => delete item[field]);
        Object.assign(item, { event_id: eventId, at_seconds: atSeconds, ...values });
      };
      if (control.value === "write_point") {
        const point = this.points.find((entry) => entry.access === "read_write");
        replaceEvent({ kind: control.value, node_id: point?.node_id || "",
          address: point?.address || "", value: point?.data_type === "BOOL" ? false : 0 });
      } else if (control.value === "protocol_write") {
        const station = this.participantStations()[0];
        const point = this.protocolWritablePoints()[0];
        replaceEvent({ kind: control.value, source_node_id: station?.node_id || "",
          target_node_id: point?.node_id || "", address: point?.address || "",
          value: point?.data_type === "BOOL" ? false : 0 });
        const route = this.protocolRoute(station?.node_id, point?.node_id);
        if (route) item.route_id = route.route_id;
        else delete item.route_id;
      } else if (control.value === "modbus_interception") {
        const route = this.modbusInspectionRoutes()[0];
        const point = route ? this.modbusRegisterPoints(route.route_id)[0] : null;
        replaceEvent({ kind: control.value, route_id: route?.route_id || "", action: "set",
          rules: route && point ? [this.defaultModbusRule(route, point)] : [] });
      } else if (control.value === "node_lifecycle") replaceEvent({ kind: control.value,
        node_id: this.devices[0]?.node_id || "", action: "restart" });
      else if (control.value === "network_route") replaceEvent({ kind: control.value,
        route_id: this.routes[0]?.route_id || "", action: "interrupt" });
      else replaceEvent({ kind: control.value,
        ...this.defaultFieldControl(this.fields.find((field) => fieldEventTypes.has(field.type)) || { object_id: "", type: "" }) });
      return;
    }
    if (key === "target" && item.kind === "write_point") {
      const [nodeId, address] = control.value.split("\u001f");
      const point = this.points.find((entry) => entry.node_id === nodeId && entry.address === address);
      if (point) Object.assign(item, { node_id: point.node_id, address: point.address, value: point.data_type === "BOOL" ? false : 0 });
      return;
    }
    if (key === "protocol_target") {
      const [nodeId, address] = control.value.split("\u001f");
      const point = this.protocolWritablePoints().find((entry) => entry.node_id === nodeId && entry.address === address);
      if (point) {
        item.target_node_id = point.node_id;
        item.address = point.address;
        item.value = point.data_type === "BOOL" ? false : 0;
        const route = this.protocolRoute(item.source_node_id, point.node_id);
        if (route) item.route_id = route.route_id;
        else delete item.route_id;
      }
      return;
    }
    if (key === "interception_target" && item.kind === "modbus_interception" && item.action === "set") {
      const route = this.modbusInspectionRoutes().find((entry) => entry.route_id === item.route_id);
      const functionCode = item.rules?.[0]?.function_code ?? 3;
      const point = route && this.modbusRegisterPoints(route.route_id, functionCode)
        .find((entry) => entry.address === control.value);
      if (route && point) item.rules = [this.defaultModbusRule(route, point, item.rules?.[0]?.rule_id, functionCode, item.rules?.[0]?.phase)];
      return;
    }
    if (key === "interception_phase" && item.kind === "modbus_interception" && item.rules?.[0]?.function_code === 23) {
      item.rules[0].phase = control.value;
      return;
    }
    if (key === "interception_function" && item.kind === "modbus_interception" && item.action === "set") {
      const route = this.modbusInspectionRoutes().find((entry) => entry.route_id === item.route_id);
      const functionCode = Number(control.value);
      const point = route && this.modbusRegisterPoints(route.route_id, functionCode)[0];
      item.rules = route && point ? [this.defaultModbusRule(route, point, item.rules?.[0]?.rule_id, functionCode)] : [];
      return;
    }
    if (key === "route_id" && item.kind === "modbus_interception") {
      item.route_id = control.value;
      if (item.action === "set") {
        const route = this.modbusInspectionRoutes().find((entry) => entry.route_id === item.route_id);
        const functionCode = item.rules?.[0]?.function_code ?? 3;
        const point = route && this.modbusRegisterPoints(route.route_id, functionCode)[0];
        item.rules = route && point ? [this.defaultModbusRule(route, point, item.rules?.[0]?.rule_id, functionCode, item.rules?.[0]?.phase)] : [];
      }
      return;
    }
    if (key === "action" && item.kind === "modbus_interception") {
      item.action = control.value;
      if (item.action === "clear") delete item.rules;
      else {
        const route = this.modbusInspectionRoutes().find((entry) => entry.route_id === item.route_id);
        const functionCode = item.rules?.[0]?.function_code ?? 3;
        const point = route && this.modbusRegisterPoints(route.route_id, functionCode)[0];
        item.rules = route && point ? [this.defaultModbusRule(route, point, undefined, functionCode)] : [];
      }
      return;
    }
    if (key === "source_node_id" && item.kind === "protocol_write") {
      item.source_node_id = control.value;
      const route = this.protocolRoute(item.source_node_id, item.target_node_id);
      if (route) item.route_id = route.route_id;
      else delete item.route_id;
      return;
    }
    if (key === "target" && item.kind === "field_control") {
      const field = this.fields.find((entry) => entry.object_id === control.value);
      Object.assign(item, { object_id: control.value });
      delete item.fault; delete item.override; delete item.demand_flow_lpm;
      if (["valve", "proportional_valve", "motor", "pump"].includes(field?.type)) item.fault = false;
      else if (["analog_sensor", "digital_input", "alarm"].includes(field?.type)) item.override = field?.type === "analog_sensor" ? 0 : false;
      else if (field?.type === "tank") item.demand_flow_lpm = 0;
      return;
    }
    if (key === "control") {
      delete item.fault; delete item.override; delete item.demand_flow_lpm;
      if (control.value === "fault") item.fault = false;
      if (control.value === "override") item.override = false;
      if (control.value === "demand_flow_lpm") item.demand_flow_lpm = 0;
      return;
    }
    if (key === "value") {
      const pointNodeId = item.kind === "protocol_write" ? item.target_node_id : item.node_id;
      const point = this.points.find((entry) => entry.node_id === pointNodeId && entry.address === item.address);
      item.value = typedValue(control.value, point?.data_type === "BOOL" || control.dataset.valueType === "boolean");
      return;
    }
    if (key === "at_seconds") item.at_seconds = Math.max(0, Math.min(86400, Math.trunc(numericValue(control.value))));
    else if (key === "node_id") item.node_id = control.value;
    else if (key === "route_id") {
      if (control.value) item.route_id = control.value;
      else delete item.route_id;
    }
    else if (key === "action") item.action = control.value;
    else if (key === "replacement_value" && item.kind === "modbus_interception" && item.rules?.[0]) {
      const isCoil = [1, 2, 5, 15].includes(item.rules[0].function_code);
      item.rules[0].replacement_value = isCoil ? Number(typedValue(control.value, true))
        : Math.max(0, Math.min(65535, Math.trunc(numericValue(control.value))));
    }
    else if (key === "fault") item.fault = control.value === "true";
    else if (key === "override") item.override = control.dataset.valueType === "boolean" ? control.value === "true" : numericValue(control.value);
    else if (key === "demand_flow_lpm") item.demand_flow_lpm = numericValue(control.value);
  }

  participantStations() {
    return this.devices.filter((device) => device.device_type === "attacker-station"
      && device.config?.participant_access_enabled === true);
  }

  protocolWritablePoints() {
    return this.points.filter((point) => point.access === "read_write");
  }

  protocolPolicyTargets() {
    return this.devices.flatMap((device) => {
      if (device.device_type === "plc-s7") {
        return [{ node_id: device.node_id, name: device.name, protocol: "s7" }];
      }
      if (device.device_type === "modbus-tcp" && (device.config?.transport || "tcp") === "tcp") {
        return [{ node_id: device.node_id, name: device.name, protocol: "modbus-tcp" }];
      }
      return [];
    });
  }

  modbusInspectionRoutes() {
    const byId = new Map(this.devices.map((device) => [device.node_id, device]));
    return this.routes.filter((route) => {
      const source = byId.get(route.source_node_id);
      const target = byId.get(route.target_node_id);
      return route.protocol === "tcp" && source?.device_type === "attacker-station"
        && source.config?.participant_access_enabled === true && target?.device_type === "modbus-tcp"
        && (target.config?.transport || "tcp") === "tcp"
        && Number(route.tcp_port) === Number(target.config?.port ?? 502)
        && source.zone_id !== target.zone_id;
    });
  }

  modbusRegisterPoints(routeId, functionCode = 3) {
    const route = this.modbusInspectionRoutes().find((entry) => entry.route_id === routeId);
    if (!route) return [];
      return this.points.filter((point) => point.node_id === route.target_node_id
      && ([1, 5, 15].includes(functionCode)
        ? /^coil:\d+$/.test(point.address) && (![5, 15].includes(functionCode) || point.access === "read_write")
        : functionCode === 2
          ? /^discrete_input:\d+$/.test(point.address)
        : functionCode === 4
        ? /^input_register:\d+$/.test(point.address)
        : /^holding_register:\d+$/.test(point.address)
          && (![6, 16, 22, 23].includes(functionCode) || point.access === "read_write")));
  }

  defaultModbusRule(route, point, ruleId = newId("rewrite"), functionCode = null, phase = "read") {
    const target = this.devices.find((device) => device.node_id === route.target_node_id);
    return {
      rule_id: ruleId,
      unit_id: Number(target?.config?.unit_id ?? 1),
      function_code: functionCode || (point.address.startsWith("input_register:") ? 4
        : point.address.startsWith("discrete_input:") ? 2
        : point.address.startsWith("coil:") ? 15 : 3),
      address: Number(point.address.split(":")[1]),
      replacement_value: 0,
      ...(functionCode === 23 ? { phase: phase || "read" } : {}),
    };
  }

  protocolRoute(sourceNodeId, targetNodeId) {
    const target = this.devices.find((device) => device.node_id === targetNodeId);
    const expectedPort = target?.device_type === "plc-s7" ? 102 : Number(target?.config?.port || 502);
    return this.routes.find((route) => route.source_node_id === sourceNodeId
      && route.target_node_id === targetNodeId && route.protocol === "tcp"
      && Number(route.tcp_port) === expectedPort);
  }

  updateObjective(control) {
    const index = Number(control.dataset.exerciseObjectiveIndex);
    const key = control.dataset.exerciseObjectiveField;
    const item = this.objectives[index];
    if (!item) return;
    if (key === "kind") {
      const id = item.objective_id;
      const description = item.description;
      const weight = item.weight;
      const replaceObjective = (values) => {
        Object.keys(item).forEach((field) => delete item[field]);
        Object.assign(item, { objective_id: id, description, weight, ...values });
      };
      if (control.value === "point_value") {
        const point = this.points[0];
        replaceObjective({ kind: control.value,
          node_id: point?.node_id || "", address: point?.address || "", operator: "eq", value: point?.data_type === "BOOL" ? true : 0, duration_seconds: 0 });
      } else if (control.value === "field_value") {
        const field = this.fields.find((entry) => (fieldProperties[entry.type] || []).length);
        const property = field ? fieldProperties[field.type][0] : "value";
        replaceObjective({ kind: control.value,
          object_id: field?.object_id || "", property, operator: "eq", value: isBooleanFieldProperty(property), duration_seconds: 0 });
      } else if (control.value === "field_trend") {
        const numeric = this.fields.find((entry) => (fieldProperties[entry.type] || []).some((property) => !isBooleanFieldProperty(property)));
        const property = (fieldProperties[numeric?.type] || []).find((value) => !isBooleanFieldProperty(value)) || "value";
        replaceObjective({ kind: control.value,
          object_id: numeric?.object_id || "", property, direction: "rising", window_seconds: 10, minimum_delta: 0 });
      } else if (control.value === "field_sequence") {
        const field = this.fields.find((entry) => (fieldProperties[entry.type] || []).length);
        const property = fieldProperties[field?.type]?.[0] || "value";
        const step = { object_id: field?.object_id || "", property, operator: "eq", value: isBooleanFieldProperty(property) };
        replaceObjective({ kind: control.value,
          steps: [{ ...step }, { ...step }], max_gap_seconds: null });
      } else if (control.value === "protocol_write_policy") {
        const target = this.protocolPolicyTargets()[0];
        replaceObjective({ kind: control.value,
          target_node_id: target?.node_id || "", protocol: target?.protocol || "s7",
          allowed_source_ips: [], allowed_source_ips_text: "", expectation: "detected" });
      } else if (control.value === "audit_event") replaceObjective({ kind: control.value, event_type: "operator.field_control", expectation: "occurred", fail_fast: false });
      else replaceObjective({ kind: control.value,
        event_types: ["operator.point_written", "operator.field_control"], max_gap_seconds: null });
      return;
    }
    if (key === "target" && item.kind === "point_value") {
      const [nodeId, address] = control.value.split("\u001f");
      const point = this.points.find((entry) => entry.node_id === nodeId && entry.address === address);
      if (point) Object.assign(item, { node_id: point.node_id, address: point.address, value: point.data_type === "BOOL" });
      return;
    }
    if (key === "target" && item.kind === "protocol_write_policy") {
      const [nodeId, protocol] = control.value.split("\u001f");
      item.target_node_id = nodeId || "";
      item.protocol = protocol || "s7";
      return;
    }
    if (key === "expectation") {
      item.expectation = control.value;
      if (control.value !== "absent") item.fail_fast = false;
      return;
    }
    if (key === "target" && item.kind === "field_value") {
      const field = this.fields.find((entry) => entry.object_id === control.value && (fieldProperties[entry.type] || []).length);
      const property = field ? fieldProperties[field.type][0] : "value";
        Object.assign(item, { object_id: control.value, property, value: isBooleanFieldProperty(property) });
      return;
    }
    if (key === "target" && item.kind === "field_trend") {
      const field = this.fields.find((entry) => entry.object_id === control.value && (fieldProperties[entry.type] || []).some((property) => !isBooleanFieldProperty(property)));
      item.object_id = control.value;
      item.property = (fieldProperties[field?.type] || []).find((value) => !isBooleanFieldProperty(value)) || "value";
      return;
    }
    if (key === "weight") item.weight = Math.max(1, Math.min(100, Math.trunc(numericValue(control.value))));
    else if (key === "duration_seconds") item.duration_seconds = Math.max(0, Math.min(86400, numericValue(control.value)));
    else if (key === "window_seconds") item.window_seconds = Math.max(2, Math.min(300, Math.trunc(numericValue(control.value))));
    else if (key === "minimum_delta") item.minimum_delta = Math.max(0, numericValue(control.value));
    else if (key === "event_types") item.event_types = control.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    else if (key === "details_match_text") {
      item.details_match_text = control.value;
      try {
        const parsed = JSON.parse(control.value || "{}");
        control.setCustomValidity(parsed && !Array.isArray(parsed) && typeof parsed === "object"
          ? "" : "Los filtros deben ser un objeto JSON.");
      } catch {
        control.setCustomValidity("Escribe un objeto JSON válido, por ejemplo {\"node_id\":\"plc-1\"}.");
      }
    }
    else if (key === "max_gap_seconds") item.max_gap_seconds = control.value === "" ? null : Math.max(0.001, Math.min(86400, numericValue(control.value)));
    else if (key === "fail_fast") item.fail_fast = control.checked;
    else if (key === "value") {
      const point = item.kind === "point_value" && this.points.find((entry) => entry.node_id === item.node_id && entry.address === item.address);
      item.value = typedValue(control.value, point?.data_type === "BOOL" || item.kind === "field_value" && isBooleanFieldProperty(item.property));
    } else if (key === "property") {
      item.property = control.value;
      if (isBooleanFieldProperty(control.value)) item.value = true;
      else if (typeof item.value === "boolean") item.value = 0;
    } else item[key] = control.value;
  }

  updateSequenceStep(control) {
    const objective = this.objectives[Number(control.dataset.exerciseObjectiveIndex)];
    const key = control.dataset.exerciseSequenceStepField;
    if (objective?.kind === "field_sequence" && key === "max_gap_seconds") {
      objective.max_gap_seconds = control.value === "" ? null : Math.max(0.001, Math.min(86400, numericValue(control.value)));
      return;
    }
    const step = objective?.steps?.[Number(control.dataset.exerciseSequenceStepIndex)];
    if (!step) return;
    if (key === "target") {
      const field = this.fields.find((entry) => entry.object_id === control.value && (fieldProperties[entry.type] || []).length);
      step.object_id = control.value;
      step.property = fieldProperties[field?.type]?.[0] || "value";
      step.operator = "eq";
      step.value = isBooleanFieldProperty(step.property);
    } else if (key === "property") {
      step.property = control.value;
      step.operator = "eq";
      step.value = isBooleanFieldProperty(control.value) ? true : typeof step.value === "boolean" ? 0 : step.value;
    } else if (key === "operator") step.operator = control.value;
    else if (key === "value") step.value = typedValue(control.value, isBooleanFieldProperty(step.property));
  }

  eventRow(item, index) {
    const common = `<label>Segundo de ejecución<input type="number" min="0" max="86400" step="1" value="${item.at_seconds}" data-exercise-event-index="${index}" data-exercise-event-field="at_seconds"></label>`;
    let controls = "";
    if (item.kind === "write_point") {
      const writable = this.points.filter((point) => point.access === "read_write");
      const selected = `${item.node_id}\u001f${item.address}`;
      const point = writable.find((entry) => entry.node_id === item.node_id && entry.address === item.address);
      const range = point ? `min="${point.min_value ?? 0}" max="${point.max_value ?? (point.data_type === "REAL" ? 3.4e38 : 65535)}"` : "";
      controls = `<label>Salida<select data-exercise-event-index="${index}" data-exercise-event-field="target">${options(writable.map((p) => [`${p.node_id}\u001f${p.address}`, `${p.device_name} · ${p.component_name} (${p.address})`]), selected, "Sin salidas escribibles")}</select></label>${valueControl(item.value, point?.data_type === "BOOL", "Valor", `data-exercise-event-index="${index}" data-exercise-event-field="value" ${point?.data_type === "BOOL" ? "" : `step=any ${range}`}`)}`;
    } else if (item.kind === "protocol_write") {
      const stations = this.participantStations();
      const writable = this.protocolWritablePoints();
      const point = writable.find((entry) => entry.node_id === item.target_node_id && entry.address === item.address);
      const selected = `${item.target_node_id}\u001f${item.address}`;
      const route = this.protocolRoute(item.source_node_id, item.target_node_id);
      const selectedRoute = item.route_id || "";
      const source = stations.find((device) => device.node_id === item.source_node_id);
      const target = this.devices.find((device) => device.node_id === item.target_node_id);
      const sameZone = source?.zone_id === target?.zone_id;
      const routeOptions = route ? [[route.route_id, `${route.source_node_id} → ${route.target_node_id} · TCP/${route.tcp_port}`]]
        : [["", sameZone ? "Conexión directa (misma zona; sin ruta)" : "No hay ruta TCP habilitada al puerto de protocolo"]];
      const range = point ? `min="${point.min_value ?? 0}" max="${point.max_value ?? (point.data_type === "REAL" ? 3.4e38 : 65535)}"` : "";
      controls = `<label>Estación participante<select data-exercise-event-index="${index}" data-exercise-event-field="source_node_id">${options(stations.map((device) => [device.node_id, device.name]), item.source_node_id, "Sin estación con acceso habilitado")}</select></label><label>Punto de protocolo<select data-exercise-event-index="${index}" data-exercise-event-field="protocol_target">${options(writable.map((p) => [`${p.node_id}\u001f${p.address}`, `${p.device_name} · ${p.component_name} (${p.address})`]), selected, "Sin salidas protocolarias escribibles")}</select></label><label>Ruta TCP<select data-exercise-event-index="${index}" data-exercise-event-field="route_id">${options(routeOptions, selectedRoute)}</select></label>${valueControl(item.value, point?.data_type === "BOOL", "Valor", `data-exercise-event-index="${index}" data-exercise-event-field="value" ${point?.data_type === "BOOL" ? "" : `step=any ${range}`}`)}<p>Envía S7/Modbus desde la estación del escenario. Entre zonas se necesita una ruta TCP al puerto exacto del servicio.</p>`;
    } else if (item.kind === "modbus_interception") {
      const routes = this.modbusInspectionRoutes();
      const route = routes.find((entry) => entry.route_id === item.route_id);
      const rule = item.rules?.[0];
      const functionCode = rule?.function_code ?? 3;
      const registers = route ? this.modbusRegisterPoints(route.route_id, functionCode) : [];
      const selectedAddress = rule ? `${functionCode === 4 ? "input_register" : functionCode === 2 ? "discrete_input" : [1, 5, 15].includes(functionCode) ? "coil" : "holding_register"}:${rule.address}` : "";
      const registerOptions = registers.map((point) => [point.address,
        `${point.device_name} · ${point.component_name} (${point.address})`]);
      const actionOptions = [["set", "Activar sustitución"], ["clear", "Retirar sustituciones"]];
      const functionOptions = [["1", "FC01 · sustituir lectura de coils"], ["2", "FC02 · sustituir lectura de discrete inputs"], ["3", "FC03 · leer holding register"], ["4", "FC04 · leer input register"], ["5", "FC05 · alterar escritura de una coil"], ["6", "FC06 · alterar escritura de un holding register"], ["15", "FC15 · alterar escritura de varias coils"], ["16", "FC16 · alterar escritura de varios holding registers"], ["22", "FC22 · forzar valor en Mask Write Register"], ["23", "FC23 · sustituir lectura de Read/Write Multiple Registers"]];
      const phase = rule?.phase || "read";
      const changesWrite = [5, 6, 15, 16, 22].includes(functionCode) || (functionCode === 23 && phase === "write");
      const coilRead = [1, 2].includes(functionCode);
      const coilWrite = [5, 15].includes(functionCode);
      const readWriteExplanation = [1, 2].includes(functionCode)
        ? `El gateway sustituye el bit de FC${functionCode} en la respuesta; el valor de la coil/entrada en el servidor permanece intacto.`
        : functionCode === 23
        ? phase === "write"
          ? "El gateway sustituye solo los holding registers seleccionados en la escritura FC23 antes de enviarlos al servidor; la respuesta de lectura no se modifica."
          : "El gateway sustituye solo el rango de lectura de la respuesta FC23; los registros enviados en la parte de escritura llegan sin cambios al servidor."
        : changesWrite
        ? `El gateway sustituye el valor en la petición FC${functionCode} antes de que llegue al servidor; puede modificar el proceso conectado. El cliente recibe la confirmación del servidor.`
        : "El servidor conserva su valor y el cliente observa una respuesta sustituida.";
      controls = `<label>Ruta participante → Modbus TCP<select data-exercise-event-index="${index}" data-exercise-event-field="route_id">${options(routes.map((entry) => [entry.route_id, `${entry.source_node_id} → ${entry.target_node_id} · TCP/${entry.tcp_port}`]), item.route_id, "Sin ruta Modbus TCP inspeccionable")}</select></label><label>Acción<select data-exercise-event-index="${index}" data-exercise-event-field="action">${options(actionOptions, item.action)}</select></label>${item.action === "set" ? `<label>Función Modbus<select data-exercise-event-index="${index}" data-exercise-event-field="interception_function">${options(functionOptions, String(functionCode))}</select></label>${functionCode === 23 ? `<label>Parte de FC23<select data-exercise-event-index="${index}" data-exercise-event-field="interception_phase">${options([["read", "Respuesta de lectura"], ["write", "Petición de escritura"]], phase)}</select></label>` : ""}<label>Punto objetivo<select data-exercise-event-index="${index}" data-exercise-event-field="interception_target">${options(registerOptions, selectedAddress, "La ruta no tiene puntos compatibles con esta función")}</select></label>${valueControl(coilWrite || coilRead ? Boolean(rule?.replacement_value) : rule?.replacement_value ?? 0, coilWrite || coilRead, changesWrite ? (coilWrite ? "Estado de coil que recibirá el servidor" : "Valor UINT16 que escribirá el servidor") : coilRead ? "Estado booleano observado por el cliente" : "Valor UINT16 observado por el cliente", `data-exercise-event-index="${index}" data-exercise-event-field="replacement_value" ${coilWrite || coilRead ? "data-value-type=boolean" : "min=\"0\" max=\"65535\" step=\"1\""} required`)}<p>${readWriteExplanation} Añade un evento «Retirar sustituciones» para esta ruta en un segundo posterior. Cancelar o terminar el ejercicio también limpia la regla.</p>` : `<p>Retira las sustituciones activas de esta ruta.</p>`}`;
    } else if (item.kind === "node_lifecycle") {
      controls = `<label>Dispositivo<select data-exercise-event-index="${index}" data-exercise-event-field="node_id">${options(this.devices.map((device) => [device.node_id, device.name]), item.node_id)}</select></label><label>Acción<select data-exercise-event-index="${index}" data-exercise-event-field="action">${options([["start", "Iniciar"], ["stop", "Detener"], ["restart", "Reiniciar"]], item.action)}</select></label>`;
    } else if (item.kind === "network_route") {
      const deviceNames = new Map(this.devices.map((device) => [device.node_id, device.name]));
      controls = `<label>Ruta declarada<select data-exercise-event-index="${index}" data-exercise-event-field="route_id">${options(this.routes.map((route) => [route.route_id, `${deviceNames.get(route.source_node_id) || route.source_node_id} → ${deviceNames.get(route.target_node_id) || route.target_node_id} · ${route.protocol.toUpperCase()}`]), item.route_id)}</select></label><label>Acción<select data-exercise-event-index="${index}" data-exercise-event-field="action">${options([["interrupt", "Interrumpir comunicación"], ["restore", "Restaurar comunicación"]], item.action)}</select></label>`;
    } else {
      const field = this.fields.find((entry) => entry.object_id === item.object_id);
      const controlsForType = field?.type === "tank" ? [["demand_flow_lpm", "Cambiar demanda (L/min)"]] : ["valve", "proportional_valve", "motor", "pump"].includes(field?.type) ? [["fault", "Inyectar fallo"]] : [["override", "Forzar lectura"]];
      const control = item.fault !== undefined ? "fault" : item.demand_flow_lpm !== undefined ? "demand_flow_lpm" : "override";
      const value = control === "fault" ? item.fault : control === "demand_flow_lpm" ? item.demand_flow_lpm : item.override;
      const bounds = field?.type === "analog_sensor" ? `min="${field.min_value}" max="${field.max_value}"` : control === "demand_flow_lpm" ? 'min="0"' : "";
      controls = `<label>Objeto de campo<select data-exercise-event-index="${index}" data-exercise-event-field="target">${options(this.fields.filter((object) => fieldEventTypes.has(object.type)).map((object) => [object.object_id, object.name]), item.object_id)}</select></label><label>Acción<select data-exercise-event-index="${index}" data-exercise-event-field="control">${options(controlsForType, control)}</select></label>${valueControl(value, control === "fault" || control === "override" && field?.type !== "analog_sensor", "Valor", `data-exercise-event-index="${index}" data-exercise-event-field="${control}" data-value-type="${control === "fault" || control === "override" && field?.type !== "analog_sensor" ? "boolean" : "number"} ${bounds}`)}`;
    }
    const kinds = [
      ...(this.points.some((point) => point.access === "read_write") ? [["write_point", "Escribir una salida S7/Modbus"]] : []),
      ...(this.participantStations().length && this.protocolWritablePoints().length ? [["protocol_write", "Escritura protocolaria desde participante"]] : []),
      ...(this.modbusInspectionRoutes().some((route) => [1, 2, 3, 4, 5, 6, 15, 16, 22, 23].some((functionCode) => this.modbusRegisterPoints(route.route_id, functionCode).length)) ? [["modbus_interception", "Alterar comunicación Modbus FC01/FC02/FC03/FC04/FC05/FC06/FC15/FC16/FC22/FC23 por ruta"]] : []),
      ...(this.devices.length ? [["node_lifecycle", "Acción sobre un dispositivo"]] : []),
      ...(this.routes.length ? [["network_route", "Interrumpir/restaurar una ruta de red"]] : []),
      ...(this.fields.some((object) => fieldEventTypes.has(object.type)) ? [["field_control", "Incidente en objeto de campo"]] : []),
    ];
    return `<article class="exercise-card"><header><strong>Evento ${index + 1}</strong><button class="tiny-button" type="button" data-exercise-remove-event="${index}">Eliminar</button></header><div class="exercise-fields"><label>Tipo<select data-exercise-event-index="${index}" data-exercise-event-field="kind">${options(kinds, item.kind)}</select></label>${common}${controls}</div></article>`;
  }

  objectiveRow(item, index) {
    let controls = "";
    const common = `<label>Descripción<input type="text" maxlength="240" value="${escapeHtml(item.description)}" data-exercise-objective-index="${index}" data-exercise-objective-field="description" required></label><label>Peso<input type="number" min="1" max="100" step="1" value="${item.weight}" data-exercise-objective-index="${index}" data-exercise-objective-field="weight"></label>`;
    if (item.kind === "point_value") {
      const point = this.points.find((entry) => entry.node_id === item.node_id && entry.address === item.address);
      const selected = `${item.node_id}\u001f${item.address}`;
      const bool = point?.data_type === "BOOL";
      const range = point ? `min="${point.min_value ?? 0}" max="${point.max_value ?? (point.data_type === "REAL" ? 3.4e38 : 65535)}"` : "";
      controls = `<label>Señal<select data-exercise-objective-index="${index}" data-exercise-objective-field="target">${options(this.points.map((p) => [`${p.node_id}\u001f${p.address}`, `${p.device_name} · ${p.component_name} (${p.address})`]), selected)}</select></label><label>Condición<select data-exercise-objective-index="${index}" data-exercise-objective-field="operator">${options((bool ? operators.slice(0, 2) : operators).map((op) => [op, ({eq:"Igual a",ne:"Distinto de",gt:"Mayor que",gte:"Mayor o igual",lt:"Menor que",lte:"Menor o igual"})[op]]), item.operator)}</select></label>${valueControl(item.value, bool, "Valor esperado", `data-exercise-objective-index="${index}" data-exercise-objective-field="value" ${bool ? "" : `step=any ${range}`}`)}<label>Debe mantenerse (segundos)<input type="number" min="0" max="86400" step="any" value="${item.duration_seconds ?? 0}" data-exercise-objective-index="${index}" data-exercise-objective-field="duration_seconds"></label>`;
    } else if (item.kind === "field_value") {
      const field = this.fields.find((entry) => entry.object_id === item.object_id);
      const props = fieldProperties[field?.type] || [];
      const bool = isBooleanFieldProperty(item.property);
      controls = `<label>Objeto de campo<select data-exercise-objective-index="${index}" data-exercise-objective-field="target">${options(this.fields.filter((object) => (fieldProperties[object.type] || []).length).map((object) => [object.object_id, object.name]), item.object_id)}</select></label><label>Propiedad<select data-exercise-objective-index="${index}" data-exercise-objective-field="property">${options(props.map((prop) => [prop, fieldPropertyLabels[prop] || prop]), item.property)}</select></label><label>Condición<select data-exercise-objective-index="${index}" data-exercise-objective-field="operator">${options((bool ? ["eq", "ne"] : operators).map((op) => [op, op]), item.operator)}</select></label>${valueControl(item.value, bool, "Valor esperado", `data-exercise-objective-index="${index}" data-exercise-objective-field="value"`)}<label>Debe mantenerse (segundos)<input type="number" min="0" max="86400" step="any" value="${item.duration_seconds ?? 0}" data-exercise-objective-index="${index}" data-exercise-objective-field="duration_seconds"></label>`;
    } else if (item.kind === "field_trend") {
      const numericFields = this.fields.filter((object) => (fieldProperties[object.type] || []).some((property) => !isBooleanFieldProperty(property)));
      const field = this.fields.find((entry) => entry.object_id === item.object_id);
      const props = (fieldProperties[field?.type] || []).filter((property) => !isBooleanFieldProperty(property));
      controls = `<label>Objeto de campo<select data-exercise-objective-index="${index}" data-exercise-objective-field="target">${options(numericFields.map((object) => [object.object_id, object.name]), item.object_id)}</select></label><label>Propiedad<select data-exercise-objective-index="${index}" data-exercise-objective-field="property">${options(props.map((prop) => [prop, fieldPropertyLabels[prop] || prop]), item.property)}</select></label><label>Tendencia<select data-exercise-objective-index="${index}" data-exercise-objective-field="direction">${options([["rising", "Sube"], ["falling", "Baja"], ["stable", "Se mantiene"]], item.direction)}</select></label><label>Ventana de muestreo (2–300 s)<input type="number" min="2" max="300" step="1" value="${item.window_seconds}" data-exercise-objective-index="${index}" data-exercise-objective-field="window_seconds"></label><label>Cambio mínimo en la ventana<input type="number" min="0" step="any" value="${item.minimum_delta ?? 0}" data-exercise-objective-index="${index}" data-exercise-objective-field="minimum_delta"></label><p>Se compara el primer y el último valor válido muestreado. Requiere cubrir casi toda la ventana.</p>`;
    } else if (item.kind === "field_sequence") {
      const availableFields = this.fields.filter((field) => (fieldProperties[field.type] || []).length);
      const stepRows = (item.steps || []).map((step, stepIndex) => {
        const field = this.fields.find((entry) => entry.object_id === step.object_id);
        const properties = fieldProperties[field?.type] || [];
        const boolean = isBooleanFieldProperty(step.property);
        const stepOperators = boolean ? ["eq", "ne"] : operators;
        return `<article class="exercise-sequence-step"><strong>Paso ${stepIndex + 1}</strong><label>Objeto<select data-exercise-objective-index="${index}" data-exercise-sequence-step-index="${stepIndex}" data-exercise-sequence-step-field="target">${options(availableFields.map((entry) => [entry.object_id, entry.name]), step.object_id)}</select></label><label>Propiedad<select data-exercise-objective-index="${index}" data-exercise-sequence-step-index="${stepIndex}" data-exercise-sequence-step-field="property">${options(properties.map((property) => [property, fieldPropertyLabels[property] || property]), step.property)}</select></label><label>Condición<select data-exercise-objective-index="${index}" data-exercise-sequence-step-index="${stepIndex}" data-exercise-sequence-step-field="operator">${options(stepOperators.map((operator) => [operator, operator]), step.operator)}</select></label>${valueControl(step.value, boolean, "Valor esperado", `data-exercise-objective-index="${index}" data-exercise-sequence-step-index="${stepIndex}" data-exercise-sequence-step-field="value"`)}${item.steps.length > 2 ? `<button class="tiny-button" type="button" data-exercise-remove-step="${index}:${stepIndex}">Quitar paso</button>` : ""}</article>`;
      }).join("");
      controls = `<div class="exercise-sequence-steps">${stepRows}</div><button class="tiny-button" type="button" data-exercise-add-step="${index}" ${item.steps.length >= 16 ? "disabled" : ""}>Añadir paso</button><label>Tiempo máximo entre pasos (segundos, opcional)<input type="number" min="0.001" max="86400" step="any" value="${item.max_gap_seconds ?? ""}" data-exercise-objective-index="${index}" data-exercise-sequence-step-field="max_gap_seconds"></label><p>Se cumple en orden: cada condición debe observarse en una muestra posterior. Si vence el intervalo, la secuencia empieza de nuevo.</p>`;
    } else if (item.kind === "protocol_write_policy") {
      const targets = this.protocolPolicyTargets();
      const selected = `${item.target_node_id}\u001f${item.protocol}`;
      const peers = this.devices.filter((device) => device.ip)
        .map((device) => `${device.node_id} (${device.ip})`).join(", ") || "Sin nodos con IP configurada";
      controls = `<label>Dispositivo vigilado<select data-exercise-objective-index="${index}" data-exercise-objective-field="target">${options(targets.map((target) => [`${target.node_id}\u001f${target.protocol}`, `${target.name} · ${target.protocol}`]), selected)}</select></label><label>IP de pares permitidas · una por línea<textarea rows="3" data-exercise-objective-index="${index}" data-exercise-objective-field="allowed_source_ips_text">${escapeHtml(item.allowed_source_ips_text ?? (item.allowed_source_ips || []).join("\n"))}</textarea></label><p>Vacío = toda escritura observada se considera fuera de política. IP configuradas en el escenario: ${escapeHtml(peers)}. En una ruta entre zonas, el PLC puede observar la IP del gateway.</p><label>Resultado esperado<select data-exercise-objective-index="${index}" data-exercise-objective-field="expectation">${options([["detected", "Detectar una escritura de origen no permitido"], ["none", "No detectar incumplimientos"]], item.expectation || "detected")}</select></label><p>La comparación usa la IP de origen que ve el servidor. No autentica ni identifica a la persona o proceso detrás de esa IP.</p>`;
    } else if (item.kind === "audit_event") controls = `<label>Tipo de evento de auditoría<input type="text" maxlength="80" value="${escapeHtml(item.event_type)}" data-exercise-objective-index="${index}" data-exercise-objective-field="event_type" required></label><label>Filtrar detalles (objeto JSON, opcional)<textarea rows="3" data-exercise-objective-index="${index}" data-exercise-objective-field="details_match_text">${escapeHtml(item.details_match_text ?? JSON.stringify(item.details_match || {}, null, 2))}</textarea></label><p>Ejemplo: {"node_id":"plc-1","address":"holding_register:2","source":"exercise"}. Todos los campos deben coincidir exactamente.</p><p>Para detectar escrituras nativas: S7 admite area, db, byte_offset y result; Modbus admite unit_id, function_code, operation, space, address y result. Usa protocol.s7_operation o protocol.modbus_operation como evento.</p><label>Condición<select data-exercise-objective-index="${index}" data-exercise-objective-field="expectation">${options([["occurred", "Debe ocurrir"], ["absent", "No debe ocurrir"]], item.expectation || "occurred")}</select></label><label class="check-field"><input type="checkbox" data-exercise-objective-index="${index}" data-exercise-objective-field="fail_fast" ${item.fail_fast ? "checked" : ""} ${item.expectation === "absent" ? "" : "disabled"}>Finalizar inmediatamente si ocurre</label><p>La ausencia se comprueba al terminar la cronología; con parada inmediata, se detiene al detectarlo.</p>`;
    else controls = `<label>Secuencia de eventos · uno por línea<textarea rows="4" data-exercise-objective-index="${index}" data-exercise-objective-field="event_types" required>${escapeHtml((item.event_types || []).join("\n"))}</textarea></label><label>Separación máxima entre pasos (segundos, opcional)<input type="number" min="0.001" max="86400" step="any" value="${item.max_gap_seconds ?? ""}" data-exercise-objective-index="${index}" data-exercise-objective-field="max_gap_seconds"></label><p>Se evalúan en orden desde el inicio del ejercicio; se permiten otros eventos entre los pasos.</p>`;
    const kinds = [
      ...(this.points.length ? [["point_value", "Valor de una señal S7/Modbus"]] : []),
      ...(this.fields.some((object) => (fieldProperties[object.type] || []).length) ? [["field_value", "Estado del proceso físico"]] : []),
      ...(this.fields.some((object) => (fieldProperties[object.type] || []).some((property) => !isBooleanFieldProperty(property))) ? [["field_trend", "Tendencia de una variable física"]] : []),
      ...(this.fields.some((object) => (fieldProperties[object.type] || []).length) ? [["field_sequence", "Secuencia de condiciones del proceso"]] : []),
      ...(this.protocolPolicyTargets().length ? [["protocol_write_policy", "Política de escrituras S7/Modbus"]] : []),
      ["audit_event", "Evento de auditoría"],
      ["audit_sequence", "Secuencia ordenada de eventos"],
    ];
    return `<article class="exercise-card"><header><strong>Objetivo ${index + 1}</strong><button class="tiny-button" type="button" data-exercise-remove-objective="${index}">Eliminar</button></header><div class="exercise-fields"><label>Tipo<select data-exercise-objective-index="${index}" data-exercise-objective-field="kind">${options(kinds, item.kind)}</select></label>${common}${controls}</div></article>`;
  }

  render() {
    if (this.eventsRoot) this.eventsRoot.innerHTML = this.events.map((item, index) => this.eventRow(item, index)).join("") || '<p class="runtime-empty">Sin eventos programados. El escenario puede evaluarse sin una cronología.</p>';
    if (this.objectivesRoot) this.objectivesRoot.innerHTML = this.objectives.map((item, index) => this.objectiveRow(item, index)).join("") || '<p class="runtime-empty">Añade al menos un objetivo para evaluar el resultado.</p>';
  }

  toDefinition() {
    const events = this.events.map((item) => ({ ...item, at_seconds: Number(item.at_seconds) }));
    events.sort((a, b) => a.at_seconds - b.at_seconds);
    const objectives = this.objectives.map((item, index) => {
      const result = { ...item, weight: Number(item.weight),
        ...(item.duration_seconds !== undefined ? { duration_seconds: Number(item.duration_seconds) } : {}),
        ...(item.kind === "audit_sequence" ? { event_types: (item.event_types || []).map((value) => value.trim()).filter(Boolean) } : {}),
        ...(item.kind === "protocol_write_policy" ? {
          allowed_source_ips: (item.allowed_source_ips_text ?? (item.allowed_source_ips || []).join("\n"))
            .split(/[\r\n,]+/).map((value) => value.trim()).filter(Boolean),
        } : {}),
        ...(["audit_sequence", "field_sequence"].includes(item.kind) ? {
          max_gap_seconds: item.max_gap_seconds === null || item.max_gap_seconds === "" ? null : Number(item.max_gap_seconds),
        } : {}) };
      if (item.kind === "audit_event") {
        try {
          result.details_match = JSON.parse(item.details_match_text ?? JSON.stringify(item.details_match || {}));
        } catch {
          throw new Error(`El filtro JSON del objetivo ${index + 1} no es válido.`);
        }
        delete result.details_match_text;
      }
      if (item.kind === "protocol_write_policy") delete result.allowed_source_ips_text;
      return result;
    });
    return { name: this.name.value.trim(), description: this.description.value.trim(), events, objectives,
      success_rule: this.successRule.value, passing_score: Number(this.passingScore.value) };
  }
}
