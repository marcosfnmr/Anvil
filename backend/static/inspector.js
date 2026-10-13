import { getComponentType, listComponentTypesForDevice } from "./components/registry.js?v=field-live-3";
import { s7Signals } from "./components/remote_input.js?v=field-live-3";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clone(value) {
  return globalThis.structuredClone
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

function getPath(target, path) {
  return path.split(".").reduce((value, key) => value?.[key], target);
}

function setPath(target, path, value) {
  const keys = path.split(".");
  const leaf = keys.pop();
  const parent = keys.reduce((current, key) => {
    current[key] ??= {};
    return current[key];
  }, target);
  parent[leaf] = value;
}

function parseInput(input) {
  if (input.type === "checkbox") return input.checked;
  if (input.type === "number") return input.value === "" ? null : Number(input.value);
  return input.value;
}

function renderField(field, value, attributes) {
  const classes = `field${field.full ? " field-full" : ""}`;
  const common = `${attributes} data-value-type="${field.type}"`;
  if (field.type === "checkbox") {
    return `
      <label class="${classes} checkbox-field"><span>${escapeHtml(field.label)}</span>
        <input type="checkbox" ${common} ${value ? "checked" : ""} />${field.help ? `<small>${escapeHtml(field.help)}</small>` : ""}
      </label>`;
  }
  if (field.type === "select") {
    return `<label class="${classes}"><span>${escapeHtml(field.label)}</span>
      <select ${common}>${field.options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === value ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select></label>`;
  }
  const constraints = [
    field.min !== undefined ? `min="${field.min}"` : "",
    field.max !== undefined ? `max="${field.max}"` : "",
    field.step !== undefined ? `step="${field.step}"` : "",
  ].join(" ");
  return `
    <label class="${classes}"><span>${escapeHtml(field.label)}</span>
      <input type="${field.type}" value="${escapeHtml(value ?? "")}" ${constraints} ${common} />
    </label>`;
}

export class Inspector {
  constructor(element, { onChange, onRemove, onReadHmi, onWriteHmi, onAcknowledgeHmi, onReadScada, onWriteScada, onAcknowledgeScada, onPublishComponent, onImportDeviceProfile, onExportDeviceProfile, getDevices, getZones, getNetworkRoutes, getDeviceServices, isDirty, onNetworkRoutesChange } = {}) {
    this.element = element;
    this.onChange = onChange || (() => {});
    this.onRemove = onRemove || (() => {});
    this.onReadHmi = onReadHmi || (() => {});
    this.onWriteHmi = onWriteHmi || (() => {});
    this.onAcknowledgeHmi = onAcknowledgeHmi || (() => {});
    this.onReadScada = onReadScada || (() => {});
    this.onWriteScada = onWriteScada || (() => {});
    this.onAcknowledgeScada = onAcknowledgeScada || (() => {});
    this.onPublishComponent = onPublishComponent || (() => {});
    this.onImportDeviceProfile = onImportDeviceProfile || (() => {});
    this.onExportDeviceProfile = onExportDeviceProfile || (() => {});
    this.getDevices = getDevices || (() => []);
    this.getZones = getZones || (() => []);
    this.getNetworkRoutes = getNetworkRoutes || (() => []);
    this.getDeviceServices = getDeviceServices || (() => null);
    this.isDirty = isDirty || (() => false);
    this.onNetworkRoutesChange = onNetworkRoutesChange || (() => {});
    this.locked = false;
    this.runtimeStatus = "stopped";
    this.runtimeNodes = new Map();
    this.data = null;
    this.componentIndex = null;
    this.bindEvents();
    this.renderEmpty();
  }

  fieldsChanged() {
    const points = this.data.device_type === "modbus-tcp";
    const isRtu = points && this.data.config.transport === "rtu";
    const faultEnabled = Boolean(this.data.config.fault_mode && this.data.config.fault_mode !== "none");
    const virtual = ["hmi", "scada", "gateway", "firewall", "attacker-station", "engineering-station", "ethernet-switch"].includes(this.data.device_type);
    const zones = this.getZones();
    const zoneField = zones.length ? [{ path: "zone_id", label: "Zona Purdue", value: this.data.zone_id || zones[0].zone_id, type: "select", options: zones.map(zone => ({ value: zone.zone_id, label: `${zone.name} · ${zone.purdue_level}` }),), full: true }] : [];
    const hostnameField = { path: "hostname", label: "Hostname DNS en su zona", value: this.data.hostname || this.data.node_id.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63) || "node", type: "text", full: true };
    if (virtual) {
      return {
        entries: [], listKey: "components", title: this.data.device_type === "scada" ? "Adquisición supervisora" : this.data.device_type === "ethernet-switch" ? "Switch Ethernet" : "Configuración simulada",
        deviceFields: [
          hostnameField,
          ...zoneField,
          ...(this.data.device_type === "scada" ? [
            { path: "config.poll_interval_ms", label: "Periodo de sondeo (ms)", value: this.data.config.poll_interval_ms ?? 1000, min: 250, max: 60000, step: 50 },
            { path: "config.history_capacity", label: "Muestras históricas", value: this.data.config.history_capacity ?? 600, min: 1, max: 10000 },
            { path: "config.durable_history_capacity", label: "Buffer durable pendiente", value: this.data.config.durable_history_capacity ?? 100000, min: 1000, max: 100000, step: 1000 },
            { path: "config.durable_history_max_bytes", label: "Presupuesto de muestras pendientes (bytes)", value: this.data.config.durable_history_max_bytes ?? 134217728, min: 16777216, max: 536870912, step: 16777216 },
          ] : []),
          ...(this.data.device_type === "firewall" ? [{ path: "config.firewall_allow_tcp_ports", label: "Puertos TCP permitidos (separados por coma)", value: (this.data.config.firewall_allow_tcp_ports || []).join(","), type: "text", full: true }] : []),
          ...(this.data.device_type === "attacker-station" ? [{ path: "config.participant_access_enabled", label: "Conectar a la red interna de su zona", value: this.data.config.participant_access_enabled === true, type: "checkbox", full: true }] : []),
          ...(this.data.device_type === "engineering-station" ? [{ path: "config.engineering_access_enabled", label: "Conectar a la red interna de su zona", value: this.data.config.engineering_access_enabled === true, type: "checkbox", full: true }] : []),
          ...(this.data.device_type === "ethernet-switch" ? [{ path: "config.switch_port_capacity", label: "Capacidad de puertos", value: this.data.config.switch_port_capacity ?? 24, min: 2, max: 256, step: 1 }] : []),
        ],
      };
    }
    return {
      entries: points ? this.data.config.points : this.data.config.components,
      listKey: points ? "points" : "components",
      title: points ? "Puntos Modbus" : "Canales y señales",
      deviceFields: points
        ? [
            hostnameField,
            ...zoneField,
            { path: "config.transport", label: "Transporte Modbus", value: this.data.config.transport ?? "tcp", type: "select", options: [
              { value: "tcp", label: "Modbus TCP (MBAP)" },
              { value: "rtu", label: "Modbus RTU · serie virtual por túnel raw TCP" },
            ], full: true },
            { path: "config.unit_id", label: isRtu ? "Unit ID RTU (1–247)" : "Unit ID", value: this.data.config.unit_id, min: isRtu ? 1 : 0, max: isRtu ? 247 : 255 },
            { path: "config.port", label: isRtu ? "Puerto del túnel serie raw TCP" : "Puerto del contenedor", value: this.data.config.port, min: 1, max: 65535 },
            ...(isRtu ? [
              { path: "config.baudrate", label: "Baudios nominales", value: this.data.config.baudrate ?? 19200, type: "select", options: [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200].map(value => ({ value: String(value), label: String(value) })) },
            ] : [
              { path: "config.response_delay_ms", label: "Retardo de respuesta (ms)", value: this.data.config.response_delay_ms ?? 0, min: 0, max: 10000, step: 10 },
              { path: "config.response_jitter_ms", label: "Variación adicional máx. (ms)", value: this.data.config.response_jitter_ms ?? 0, min: 0, max: 10000, step: 10 },
              { path: "config.response_jitter_distribution", label: "Distribución del jitter", value: this.data.config.response_jitter_distribution ?? "deterministic", type: "select", options: [
                { value: "deterministic", label: "Secuencia determinista" },
                { value: "uniform", label: "Uniforme · 0 a máximo" },
                { value: "normal", label: "Normal · centrada en la mitad" },
              ] },
              { path: "config.response_jitter_seed", label: "Semilla reproducible", value: this.data.config.response_jitter_seed ?? 0, min: 0, max: 2147483647, step: 1 },
              { path: "config.fault_mode", label: "Fallo inducido", value: this.data.config.fault_mode ?? "none", type: "select", options: [
                { value: "none", label: "Sin fallo" },
                { value: "device_failure", label: "Excepción Modbus: device failure" },
                { value: "disconnect", label: "Cerrar conexión sin responder" },
              ] },
              ...(faultEnabled ? [
                { path: "config.fault_probability", label: "Probabilidad por solicitud (0–1)", value: this.data.config.fault_probability ?? 1, min: 0, max: 1, step: 0.01 },
                { path: "config.fault_seed", label: "Semilla reproducible del fallo", value: this.data.config.fault_seed ?? 0, min: 0, max: 2147483647, step: 1 },
              ] : []),
            ]),
            ...(!isRtu ? [
              { path: "external_access", label: "Habilitar acceso desde el host/red", value: this.data.external_access, type: "checkbox", full: true },
              ...(this.data.external_access ? [
                { path: "external_host_ip", label: "IP de escucha del host (127.0.0.1 para local)", value: this.data.external_host_ip || "127.0.0.1", type: "text", full: true },
                { path: "external_port", label: "Puerto local del host", value: this.data.external_port, min: 1, max: 65535 },
              ] : []),
            ] : []),
          ]
        : [
            hostnameField,
            ...zoneField,
            { path: "config.rack", label: "Rack", value: this.data.config.rack, min: 0 },
            { path: "config.slot", label: "Slot", value: this.data.config.slot, min: 0 },
            { path: "config.db_size", label: "Tamaño DB", value: this.data.config.db_size, min: 1, full: true },
            { path: "config.scan_cycle_ms", label: "Ciclo PLC (ms)", value: this.data.config.scan_cycle_ms ?? 100, min: 10, max: 5000, step: 1, full: true },
            { path: "config.retain_logic_state", label: "Conservar estado de bloques al recrear el PLC", value: this.data.config.retain_logic_state ?? false, type: "checkbox", full: true, help: "Guarda SR, TON, CTU, secuenciador e histéresis en un volumen gestionado por Anvil. Se elimina al borrar el escenario." },
            { path: "config.response_delay_ms", label: "Retardo de respuesta S7 (ms)", value: this.data.config.response_delay_ms ?? 0, min: 0, max: 10000, step: 10, full: true },
            { path: "config.response_jitter_ms", label: "Variación adicional S7 máx. (ms)", value: this.data.config.response_jitter_ms ?? 0, min: 0, max: 10000, step: 10, full: true },
            { path: "config.response_jitter_distribution", label: "Distribución del jitter S7", value: this.data.config.response_jitter_distribution ?? "deterministic", type: "select", options: [
              { value: "deterministic", label: "Secuencia determinista" },
              { value: "uniform", label: "Uniforme · 0 a máximo" },
              { value: "normal", label: "Normal · centrada en la mitad" },
            ], full: true },
            { path: "config.response_jitter_seed", label: "Semilla reproducible S7", value: this.data.config.response_jitter_seed ?? 0, min: 0, max: 2147483647, step: 1, full: true },
            { path: "config.fault_mode", label: "Fallo inducido S7", value: this.data.config.fault_mode ?? "none", type: "select", options: [
              { value: "none", label: "Normal" },
              { value: "timeout", label: "Omitir respuesta de lectura/escritura (timeout)" },
            ], full: true },
            ...(faultEnabled ? [
              { path: "config.fault_probability", label: "Probabilidad de timeout por solicitud (0–1)", value: this.data.config.fault_probability ?? 1, min: 0, max: 1, step: 0.01, full: true },
              { path: "config.fault_seed", label: "Semilla reproducible del fallo S7", value: this.data.config.fault_seed ?? 0, min: 0, max: 2147483647, step: 1, full: true },
            ] : []),
            { path: "external_access", label: "Habilitar acceso desde el host/red", value: this.data.external_access, type: "checkbox", full: true },
            ...(this.data.external_access ? [
              { path: "external_host_ip", label: "IP de escucha del host (127.0.0.1 para local)", value: this.data.external_host_ip || "127.0.0.1", type: "text", full: true },
              { path: "external_port", label: "Puerto local del host", value: this.data.external_port, min: 1, max: 65535 },
            ] : []),
          ],
    };
  }

  bindEvents() {
    const handleField = (event) => {
      const input = event.target.closest("[data-device-field], [data-component-field]");
      if (!input || !this.data || this.locked) return;
      if (input.dataset.deviceField) {
        let value = parseInput(input);
        const previousValue = getPath(this.data, input.dataset.deviceField);
        if (["config.baudrate", "config.bytesize", "config.stopbits"].includes(input.dataset.deviceField)) value = Number(value);
        if (input.dataset.deviceField === "config.target_node_ids") value = value.split(",").map((item) => item.trim()).filter(Boolean).join(",");
        if (input.dataset.deviceField === "config.firewall_allow_tcp_ports") value = value.split(",").map((item) => item.trim()).filter(Boolean).map(Number);
        setPath(this.data, input.dataset.deviceField, value);
        if (input.dataset.deviceField === "config.transport") {
          if (value === "rtu") {
            this.data.config.unit_id = Math.min(247, Math.max(1, Number(this.data.config.unit_id) || 1));
            this.data.config.response_delay_ms = 0;
            this.data.config.response_jitter_ms = 0;
            this.data.config.response_jitter_distribution = "deterministic";
            this.data.config.response_jitter_seed = 0;
            this.data.config.fault_mode = "none";
            this.data.config.fault_probability = 1;
            this.data.config.fault_seed = 0;
          }
          this.render(this.data);
        }
        if (input.dataset.deviceField === "config.fault_mode") this.render(this.data);
        if (input.dataset.deviceField === "external_access" && input.checked && !this.data.external_port) {
          this.data.external_port = this.data.device_type === "plc-s7" ? 102 : this.data.config.port;
          this.render(this.data);
        }
        if (input.dataset.deviceField === "external_access" && !input.checked) {
          this.data.external_port = null;
          this.data.external_host_ip = "127.0.0.1";
          this.render(this.data);
        }
        if (input.dataset.deviceField === "config.port" && this.data.external_access && this.data.external_port === previousValue) {
          this.data.external_port = this.data.config.port;
          const hostPort = this.element.querySelector('[data-device-field="external_port"]');
          if (hostPort) hostPort.value = this.data.external_port;
        }
      } else {
        const index = Number(input.dataset.componentIndex);
        const { entries } = this.fieldsChanged();
        const oldSourceNodeId = entries[index]?.type === "remote_input" ? entries[index].source_node_id : null;
        const emptyAddress = input.dataset.componentField === "address" && input.value === "";
        const emptyInputRange = entries[index]?.type === "io_channel" && entries[index].direction === "input"
          && entries[index].data_type === "REAL" && ["min_value", "max_value"].includes(input.dataset.componentField)
          && input.value === "";
        const fieldValue = emptyInputRange ? undefined : emptyAddress ? null : parseInput(input);
        if (emptyInputRange) delete entries[index][input.dataset.componentField];
        else setPath(entries[index], input.dataset.componentField, fieldValue);
        if (entries[index]?.type === "io_channel" &&
            (input.dataset.componentField === "byte_offset" && fieldValue === null ||
             input.dataset.componentField === "data_type" && fieldValue === "REAL")) {
          delete entries[index].bit_offset;
        }
        if (entries[index].type === "io_channel" && event.type === "change" && ["direction", "data_type", "area"].includes(input.dataset.componentField)) this.render(this.data, this.componentIndex);
        if (entries[index].type === "remote_input" && event.type === "change") {
          const component = entries[index];
          if (["source_node_id", "data_type"].includes(input.dataset.componentField)) {
            component.source_address = "";
            component.source_name = null;
          }
          if (input.dataset.componentField === "source_node_id" && oldSourceNodeId &&
              !entries.some((item) => item.type === "remote_input" && item.source_node_id === oldSourceNodeId)) {
            this.onNetworkRoutesChange(this.getNetworkRoutes().filter((route) =>
              route.protocol === "tcp" || route.source_node_id !== this.data.node_id || route.target_node_id !== oldSourceNodeId));
          }
          if (input.dataset.componentField === "source_address") {
            const source = this.getDevices().find((item) => item.node_id === component.source_node_id);
            const point = s7Signals(source).find((point) => point.address === component.source_address);
            component.source_name = point?.name ?? null;
            component.unit = point?.unit || "";
          }
          if (["source_node_id", "data_type", "source_address"].includes(input.dataset.componentField)) this.render(this.data, this.componentIndex);
        }
      }
      this.onChange(clone(this.data));
    };
    this.element.addEventListener("input", handleField);
    this.element.addEventListener("change", handleField);
    this.element.addEventListener("change", async (event) => {
      if (!event.target.matches("[data-device-profile-file]") || !event.target.files?.[0] || !this.data) return;
      try {
        const profile = JSON.parse(await event.target.files[0].text());
        this.onImportDeviceProfile(profile, this.data.node_id);
      } catch (error) {
        this.element.dispatchEvent(new CustomEvent("anvil-profile-error", { bubbles: true, detail: error.message }));
      } finally {
        event.target.value = "";
      }
    });
    this.element.addEventListener("click", (event) => {
      if (!this.data) return;
      if (event.target.closest("[data-export-device-profile]")) {
        this.onExportDeviceProfile(this.data.node_id);
        return;
      }
      if (event.target.closest("[data-import-device-profile]")) {
        this.element.querySelector("[data-device-profile-file]")?.click();
        return;
      }
      const lifecycle = event.target.closest("[data-node-action]");
      if (lifecycle) {
        this.element.dispatchEvent(new CustomEvent("anvil-node-lifecycle", {
          bubbles: true,
          detail: { nodeId: this.data.node_id, action: lifecycle.dataset.nodeAction },
        }));
        return;
      }
      if (event.target.closest('[data-open-owner]')) {
        this.render(this.data);
        document.getElementById('heading-config').textContent = 'Configura el dispositivo';
        return;
      }
      const { entries } = this.fieldsChanged();
      const publishComponent = event.target.closest("[data-share-component]");
      if (publishComponent && !this.locked) {
        const component = entries[Number(publishComponent.dataset.shareComponent)];
        if (component) this.onPublishComponent(clone(component), this.data.device_type);
        return;
      }
      const addButton = event.target.closest("[data-add-component]");
      if (addButton && this.data && !this.locked) {
        const definition = getComponentType(this.element.querySelector("[data-component-type]")?.value || addButton.dataset.addComponent);
        const count = entries.filter((item) => item.type === definition.type).length;
        entries.push(definition.create(count + 1));
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }
      const removeButton = event.target.closest("[data-remove-component]");
      if (removeButton && this.data && !this.locked) {
        const [removed] = entries.splice(Number(removeButton.dataset.removeComponent), 1);
        if (removed?.type === "remote_input" && removed.source_node_id &&
            !entries.some((item) => item.type === "remote_input" && item.source_node_id === removed.source_node_id)) {
          this.onNetworkRoutesChange(this.getNetworkRoutes().filter((route) =>
            route.protocol === "tcp" || route.source_node_id !== this.data.node_id || route.target_node_id !== removed.source_node_id));
        }
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }
      const hmiButton = event.target.closest("[data-hmi-read]");
      if (hmiButton && this.data?.device_type === "hmi") {
        this.onReadHmi(this.data.node_id, this.element.querySelector("[data-hmi-values]"));
        return;
      }
      const hmiAck = event.target.closest("[data-hmi-ack]");
      if (hmiAck && this.data?.device_type === "hmi") {
        this.onAcknowledgeHmi(this.data.node_id, {
          scada_node_id: hmiAck.dataset.scada,
          target_node_id: hmiAck.dataset.target,
          address: hmiAck.dataset.address,
        }, this.element.querySelector("[data-hmi-values]"));
        return;
      }
      const hmiWrite = event.target.closest("[data-hmi-write]");
      if (hmiWrite && this.data?.device_type === "hmi") {
        const row = hmiWrite.closest("[data-hmi-target-node]");
        const input = row?.querySelector("[data-hmi-value]");
        if (!row || !input) return;
        const value = input.type === "checkbox" ? input.checked : input.value.trim() === "" ? NaN : Number(input.value);
        const validReal = input.dataset.dataType === "REAL" && typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 3.402823466e38;
        const validRegister = input.dataset.dataType === "UINT16" && Number.isInteger(value) && value >= 0 && value <= 65535;
        if (input.type !== "checkbox" && !validReal && !validRegister) {
          input.setCustomValidity(input.dataset.dataType === "REAL" ? "Introduce un número finito dentro del rango S7 REAL." : "Introduce un entero entre 0 y 65535.");
          input.reportValidity();
          return;
        }
        input.setCustomValidity("");
        this.onWriteHmi(this.data.node_id, {
          target_node_id: row.dataset.hmiTargetNode,
          address: row.dataset.hmiAddress,
          value,
        }, this.element.querySelector("[data-hmi-values]"));
        return;
      }
      const scadaRead = event.target.closest("[data-scada-read]");
      if (scadaRead && this.data?.device_type === "scada") {
        this.onReadScada(this.data.node_id, this.element.querySelector("[data-scada-values]"));
        return;
      }
      const scadaWrite = event.target.closest("[data-scada-write]");
      if (scadaWrite && this.data?.device_type === "scada") {
        const row = scadaWrite.closest("[data-scada-target-node]");
        const input = row?.querySelector("[data-scada-value]");
        if (!row || !input) return;
        const value = input.type === "checkbox" ? input.checked : input.value.trim() === "" ? NaN : Number(input.value);
        const validReal = input.dataset.dataType === "REAL" && typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 3.402823466e38;
        const validRegister = input.dataset.dataType === "UINT16" && Number.isInteger(value) && value >= 0 && value <= 65535;
        if (input.type !== "checkbox" && !validReal && !validRegister) {
          input.setCustomValidity(input.dataset.dataType === "REAL" ? "Introduce un número finito dentro del rango S7 REAL." : "Introduce un entero entre 0 y 65535.");
          input.reportValidity();
          return;
        }
        input.setCustomValidity("");
        this.onWriteScada(this.data.node_id, {
          target_node_id: row.dataset.scadaTargetNode,
          address: row.dataset.scadaAddress,
          value,
        }, this.element.querySelector("[data-scada-values]"));
        return;
      }
      const scadaAck = event.target.closest("[data-scada-ack]");
      if (scadaAck && this.data?.device_type === "scada") {
        this.onAcknowledgeScada(this.data.node_id, { target_node_id: scadaAck.dataset.target, address: scadaAck.dataset.address }, this.element.querySelector("[data-scada-values]"));
        return;
      }
      if (event.target.closest("[data-hmi-add-alarm]") && this.data?.device_type === "hmi" && !this.locked) {
        const target = (this.data.config.target_node_ids || "").split(",").map(value => value.trim()).find(Boolean) || "";
        this.data.config.alarms.push({ target_node_id: target, address: "", mode: "threshold", above: null, below: null, expected_state: null, delay_ms: 0 });
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }
      const hmiAlarmRemove = event.target.closest("[data-hmi-remove-alarm]");
      if (hmiAlarmRemove && this.data?.device_type === "hmi" && !this.locked) {
        this.data.config.alarms.splice(Number(hmiAlarmRemove.dataset.hmiRemoveAlarm), 1);
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }
      if (event.target.closest("[data-scada-add-alarm]") && this.data?.device_type === "scada" && !this.locked) {
        const target = (this.data.config.target_node_ids || "").split(",").map(value => value.trim()).find(Boolean) || "";
        this.data.config.alarms.push({ target_node_id: target, address: "", above: null, below: null, delay_ms: 0 });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-logic-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcLogicCandidates("input");
        const outputs = this.plcLogicOutputCandidates();
        if (!inputs.length || !outputs.length) return;
        this.data.config.control_rules ??= [];
        this.data.config.control_rules.push({ name: `Control ${this.data.config.control_rules.length + 1}`, input_component: inputs[0].name, output_component: outputs[0].name, mode: "below", setpoint: 0, hysteresis: 1 });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-timer-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcTimerCandidates("input");
        const outputs = this.plcTimerCandidates("output");
        if (!inputs.length || !outputs.length) return;
        this.data.config.timers ??= [];
        this.data.config.timers.push({ name: `TON ${this.data.config.timers.length + 1}`, mode: "ton", input_component: inputs[0].name, output_component: outputs[0].name, preset_ms: 1000 });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-boolean-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcBooleanCandidates();
        const outputs = this.plcLogicOutputCandidates();
        if (inputs.length < 2 || !outputs.length) return;
        this.data.config.boolean_rules ??= [];
        this.data.config.boolean_rules.push({ name: `BOOL ${this.data.config.boolean_rules.length + 1}`, input_a_component: inputs[0].name, input_b_component: inputs[1].name, output_component: outputs[0].name, operator: "and" });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-latch-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcBooleanCandidates();
        const outputs = this.plcLogicOutputCandidates();
        if (inputs.length < 2 || !outputs.length) return;
        this.data.config.latches ??= [];
        this.data.config.latches.push({ name: `SR ${this.data.config.latches.length + 1}`, set_component: inputs[0].name, reset_component: inputs[1].name, output_component: outputs[0].name });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-counter-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcBooleanCandidates();
        const outputs = this.plcLogicOutputCandidates();
        if (inputs.length < 2 || !outputs.length) return;
        this.data.config.counters ??= [];
        this.data.config.counters.push({ name: `CTU ${this.data.config.counters.length + 1}`, count_component: inputs[0].name, reset_component: inputs[1].name, output_component: outputs[0].name, preset: 1 });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-sequence-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const inputs = this.plcBooleanCandidates();
        const outputs = this.plcLogicOutputCandidates();
        if (inputs.length < 2 || outputs.length < 2) return;
        this.data.config.sequences ??= [];
        this.data.config.sequences.push({ name: `Secuencia ${this.data.config.sequences.length + 1}`, advance_component: inputs[0].name, reset_component: inputs[1].name, steps: [outputs[0].name, outputs[1].name] });
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-plc-sequence-step-add]") && this.data?.device_type === "plc-s7" && !this.locked) {
        const sequence = this.data.config.sequences?.[Number(event.target.closest("[data-plc-sequence-step-add]").dataset.plcSequenceStepAdd)];
        const output = this.plcLogicOutputCandidates()[0];
        if (!sequence || sequence.steps.length >= 16 || !output) return;
        sequence.steps.push(output.name); this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeSequenceStep = event.target.closest("[data-plc-sequence-step-remove]");
      if (removeSequenceStep && this.data?.device_type === "plc-s7" && !this.locked) {
        const sequence = this.data.config.sequences?.[Number(removeSequenceStep.dataset.plcSequenceIndex)];
        if (!sequence || sequence.steps.length <= 2) return;
        sequence.steps.splice(Number(removeSequenceStep.dataset.plcSequenceStepRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeTimer = event.target.closest("[data-plc-timer-remove]");
      if (removeTimer && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.timers.splice(Number(removeTimer.dataset.plcTimerRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeLogic = event.target.closest("[data-plc-logic-remove]");
      if (removeLogic && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.control_rules.splice(Number(removeLogic.dataset.plcLogicRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeBooleanRule = event.target.closest("[data-plc-boolean-remove]");
      if (removeBooleanRule && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.boolean_rules.splice(Number(removeBooleanRule.dataset.plcBooleanRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeLatch = event.target.closest("[data-plc-latch-remove]");
      if (removeLatch && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.latches.splice(Number(removeLatch.dataset.plcLatchRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeCounter = event.target.closest("[data-plc-counter-remove]");
      if (removeCounter && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.counters.splice(Number(removeCounter.dataset.plcCounterRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeSequence = event.target.closest("[data-plc-sequence-remove]");
      if (removeSequence && this.data?.device_type === "plc-s7" && !this.locked) {
        this.data.config.sequences.splice(Number(removeSequence.dataset.plcSequenceRemove), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      const removeAlarm = event.target.closest("[data-scada-remove-alarm]");
      if (removeAlarm && this.data?.device_type === "scada" && !this.locked) {
        this.data.config.alarms.splice(Number(removeAlarm.dataset.scadaRemoveAlarm), 1);
        this.onChange(clone(this.data)); this.render(this.data); return;
      }
      if (event.target.closest("[data-remove-node]") && !this.locked) this.onRemove();
    });
    this.element.addEventListener("change", (event) => {
      if (event.target.matches("[data-s7-route]") && !this.locked) {
        const targetId = event.target.value;
        const routes = this.getNetworkRoutes().filter((item) => !(item.protocol === "s7" && item.source_node_id === this.data.node_id && item.target_node_id === targetId));
        const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 8) || Date.now().toString(36);
        const routeId = `route-${this.data.node_id}-${targetId}`.replace(/[^a-zA-Z0-9_.-]+/g, "-").slice(0, 55) + `-${suffix}`;
        routes.push({ route_id: routeId, source_node_id: this.data.node_id, target_node_id: targetId, protocol: "s7", enabled: event.target.checked });
        this.onNetworkRoutesChange(routes);
        this.render(this.data, this.componentIndex);
        this.onChange(clone(this.data));
        return;
      }
      if (event.target.matches("[data-scada-route]") && !this.locked) {
        const targetId = event.target.value;
        const target = this.getDevices().find((item) => item.node_id === targetId);
        const expectedProtocol = target.device_type === "plc-s7" ? "s7" : "modbus-tcp";
        const routes = this.getNetworkRoutes().filter((item) => !(item.protocol === expectedProtocol && item.source_node_id === this.data.node_id && item.target_node_id === targetId));
        const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 8) || Date.now().toString(36);
        const routeId = `route-${this.data.node_id}-${targetId}`.replace(/[^a-zA-Z0-9_.-]+/g, "-").slice(0, 55) + `-${suffix}`;
        routes.push({ route_id: routeId, source_node_id: this.data.node_id, target_node_id: targetId, protocol: target.device_type === "plc-s7" ? "s7" : "modbus-tcp", enabled: event.target.checked });
        this.onNetworkRoutesChange(routes);
        this.render(this.data);
        return;
      }
      if (event.target.matches("[data-hmi-alarm-source]") && !this.locked) {
        this.data.config.alarm_source_node_ids = [...this.element.querySelectorAll("[data-hmi-alarm-source]:checked")].map(item => item.value).join(",");
        this.onChange(clone(this.data));
        return;
      }
      if (!event.target.matches("[data-hmi-target], [data-scada-target]") || this.locked) return;
      const selector = this.data.device_type === "scada" ? "[data-scada-target]:checked" : "[data-hmi-target]:checked";
      this.data.config.target_node_ids = [...this.element.querySelectorAll(selector)].map((item) => item.value).join(",");
      if (this.data.device_type === "scada") {
        const selected = new Set(this.data.config.target_node_ids.split(",").filter(Boolean));
        this.data.config.alarms = (this.data.config.alarms || []).filter(rule => selected.has(rule.target_node_id));
      }
      if (this.data.device_type === "hmi") {
        const selected = new Set(this.data.config.target_node_ids.split(",").filter(Boolean));
        this.data.config.alarms = (this.data.config.alarms || []).filter(rule => selected.has(rule.target_node_id));
      }
      const selected = new Set(this.data.config.target_node_ids.split(",").filter(Boolean));
      this.onNetworkRoutesChange(this.getNetworkRoutes().filter(route => route.protocol === "tcp" || route.source_node_id !== this.data.node_id || selected.has(route.target_node_id)));
      this.render(this.data);
      this.onChange(clone(this.data));
    });
    this.element.addEventListener("input", (event) => this.handleScadaRuleField(event));
    this.element.addEventListener("change", (event) => this.handleScadaRuleField(event));
    this.element.addEventListener("input", (event) => this.handleHmiAlarmRuleField(event));
    this.element.addEventListener("change", (event) => this.handleHmiAlarmRuleField(event));
    this.element.addEventListener("input", (event) => this.handlePlcLogicField(event));
    this.element.addEventListener("change", (event) => this.handlePlcLogicField(event));
    this.element.addEventListener("input", (event) => this.handlePlcTimerField(event));
    this.element.addEventListener("change", (event) => this.handlePlcTimerField(event));
    this.element.addEventListener("input", (event) => this.handlePlcBooleanField(event));
    this.element.addEventListener("change", (event) => this.handlePlcBooleanField(event));
    this.element.addEventListener("input", (event) => this.handlePlcLatchField(event));
    this.element.addEventListener("change", (event) => this.handlePlcLatchField(event));
    this.element.addEventListener("input", (event) => this.handlePlcCounterField(event));
    this.element.addEventListener("change", (event) => this.handlePlcCounterField(event));
    this.element.addEventListener("input", (event) => this.handlePlcSequenceField(event));
    this.element.addEventListener("change", (event) => this.handlePlcSequenceField(event));
  }

  handleScadaRuleField(event) {
    const input = event.target.closest("[data-scada-rule-field]");
    if (!input || !this.data || this.data.device_type !== "scada" || this.locked) return;
    const rule = this.data.config.alarms[Number(input.dataset.scadaRuleIndex)];
    if (!rule) return;
    const field = input.dataset.scadaRuleField;
    rule[field] = ["above", "below", "delay_ms"].includes(field) ? (input.value === "" ? null : Number(input.value)) : input.value;
    if (["target_node_id", "address"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handleHmiAlarmRuleField(event) {
    const input = event.target.closest("[data-hmi-alarm-rule-field]");
    if (!input || this.locked) return;
    if (!this.data || this.data.device_type !== "hmi") return;
    const rule = this.data.config.alarms?.[Number(input.dataset.hmiAlarmRuleIndex)];
    if (!rule) return;
    const field = input.dataset.hmiAlarmRuleField;
    rule[field] = ["above", "below", "delay_ms"].includes(field) ? (input.value === "" ? null : Number(input.value))
      : field === "expected_state" ? input.value === "true" : input.value;
    if (event.type === "change" && field === "mode") {
      rule.above = null;
      rule.below = null;
      rule.expected_state = rule.mode === "state" ? false : null;
    }
    if (event.type === "change" && field === "target_node_id") rule.address = "";
    if (event.type === "change" && field === "address") {
      const tag = this.scadaTags(rule.target_node_id).find(item => item.address === rule.address);
      rule.mode = tag?.data_type === "BOOL" ? "state" : "threshold";
      rule.above = null;
      rule.below = null;
      rule.expected_state = rule.mode === "state" ? false : null;
    }
    if (event.type === "change" && ["target_node_id", "address", "mode"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcLogicField(event) {
    const input = event.target.closest("[data-plc-logic-field]");
    if (!input || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const rule = this.data.config.control_rules?.[Number(input.dataset.plcLogicIndex)];
    if (!rule) return;
    const field = input.dataset.plcLogicField;
    rule[field] = ["setpoint", "hysteresis"].includes(field) ? (input.value === "" ? null : Number(input.value)) : input.value;
    if (["input_component", "output_component"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcTimerField(event) {
    const input = event.target.closest("[data-plc-timer-field]");
    if (!input || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const timer = this.data.config.timers?.[Number(input.dataset.plcTimerIndex)];
    if (!timer) return;
    const field = input.dataset.plcTimerField;
    timer[field] = field === "preset_ms" ? (input.value === "" ? null : Number(input.value)) : input.value;
    if (["input_component", "output_component"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcBooleanField(event) {
    const input = event.target.closest("[data-plc-boolean-field]");
    if (!input || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const rule = this.data.config.boolean_rules?.[Number(input.dataset.plcBooleanIndex)];
    if (!rule) return;
    rule[input.dataset.plcBooleanField] = input.type === "checkbox" ? input.checked : input.value;
    if (input.dataset.plcBooleanField === "input_a_component" && rule.input_a_component === rule.input_b_component) {
      rule.input_b_component = this.plcBooleanCandidates().find(item => item.name !== rule.input_a_component)?.name || "";
    }
    if (["input_a_component", "input_b_component", "output_component"].includes(input.dataset.plcBooleanField)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcLatchField(event) {
    const input = event.target.closest("[data-plc-latch-field]");
    if (!input || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const latch = this.data.config.latches?.[Number(input.dataset.plcLatchIndex)];
    if (!latch) return;
    const field = input.dataset.plcLatchField;
    latch[field] = input.value;
    if (field === "set_component" && latch.set_component === latch.reset_component) {
      latch.reset_component = this.plcBooleanCandidates().find(item => item.name !== latch.set_component)?.name || "";
    }
    if (["set_component", "reset_component", "output_component"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcCounterField(event) {
    const input = event.target.closest("[data-plc-counter-field]");
    if (!input || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const counter = this.data.config.counters?.[Number(input.dataset.plcCounterIndex)];
    if (!counter) return;
    const field = input.dataset.plcCounterField;
    counter[field] = field === "preset" ? (input.value === "" ? null : Number(input.value)) : input.value;
    if (field === "count_component" && counter.count_component === counter.reset_component) {
      counter.reset_component = this.plcBooleanCandidates().find(item => item.name !== counter.count_component)?.name || "";
    }
    if (field === "reset_component" && counter.reset_component === counter.count_component) {
      counter.count_component = this.plcBooleanCandidates().find(item => item.name !== counter.reset_component)?.name || "";
    }
    if (["count_component", "reset_component", "output_component"].includes(field)) this.render(this.data);
    this.onChange(clone(this.data));
  }

  handlePlcSequenceField(event) {
    const fieldInput = event.target.closest("[data-plc-sequence-field]");
    const stepInput = event.target.closest("[data-plc-sequence-step]");
    if ((!fieldInput && !stepInput) || !this.data || this.data.device_type !== "plc-s7" || this.locked) return;
    const sequenceIndex = Number((fieldInput || stepInput).dataset.plcSequenceIndex);
    const sequence = this.data.config.sequences?.[sequenceIndex];
    if (!sequence) return;
    let rerender = false;
    if (stepInput) {
      const stepIndex = Number(stepInput.dataset.plcSequenceStep);
      sequence.steps[stepIndex] = stepInput.value;
      const duplicate = sequence.steps.some((name, index) => index !== stepIndex && name === stepInput.value);
      if (duplicate) sequence.steps[stepIndex] = this.plcLogicOutputCandidates().find(item => !sequence.steps.includes(item.name))?.name || stepInput.value;
      rerender = true;
    } else {
      const field = fieldInput.dataset.plcSequenceField;
      sequence[field] = fieldInput.value;
      if (field === "advance_component" && sequence.advance_component === sequence.reset_component) {
        sequence.reset_component = this.plcBooleanCandidates().find(item => item.name !== sequence.advance_component)?.name || "";
        rerender = true;
      }
      if (field === "reset_component" && sequence.reset_component === sequence.advance_component) {
        sequence.advance_component = this.plcBooleanCandidates().find(item => item.name !== sequence.reset_component)?.name || "";
        rerender = true;
      }
      if (["advance_component", "reset_component"].includes(field)) rerender = true;
    }
    if (rerender) this.render(this.data);
    this.onChange(clone(this.data));
  }

  plcLogicCandidates(direction) {
    const components = this.data?.config?.components || [];
    return components.filter(item => direction === "input"
      ? item.type === "analog_sensor" || item.type === "pump" || item.type === "remote_input" && item.data_type === "REAL" || item.type === "io_channel" && item.area === "DB" && item.direction === "input" && item.data_type === "REAL"
      : ["valve", "motor", "pump"].includes(item.type) || item.type === "io_channel" && item.area === "DB" && item.direction === "output" && item.data_type === "BOOL");
  }

  plcLogicOutputCandidates(currentOutput = "") {
    const used = new Set([
      ...(this.data?.config?.control_rules || []).map(rule => rule.output_component),
      ...(this.data?.config?.boolean_rules || []).map(rule => rule.output_component),
      ...(this.data?.config?.timers || []).map(timer => timer.output_component),
      ...(this.data?.config?.latches || []).map(latch => latch.output_component),
      ...(this.data?.config?.counters || []).map(counter => counter.output_component),
      ...(this.data?.config?.sequences || []).flatMap(sequence => sequence.steps || []),
    ]);
    return this.plcLogicCandidates("output").filter(item => item.name === currentOutput || !used.has(item.name));
  }

  plcTimerCandidates(direction, currentOutput = "") {
    const components = this.data?.config?.components || [];
    if (direction === "input") return components.filter(item =>
      item.type === "digital_input" || item.type === "remote_input" && item.data_type === "BOOL"
      || item.type === "io_channel" && item.area === "DB" && item.direction === "input" && item.data_type === "BOOL");
    return this.plcLogicOutputCandidates(currentOutput);
  }

  plcBooleanCandidates() {
    return this.plcTimerCandidates("input");
  }

  renderPlcLogic() {
    if (this.data?.device_type !== "plc-s7") return "";
    const rules = this.data.config.control_rules || [];
    const inputs = this.plcLogicCandidates("input"), outputs = this.plcLogicOutputCandidates();
    const timers = this.data.config.timers || [];
    const latches = this.data.config.latches || [];
    const counters = this.data.config.counters || [];
    const sequences = this.data.config.sequences || [];
    const booleanRules = this.data.config.boolean_rules || [];
    const booleanInputs = this.plcBooleanCandidates();
    return `<section class="inspector-section"><div class="inspector-section-title"><strong>Programa PLC · bloques ON/OFF</strong><button type="button" class="tiny-button" data-plc-logic-add ${!inputs.length || !outputs.length ? "disabled" : ""}>Añadir bloque</button></div>
      <p class="section-help">Control cíclico por histéresis: vincula una entrada REAL y una salida BOOL.</p>
      ${rules.map((rule, index) => `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(rule.name)}</strong><button class="remove-component" type="button" data-plc-logic-remove="${index}" aria-label="Eliminar bloque ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre del bloque</span><input type="text" value="${escapeHtml(rule.name)}" data-plc-logic-field="name" data-plc-logic-index="${index}" /></label>
        <label class="field"><span>Entrada REAL</span><select data-plc-logic-field="input_component" data-plc-logic-index="${index}">${inputs.map(item => `<option value="${escapeHtml(item.name)}" ${rule.input_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Salida BOOL</span><select data-plc-logic-field="output_component" data-plc-logic-index="${index}">${this.plcLogicOutputCandidates(rule.output_component).map(item => `<option value="${escapeHtml(item.name)}" ${rule.output_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}${item.type === "pump" ? " · orden" : ""}</option>`).join("")}</select></label>
        <label class="field"><span>Acción</span><select data-plc-logic-field="mode" data-plc-logic-index="${index}"><option value="below" ${rule.mode === "below" ? "selected" : ""}>Encender por debajo (llenado)</option><option value="above" ${rule.mode === "above" ? "selected" : ""}>Encender por encima</option></select></label>
        <label class="field"><span>Consigna</span><input type="number" step="any" value="${escapeHtml(rule.setpoint)}" data-plc-logic-field="setpoint" data-plc-logic-index="${index}" /></label>
        <label class="field"><span>Histéresis</span><input type="number" min="0.000001" step="any" value="${escapeHtml(rule.hysteresis)}" data-plc-logic-field="hysteresis" data-plc-logic-index="${index}" /></label>
      </div></article>`).join("") || '<p class="section-help">No hay bloques ON/OFF.</p>'}</section>
      <section class="inspector-section"><div class="inspector-section-title"><strong>Rung BOOL · contactos y AND / OR / XOR</strong><button type="button" class="tiny-button" data-plc-boolean-add ${booleanInputs.length < 2 || !this.plcLogicOutputCandidates().length ? "disabled" : ""}>Añadir rung</button></div>
      <p class="section-help">Combina dos entradas BOOL para accionar una salida. Cada contacto puede ser normalmente abierto o cerrado; una entrada negada conduce cuando su señal está a 0. Solo las señales de entrada pueden usarse como contactos y cada salida admite un único bloque de control.</p>
      ${booleanRules.map((rule, index) => { const inputB = booleanInputs.filter(item => item.name !== rule.input_a_component); return `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(rule.name)}</strong><button class="remove-component" type="button" data-plc-boolean-remove="${index}" aria-label="Eliminar bloque BOOL ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre del bloque</span><input type="text" maxlength="80" value="${escapeHtml(rule.name)}" data-plc-boolean-field="name" data-plc-boolean-index="${index}" /></label>
        <label class="field"><span>Entrada BOOL A</span><select data-plc-boolean-field="input_a_component" data-plc-boolean-index="${index}">${booleanInputs.map(item => `<option value="${escapeHtml(item.name)}" ${rule.input_a_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span><input type="checkbox" data-plc-boolean-field="input_a_negated" data-plc-boolean-index="${index}" ${rule.input_a_negated ? "checked" : ""}> Contacto normalmente cerrado</span></label>
        <label class="field"><span>Operador</span><select data-plc-boolean-field="operator" data-plc-boolean-index="${index}"><option value="and" ${rule.operator === "and" ? "selected" : ""}>AND · ambas activas</option><option value="or" ${rule.operator === "or" ? "selected" : ""}>OR · al menos una activa</option><option value="xor" ${rule.operator === "xor" ? "selected" : ""}>XOR · solo una activa</option></select></label>
        <label class="field"><span>Entrada BOOL B</span><select data-plc-boolean-field="input_b_component" data-plc-boolean-index="${index}">${inputB.map(item => `<option value="${escapeHtml(item.name)}" ${rule.input_b_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span><input type="checkbox" data-plc-boolean-field="input_b_negated" data-plc-boolean-index="${index}" ${rule.input_b_negated ? "checked" : ""}> Contacto normalmente cerrado</span></label>
        <label class="field"><span>Salida BOOL</span><select data-plc-boolean-field="output_component" data-plc-boolean-index="${index}">${this.plcLogicOutputCandidates(rule.output_component).map(item => `<option value="${escapeHtml(item.name)}" ${rule.output_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
      </div></article>`; }).join("") || '<p class="section-help">No hay bloques de lógica BOOL.</p>'}</section>
      <section class="inspector-section"><div class="inspector-section-title"><strong>Temporizadores TON / TOF</strong><button type="button" class="tiny-button" data-plc-timer-add ${!this.plcTimerCandidates("input").length || !this.plcTimerCandidates("output").length ? "disabled" : ""}>Añadir temporizador</button></div>
      <p class="section-help">TON activa la salida tras mantener la entrada; TOF mantiene la salida durante el retardo al desactivarse.</p>
      ${timers.map((timer, index) => { const timerInputs = this.plcTimerCandidates("input"); const timerOutputs = this.plcTimerCandidates("output", timer.output_component); return `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(timer.name)}</strong><button class="remove-component" type="button" data-plc-timer-remove="${index}" aria-label="Eliminar temporizador ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre</span><input type="text" value="${escapeHtml(timer.name)}" data-plc-timer-field="name" data-plc-timer-index="${index}" /></label>
        <label class="field"><span>Entrada BOOL</span><select data-plc-timer-field="input_component" data-plc-timer-index="${index}">${timerInputs.map(item => `<option value="${escapeHtml(item.name)}" ${timer.input_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Salida BOOL</span><select data-plc-timer-field="output_component" data-plc-timer-index="${index}">${timerOutputs.map(item => `<option value="${escapeHtml(item.name)}" ${timer.output_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Modo</span><select data-plc-timer-field="mode" data-plc-timer-index="${index}"><option value="ton" ${timer.mode !== "tof" ? "selected" : ""}>TON · retardo al activar</option><option value="tof" ${timer.mode === "tof" ? "selected" : ""}>TOF · retardo al desactivar</option></select></label>
        <label class="field"><span>Retardo (ms)</span><input type="number" min="10" max="3600000" step="10" value="${escapeHtml(timer.preset_ms)}" data-plc-timer-field="preset_ms" data-plc-timer-index="${index}" /></label>
      </div></article>`; }).join("") || '<p class="section-help">No hay temporizadores configurados.</p>'}</section>
      <section class="inspector-section"><div class="inspector-section-title"><strong>Memorias SR (reset prioritario)</strong><button type="button" class="tiny-button" data-plc-latch-add ${booleanInputs.length < 2 || !this.plcLogicOutputCandidates().length ? "disabled" : ""}>Añadir SR</button></div>
      <p class="section-help">El SET activa y mantiene la salida; RESET la desactiva. Si ambas entradas están activas en el mismo scan, prevalece RESET.</p>
      ${latches.map((latch, index) => { const resetInputs = booleanInputs.filter(item => item.name !== latch.set_component); const outputOptions = this.plcLogicOutputCandidates(latch.output_component); return `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(latch.name)}</strong><button class="remove-component" type="button" data-plc-latch-remove="${index}" aria-label="Eliminar memoria SR ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre</span><input type="text" maxlength="80" value="${escapeHtml(latch.name)}" data-plc-latch-field="name" data-plc-latch-index="${index}" /></label>
        <label class="field"><span>Entrada SET</span><select data-plc-latch-field="set_component" data-plc-latch-index="${index}">${booleanInputs.map(item => `<option value="${escapeHtml(item.name)}" ${latch.set_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Entrada RESET</span><select data-plc-latch-field="reset_component" data-plc-latch-index="${index}">${resetInputs.map(item => `<option value="${escapeHtml(item.name)}" ${latch.reset_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Salida BOOL</span><select data-plc-latch-field="output_component" data-plc-latch-index="${index}">${outputOptions.map(item => `<option value="${escapeHtml(item.name)}" ${latch.output_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
      </div></article>`; }).join("") || '<p class="section-help">No hay memorias SR configuradas.</p>'}</section>
      <section class="inspector-section"><div class="inspector-section-title"><strong>Contadores CTU</strong><button type="button" class="tiny-button" data-plc-counter-add ${booleanInputs.length < 2 || !this.plcLogicOutputCandidates().length ? "disabled" : ""}>Añadir CTU</button></div>
      <p class="section-help">Cuenta flancos ascendentes de la entrada. RESET tiene prioridad; DONE se activa al alcanzar el preset. El contador se reinicia al recrear el PLC.</p>
      ${counters.map((counter, index) => { const countInputs = booleanInputs.filter(item => item.name !== counter.reset_component); const resetInputs = booleanInputs.filter(item => item.name !== counter.count_component); const outputOptions = this.plcLogicOutputCandidates(counter.output_component); return `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(counter.name)}</strong><button class="remove-component" type="button" data-plc-counter-remove="${index}" aria-label="Eliminar contador CTU ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre</span><input type="text" maxlength="80" value="${escapeHtml(counter.name)}" data-plc-counter-field="name" data-plc-counter-index="${index}" /></label>
        <label class="field"><span>Entrada de conteo</span><select data-plc-counter-field="count_component" data-plc-counter-index="${index}">${countInputs.map(item => `<option value="${escapeHtml(item.name)}" ${counter.count_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Entrada RESET</span><select data-plc-counter-field="reset_component" data-plc-counter-index="${index}">${resetInputs.map(item => `<option value="${escapeHtml(item.name)}" ${counter.reset_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Salida DONE</span><select data-plc-counter-field="output_component" data-plc-counter-index="${index}">${outputOptions.map(item => `<option value="${escapeHtml(item.name)}" ${counter.output_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Preset (1–65535)</span><input type="number" min="1" max="65535" step="1" value="${escapeHtml(counter.preset)}" data-plc-counter-field="preset" data-plc-counter-index="${index}" /></label>
      </div></article>`; }).join("") || '<p class="section-help">No hay contadores configurados.</p>'}</section>
      <section class="inspector-section"><div class="inspector-section-title"><strong>Secuenciadores BOOL</strong><button type="button" class="tiny-button" data-plc-sequence-add ${booleanInputs.length < 2 || this.plcLogicOutputCandidates().length < 2 ? "disabled" : ""}>Añadir secuencia</button></div>
      <p class="section-help">Cada flanco ascendente avanza una salida activa (una por paso). Mantener la entrada no repite el avance; el último paso se mantiene. RESET vuelve al paso 1 y tiene prioridad sobre avance.</p>
      ${sequences.map((sequence, index) => { const advanceInputs = booleanInputs.filter(item => item.name !== sequence.reset_component); const resetInputs = booleanInputs.filter(item => item.name !== sequence.advance_component); const remainingOutputs = this.plcLogicOutputCandidates(); return `<article class="component-card"><header class="component-card-header"><strong>${escapeHtml(sequence.name)}</strong><button class="remove-component" type="button" data-plc-sequence-remove="${index}" aria-label="Eliminar secuenciador ${index + 1}">×</button></header><div class="component-card-body field-grid">
        <label class="field"><span>Nombre</span><input type="text" maxlength="80" value="${escapeHtml(sequence.name)}" data-plc-sequence-field="name" data-plc-sequence-index="${index}" /></label>
        <label class="field"><span>Avanzar</span><select data-plc-sequence-field="advance_component" data-plc-sequence-index="${index}">${advanceInputs.map(item => `<option value="${escapeHtml(item.name)}" ${sequence.advance_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <label class="field"><span>RESET</span><select data-plc-sequence-field="reset_component" data-plc-sequence-index="${index}">${resetInputs.map(item => `<option value="${escapeHtml(item.name)}" ${sequence.reset_component === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
        <div class="sequence-step-list"><div class="inspector-section-title"><strong>Pasos (${sequence.steps.length}/16)</strong><button type="button" class="tiny-button" data-plc-sequence-step-add="${index}" ${sequence.steps.length >= 16 || !remainingOutputs.length ? "disabled" : ""}>Añadir paso</button></div>
          ${sequence.steps.map((step, stepIndex) => `<div class="sequence-step"><label class="field"><span>Paso ${stepIndex + 1}</span><select aria-label="Salida del paso ${stepIndex + 1}" data-plc-sequence-step="${stepIndex}" data-plc-sequence-index="${index}">${this.plcLogicOutputCandidates(step).map(item => `<option value="${escapeHtml(item.name)}" ${step === item.name ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label><button type="button" class="tiny-button" data-plc-sequence-step-remove="${stepIndex}" data-plc-sequence-index="${index}" aria-label="Eliminar paso ${stepIndex + 1}" ${sequence.steps.length <= 2 ? "disabled" : ""}>Quitar</button></div>`).join("")}
        </div>
      </div></article>`; }).join("") || '<p class="section-help">No hay secuenciadores configurados.</p>'}</section>`;
  }

  scadaTags(targetId) {
    const target = this.getDevices().find(item => item.node_id === targetId);
    if (target?.device_type === "modbus-tcp") {
      const points = target.config.points || [];
      const spaces = ["coil", "discrete_input", "holding_register", "input_register"];
      return spaces.flatMap(type => {
        const used = new Set(points.filter(point => point.type === type && Number.isInteger(point.address)).map(point => point.address));
        let next = 0;
        return points.filter(point => point.type === type).map(point => {
          let address = point.address;
          if (!Number.isInteger(address)) { while (used.has(next)) next++; address = next; used.add(next++); }
          const dataType = ["coil", "discrete_input"].includes(type) ? "BOOL" : "UINT16";
          return { address: `${type}:${address}`, name: point.name, data_type: dataType, numeric: dataType === "UINT16" };
        });
      });
    }
    return s7Signals(target).map(point => ({ ...point, address: point.address, numeric: point.data_type === "REAL" }));
  }

  renderScadaAlarms() {
    if (this.data?.device_type !== "scada") return "";
    const selected = (this.data.config.target_node_ids || "").split(",").map(value => value.trim()).filter(Boolean);
    const targets = this.getDevices().filter(item => selected.includes(item.node_id));
    const rules = this.data.config.alarms || [];
    return `<section class="inspector-section"><div class="inspector-section-title"><strong>Reglas de alarma</strong><button type="button" class="tiny-button" data-scada-add-alarm>Añadir regla</button></div>
      ${rules.map((rule, index) => {
        const tags = this.scadaTags(rule.target_node_id).filter(tag => tag.numeric);
        const targetOptions = targets.map(item => `<option value="${escapeHtml(item.node_id)}" ${rule.target_node_id === item.node_id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
        const tagOptions = tags.map(tag => `<option value="${escapeHtml(tag.address)}" ${rule.address === tag.address ? "selected" : ""}>${escapeHtml(tag.name)} · ${escapeHtml(tag.address)}</option>`).join("");
        return `<article class="component-card"><header class="component-card-header"><strong>Regla ${index + 1}</strong><button class="remove-component" type="button" data-scada-remove-alarm="${index}" aria-label="Eliminar regla ${index + 1}">×</button></header><div class="component-card-body field-grid">
          <label class="field"><span>Dispositivo</span><select data-scada-rule-field="target_node_id" data-scada-rule-index="${index}">${targetOptions}</select></label>
          <label class="field"><span>Señal numérica</span><select data-scada-rule-field="address" data-scada-rule-index="${index}"><option value="">Selecciona una señal</option>${tagOptions}</select></label>
          <label class="field"><span>Alarma si es mayor que</span><input type="number" step="any" value="${escapeHtml(rule.above ?? "")}" data-scada-rule-field="above" data-scada-rule-index="${index}" /></label>
          <label class="field"><span>Alarma si es menor que</span><input type="number" step="any" value="${escapeHtml(rule.below ?? "")}" data-scada-rule-field="below" data-scada-rule-index="${index}" /></label>
          <label class="field"><span>Retardo para activar (ms)</span><input type="number" min="0" max="86400000" step="100" value="${escapeHtml(rule.delay_ms ?? 0)}" data-scada-rule-field="delay_ms" data-scada-rule-index="${index}" /></label>
        </div></article>`;
      }).join("") || '<p class="section-help">Sin reglas. Las muestras seguirán registrándose.</p>'}</section>`;
  }

  renderHmiAlarms() {
    if (this.data?.device_type !== "hmi") return "";
    const selected = (this.data.config.target_node_ids || "").split(",").map(value => value.trim()).filter(Boolean);
    const targets = this.getDevices().filter(item => selected.includes(item.node_id));
    const rules = this.data.config.alarms || [];
    const tagsFor = (nodeId) => this.scadaTags(nodeId);
    return `<section class="inspector-section"><div class="inspector-section-title"><strong>Alarmas propias de esta HMI</strong><button type="button" class="tiny-button" data-hmi-add-alarm>Añadir regla</button></div>
      <p class="section-help">Se evalúan con la lectura S7/Modbus de esta HMI. Si falla la comunicación, se conserva el último estado como obsoleto.</p>
      ${rules.map((rule, index) => {
        const tags = tagsFor(rule.target_node_id);
        const selectedTag = tags.find(tag => tag.address === rule.address);
        const mode = rule.mode || (selectedTag?.data_type === "BOOL" ? "state" : "threshold");
        const modeOptions = (selectedTag?.data_type === "BOOL" ? ["state"] : selectedTag ? ["threshold"] : ["threshold", "state"])
          .map(value => `<option value="${value}" ${mode === value ? "selected" : ""}>${value === "state" ? "Estado BOOL" : "Umbral numérico"}</option>`).join("");
        const targetOptions = targets.map(item => `<option value="${escapeHtml(item.node_id)}" ${rule.target_node_id === item.node_id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
        const tagOptions = tags.map(tag => `<option value="${escapeHtml(tag.address)}" ${rule.address === tag.address ? "selected" : ""}>${escapeHtml(tag.name)} · ${escapeHtml(tag.address)}</option>`).join("");
        return `<article class="component-card"><header class="component-card-header"><strong>Regla ${index + 1}</strong><button class="remove-component" type="button" data-hmi-remove-alarm="${index}" aria-label="Eliminar regla ${index + 1}">×</button></header><div class="component-card-body field-grid">
          <label class="field"><span>Dispositivo</span><select data-hmi-alarm-rule-field="target_node_id" data-hmi-alarm-rule-index="${index}">${targetOptions}</select></label>
          <label class="field"><span>Señal numérica o digital</span><select data-hmi-alarm-rule-field="address" data-hmi-alarm-rule-index="${index}"><option value="">Selecciona una señal</option>${tagOptions}</select></label>
          <label class="field"><span>Tipo de condición</span><select data-hmi-alarm-rule-field="mode" data-hmi-alarm-rule-index="${index}">${modeOptions}</select></label>
          ${mode === "state" ? `<label class="field"><span>Alarma cuando el estado sea</span><select data-hmi-alarm-rule-field="expected_state" data-hmi-alarm-rule-index="${index}"><option value="true" ${rule.expected_state === true ? "selected" : ""}>Activado / TRUE</option><option value="false" ${rule.expected_state === false ? "selected" : ""}>Desactivado / FALSE</option></select></label>` : `<label class="field"><span>Alarma si es mayor que</span><input type="number" step="any" value="${escapeHtml(rule.above ?? "")}" data-hmi-alarm-rule-field="above" data-hmi-alarm-rule-index="${index}" /></label>
          <label class="field"><span>Alarma si es menor que</span><input type="number" step="any" value="${escapeHtml(rule.below ?? "")}" data-hmi-alarm-rule-field="below" data-hmi-alarm-rule-index="${index}" /></label>`}
          <label class="field"><span>Retardo para activar (ms)</span><input type="number" min="0" max="86400000" step="100" value="${escapeHtml(rule.delay_ms ?? 0)}" data-hmi-alarm-rule-field="delay_ms" data-hmi-alarm-rule-index="${index}" /></label>
        </div></article>`;
      }).join("") || '<p class="section-help">No hay alarmas configuradas.</p>'}</section>`;
  }

  setLocked(locked) {
    this.locked = locked;
    this.element.querySelectorAll("fieldset").forEach((field) => { field.disabled = locked; });
    this.element.querySelectorAll("[data-remove-node]").forEach((button) => { button.disabled = locked; });
    this.element.querySelectorAll("[data-node-action]").forEach((button) => { button.disabled = locked; });
    this.element.querySelectorAll("[data-scada-add-alarm], [data-scada-remove-alarm], [data-hmi-add-alarm], [data-hmi-remove-alarm]").forEach((button) => { button.disabled = locked; });
    this.element.querySelectorAll("[data-plc-logic-add], [data-plc-logic-remove], [data-plc-boolean-add], [data-plc-boolean-remove]").forEach((button) => { button.disabled = locked; });
  }

  setRuntimeStatus(status, containers = []) {
    this.runtimeStatus = status;
    this.runtimeNodes = new Map(containers.map((item) => [item.node_id, item]));
    const panel = this.element.querySelector("[data-node-lifecycle]");
    if (panel && this.data) {
      panel.innerHTML = this.renderLifecycle(this.data.node_id);
      this.setLocked(this.locked);
    }
  }

  renderLifecycle(nodeId) {
    const runtime = this.runtimeNodes.get(nodeId);
    const status = runtime?.status || "sin desplegar";
    const active = ["running", "paused", "partial", "error"].includes(this.runtimeStatus);
    const missing = !runtime || status === "missing";
    const stopped = ["exited", "created", "stopped", "missing"].includes(status);
    const paused = status === "paused";
    return `<div class="inspector-section-title"><strong>Runtime del nodo</strong><span>${escapeHtml(status)}</span></div>
      ${runtime?.health_status ? `<small class="node-health-status">Healthcheck: ${escapeHtml(runtime.health_status)}</small>` : ""}
      ${runtime?.health_error || runtime?.runtime_error ? `<p class="node-runtime-error" role="status">${escapeHtml(runtime.health_error || runtime.runtime_error)}</p>` : ""}
      ${active && missing ? `<button type="button" class="tiny-button" data-node-action="recreate">Reconstruir nodo ausente</button>` : ""}
      <div class="component-actions"><button type="button" class="tiny-button" data-node-action="start" ${!active || !stopped || missing ? "disabled" : ""}>Iniciar</button>
      <button type="button" class="tiny-button" data-node-action="stop" ${!active || stopped ? "disabled" : ""}>Detener</button>
      <button type="button" class="tiny-button" data-node-action="restart" ${!active || stopped ? "disabled" : ""}>Reiniciar</button>
      <button type="button" class="tiny-button" data-node-action="pause" ${!active || stopped || paused ? "disabled" : ""}>Pausar</button>
      <button type="button" class="tiny-button" data-node-action="resume" ${!active || !paused ? "disabled" : ""}>Reanudar</button></div>`;
  }

  renderTargets() {
    if (!["hmi", "scada"].includes(this.data.device_type)) return "";
    const selected = new Set((this.data.config.target_node_ids || "").split(","));
    const targets = this.getDevices().filter((item) => ["plc-s7", "modbus-tcp"].includes(item.device_type));
    const selector = this.data.device_type === "scada" ? "data-scada-target" : "data-hmi-target";
    const candidates = targets.filter(item => item.node_id !== this.data.node_id);
    const alarmSources = this.data.device_type === "hmi"
      ? this.getDevices().filter(item => item.device_type === "scada" && item.zone_id === this.data.zone_id)
      : [];
    const selectedAlarmSources = new Set((this.data.config.alarm_source_node_ids || "").split(",").filter(Boolean));
    return `<fieldset class="hmi-targets"><legend>Dispositivos adquiridos por ${this.data.device_type === "scada" ? "este SCADA" : "esta HMI"}</legend>
      ${candidates.map((item) => {
        const crossZone = item.zone_id !== this.data.zone_id;
        const route = this.getNetworkRoutes().find((entry) => entry.source_node_id === this.data.node_id && entry.target_node_id === item.node_id);
        return `<div class="target-option-group"><label class="target-option"><input type="checkbox" ${selector} value="${escapeHtml(item.node_id)}" ${selected.has(item.node_id) ? "checked" : ""} /><span>${escapeHtml(item.name)}<small>${escapeHtml(item.ip)} · ${escapeHtml(item.device_type)}${crossZone ? " · otra zona" : ""}</small></span></label>${crossZone && selected.has(item.node_id) ? `<label class="target-route-option"><input type="checkbox" data-scada-route value="${escapeHtml(item.node_id)}" ${route?.enabled ? "checked" : ""} /> Permitir ruta TCP S7/Modbus por gateway</label>` : ""}</div>`;
      }).join("") || '<p>Añade primero un PLC S7 o un dispositivo Modbus.</p>'}</fieldset>
      ${this.data.device_type === "hmi" ? `<fieldset class="hmi-targets"><legend>Alarmas mostradas desde SCADA</legend>${alarmSources.map(item => `<label class="target-option"><input type="checkbox" data-hmi-alarm-source value="${escapeHtml(item.node_id)}" ${selectedAlarmSources.has(item.node_id) ? "checked" : ""}><span>${escapeHtml(item.name)}<small>${escapeHtml(item.node_id)} · misma zona</small></span></label>`).join("") || '<p>Añade un SCADA en la misma zona para mostrar y reconocer sus alarmas.</p>'}</fieldset>` : ""}`;
  }

  renderNetworkServices() {
    if (!this.data || this.data.object_id) return "";
    const inventory = this.getDeviceServices();
    if (!Array.isArray(inventory)) {
      return `<section class="inspector-section service-inventory"><div class="inspector-section-title"><strong>Servicios y puertos</strong></div><p class="section-help">Guarda o vuelve a abrir el escenario para consultar los servicios efectivos declarados por el adaptador.</p></section>`;
    }
    const services = inventory.filter(item => item.node_id === this.data.node_id);
    const endpoints = services.map(service => `<article class="service-endpoint">
      <strong>${escapeHtml(service.service)}</strong>
      <code>${escapeHtml(service.hostname)} (${escapeHtml(service.ip)}):${escapeHtml(service.port)}</code>
      <small>${escapeHtml(service.protocol)} · transporte ${escapeHtml(service.transport)} · ${service.scope === "internal" ? "solo interno" : "red OT"}</small>
      <p>${escapeHtml(service.description)}</p>
      ${service.host_port !== null && service.host_port !== undefined ? `<small>Host: ${escapeHtml(service.host_bind_ip)}:${escapeHtml(service.host_port)} → puerto del contenedor ${escapeHtml(service.port)}</small>` : ""}
    </article>`).join("");
    const emptyMessage = this.data.device_type === "hmi"
      ? "La HMI actúa como cliente S7/Modbus; no expone un listener OT propio."
      : "Este adaptador no declara un listener protocolario para el nodo.";
    return `<section class="inspector-section service-inventory">
      <div class="inspector-section-title"><strong>Servicios y puertos</strong></div>
      ${this.isDirty() ? '<p class="section-help" role="status">Inventario del último guardado. Guarda los cambios para actualizarlo.</p>' : ""}
      ${endpoints || `<p class="section-help">${escapeHtml(emptyMessage)}</p>`}
      <p class="section-help">El inventario describe configuración; el estado del servicio se confirma con healthcheck y diagnóstico.</p>
    </section>`;
  }

  render(data, componentIndex = null) {
    this.componentIndex = componentIndex;
    if (!data) { this.data = null; this.renderEmpty(); return; }
    const protocolOpen = this.data?.node_id === data.node_id && this.element.querySelector(".protocol-settings")?.open;
    this.data = clone(data);
    const { entries, listKey, title, deviceFields } = this.fieldsChanged();
    const isRtu = this.data.device_type === "modbus-tcp" && this.data.config.transport === "rtu";
    if (componentIndex !== null && entries[componentIndex]) {
      const component = entries[componentIndex];
      const definition = getComponentType(component.type);
      document.getElementById('heading-config').textContent = `Configura ${definition.label.toLowerCase()}`;
      this.element.innerHTML = `<div class="inspector-form" data-legacy-field-editor>
        <h2>${escapeHtml(component.name)}</h2>
        <p>Este elemento pertenece a un escenario anterior y mantiene su vínculo con ${escapeHtml(this.data.name)}. Sus propiedades se editan aquí.</p>
        <fieldset><legend>Propiedades de ${escapeHtml(definition.label.toLowerCase())}</legend>${this.renderComponent(component, componentIndex)}</fieldset>
        <button type="button" class="button button-secondary" data-open-owner>Ver controlador conectado</button>
        <button type="button" class="button button-secondary" data-workspace-view="catalog">Añadir elemento independiente</button>
      </div>`;
      this.element.dataset.componentListKey = listKey;
      this.setLocked(this.locked);
      return;
    }
    const definitions = listComponentTypesForDevice(this.data.device_type).filter(d => this.data.device_type !== 'plc-s7' || ['io_channel','remote_input'].includes(d.type));
    const addButtons = `<label class="component-type-picker"><span class="sr-only">Tipo de señal que añadir</span><select data-component-type>${definitions.map((definition) => `<option value="${definition.type}">${escapeHtml(definition.label)}</option>`).join("")}</select></label><button class="button button-secondary" type="button" data-add-component="selected">Añadir señal</button>`;
    const deviceInputs = deviceFields.map((field) => renderField(
      { ...field, type: field.type || "number" },
      field.value,
      `data-device-field="${field.path}" ${field.disabled ? "disabled" : ""}`,
    )).join("");
    const components = entries.map((component, index) => this.renderComponent(component, index)).join("");
    this.element.innerHTML = `
      <div class="inspector-form">
        <fieldset class="device-editor"><legend class="sr-only">Configuración del dispositivo</legend>
        <div class="field-grid">
          ${renderField({ label: "Nombre", type: "text", full: true }, this.data.name, 'data-device-field="name"')}
          ${renderField({ label: "Dirección IP", type: "text", full: true }, this.data.ip, 'data-device-field="ip"')}
        </div>
        ${deviceInputs ? `<details class="protocol-settings" ${protocolOpen ? "open" : ""}><summary>Ajustes de ${this.data.device_type === "plc-s7" ? "S7 y acceso externo" : this.data.device_type === "modbus-tcp" ? "Modbus y acceso externo" : this.data.device_type === "scada" ? "adquisición" : this.data.device_type === "ethernet-switch" ? "capacidad del switch" : "la política"}</summary><div class="field-grid">${deviceInputs}</div>${["plc-s7", "modbus-tcp"].includes(this.data.device_type) ? `<p class="section-help">${isRtu ? "El túnel RTU solo está disponible dentro de la red Docker del escenario; no se publica en el host." : "Los puertos OT no se publican en el host por defecto. Al habilitar el acceso, Anvil crea un proxy de ingreso; 127.0.0.1 limita el acceso al propio host. Si eliges otra IP del host, limita los clientes con el firewall del sistema."}</p>` : ""}</details>` : ""}
        ${this.renderNetworkServices()}
        ${["plc-s7", "modbus-tcp"].includes(this.data.device_type) ? `<section class="inspector-section">
          <div class="inspector-section-title"><strong>${title}</strong><div class="component-actions">${addButtons}</div></div>
          ${this.data.device_type === "modbus-tcp" ? (isRtu ? '<p class="section-help">RTU conserva las tramas y CRC sobre un pseudo-terminal 8N1. El túnel raw TCP no añade MBAP; los clientes usan socket://. Baudios son nominales; no se simula la señal eléctrica, paridad distinta de N ni RS-485 multi-drop.</p>' : '<p class="section-help">El retardo se aplica a cada petición. El modo de fallo sustituye toda respuesta normal por una excepción del dispositivo o cierra la conexión. Útil para probar timeouts y recuperación del cliente.</p>') : ""}
          ${components || `<div class="empty-list">${this.data.device_type === 'plc-s7' ? "Añade sensores y actuadores independientes desde el catálogo. Al conectar sus puertos se crearán aquí los canales de E/S." : "Añade puntos Modbus con los controles de arriba."}</div>`}
          <section class="inspector-section"><div class="component-actions"><button type="button" class="tiny-button" data-export-device-profile>Exportar perfil JSON</button><button type="button" class="tiny-button" data-import-device-profile>Importar perfil JSON</button><input type="file" accept="application/json,.json" data-device-profile-file hidden></div><p class="section-help">Incluye configuración y mapa de direcciones resuelto. No sustituye el archivo de configuración del fabricante.</p></section>
        </section>` : `<p class="section-help">${escapeHtml({hmi: "Selecciona los dispositivos cuyas señales quieres consultar.", scada: "El contenedor adquiere datos por S7/Modbus y evalúa las reglas de alarma configuradas.", gateway: "Representación del gateway. Este nodo no enruta tráfico.", firewall: "Controla lecturas y escrituras de la API. Una lista vacía deniega todos los puertos. No filtra el tráfico directo entre contenedores.", "attacker-station": this.data.config.participant_access_enabled === true ? "Conectada únicamente a su red interna de zona. Los clientes Snap7 y PyModbus están disponibles dentro del contenedor; no se publica acceso en el host." : "Desconectada de las redes OT por defecto. Activa el acceso para conectarla únicamente a su red interna de zona.", "engineering-station": this.data.config.engineering_access_enabled === true ? "Conectada a la red interna de su zona; el acceso al terminal queda limitado a diseñador, instructor y administrador." : "Estación de ingeniería aislada por defecto. Activa el acceso para conectarla a su red interna y habilitar el terminal.", "ethernet-switch": `La zona Docker implementa la conmutación L2 real. Conecta cada equipo de la zona a este nodo; capacidad: ${Number(this.data.config.switch_port_capacity || 24)} puertos. No simula VLAN ni routing IP.`}[this.data.device_type])}</p>`}
        ${this.renderPlcLogic()}
        ${this.renderTargets()}
        ${this.renderHmiAlarms()}
        ${this.renderScadaAlarms()}
        ${!this.data.object_id && this.data.device_type !== "ethernet-switch" ? `<section class="inspector-section" data-node-lifecycle>${this.renderLifecycle(this.data.node_id)}</section>` : ""}
        <details class="advanced-settings"><summary>Identificador del dispositivo</summary><code>${escapeHtml(this.data.node_id)}</code></details>
        </fieldset>
        ${["attacker-station", "engineering-station"].includes(this.data.device_type) ? (() => { const engineering = this.data.device_type === "engineering-station"; const enabled = engineering ? this.data.config.engineering_access_enabled === true : this.data.config.participant_access_enabled === true; return `<section class="inspector-section participant-terminal-entry"><div class="inspector-section-title"><strong>${engineering ? "Terminal de ingeniería" : "Consola de participante"}</strong><button class="tiny-button" type="button" data-participant-terminal="${escapeHtml(this.data.node_id)}" data-terminal-kind="${engineering ? "engineering" : "participant"}" data-terminal-enabled="${enabled ? "true" : "false"}">Abrir terminal</button></div><p class="section-help">${enabled ? "Abre una shell sin privilegios en esta estación cuando el escenario esté desplegado." : "Activa y guarda el acceso de red de esta estación para habilitar su consola."}</p></section>`; })() : ""}
        ${this.data.device_type === "hmi" ? `
          <section class="inspector-section hmi-preview">
            <div class="inspector-section-title"><strong>Lectura de la HMI</strong>
              <button class="tiny-button" type="button" data-hmi-read>Leer valores</button></div>
            <div data-hmi-values class="hmi-values" role="status">Selecciona los dispositivos, guarda y despliega para consultar sus valores.</div>
          </section>` : ""}
        ${this.data.device_type === "scada" ? `
          <section class="inspector-section hmi-preview">
            <div class="inspector-section-title"><strong>Adquisición SCADA</strong>
              <button class="tiny-button" type="button" data-scada-read>Actualizar</button></div>
            <div data-scada-values class="hmi-values" role="status">Guarda y despliega para ver muestras y alarmas.</div>
          </section>` : ""}
        <button class="remove-node" type="button" data-remove-node>Eliminar este dispositivo</button>
      </div>`;
    this.element.dataset.componentListKey = listKey;
    this.setLocked(this.locked);
  }

  renderComponent(component, index) {
    const definition = getComponentType(component.type);
    const peer = component.type === "remote_input" ? this.getDevices().find((item) => item.node_id === component.source_node_id) : null;
    const crossZone = peer && peer.zone_id !== this.data.zone_id;
    const peerRoute = crossZone ? this.getNetworkRoutes().find((item) => item.source_node_id === this.data.node_id && item.target_node_id === peer.node_id) : null;
    const fields = definition.fields.filter((field) => {
      if (component.type === "io_channel" && ["min_value", "max_value"].includes(field.path)) {
        return component.data_type === "REAL";
      }
      if (component.type === "io_channel" && field.path === "bit_offset") {
        return component.data_type === "BOOL";
      }
      if (["coil", "discrete_input"].includes(component.type) && ["min_value", "max_value", "unit"].includes(field.path)) {
        return false;
      }
      return true;
    }).map((field) => {
      if (component.type === "remote_input" && field.path === "source_node_id") {
        field = { ...field, options: [{ value: "", label: "Selecciona un PLC…" }, ...this.getDevices().filter((item) => item.device_type === "plc-s7" && item.node_id !== this.data.node_id).map((item) => ({ value: item.node_id, label: `${item.name} · ${item.ip}` }))] };
      }
      if (component.type === "remote_input" && field.path === "source_address") {
        const source = this.getDevices().find((item) => item.node_id === component.source_node_id);
        field = { ...field, options: [{ value: "", label: "Selecciona una señal…" }, ...s7Signals(source).filter((point) => point.data_type === component.data_type).map((point) => ({ value: point.address, label: `${point.label} · ${point.address}` }))] };
      }
      return renderField(field, getPath(component, field.path),
      `data-component-index="${index}" data-component-field="${field.path}"`,
      );
    }).join("");
    return `
      <article class="component-card">
        <header class="component-card-header"><strong>${escapeHtml(definition.icon)} · ${escapeHtml(definition.label)} ${index + 1}</strong>
          <div class="component-card-actions">${["admin", "instructor"].includes(window.anvilUser?.role) ? `<button class="tiny-button" type="button" data-share-component="${index}" ${this.locked ? "disabled" : ""}>Compartir preset</button>` : ""}<button class="remove-component" type="button" aria-label="Eliminar ${escapeHtml(component.name)}" data-remove-component="${index}">×</button></div>
        </header>
        <div class="component-card-body">${component.type === "remote_input" ? '<p class="section-help">Este PLC inicia lecturas S7 del emisor. Conserva el último valor ante un fallo; la consola indica su calidad. El periodo debe ser menor o igual al umbral de obsolescencia.</p>' : ""}${fields}${crossZone ? `<label class="target-route-option"><input type="checkbox" data-s7-route value="${escapeHtml(peer.node_id)}" ${peerRoute?.enabled ? "checked" : ""} /> Permitir enlace S7 al PLC remoto mediante gateway</label>` : ""}</div>
      </article>`;
  }

  addSharedComponent(preset) {
    if (!this.data || this.locked) throw new Error("Selecciona un dispositivo detenido para añadir el componente.");
    if (this.data.device_type !== preset.device_type) throw new Error("Este preset corresponde a otro tipo de dispositivo.");
    const { entries } = this.fieldsChanged();
    const component = clone(preset.config);
    if (component.type === "io_channel") component.channel_id = `channel-${crypto.randomUUID()}`;
    entries.push(component);
    this.onChange(clone(this.data));
    this.render(this.data);
  }

  renderEmpty() {
    this.element.innerHTML = `
      <div class="inspector-empty"><strong>Ningún dispositivo seleccionado</strong>
        <span>Selecciona un nodo del lienzo para editar su configuración y componentes.</span>
      </div>`;
  }
}
