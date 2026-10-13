import { nextAvailableIp } from "./editor-utils.js?v=field-live-3";
import { scenarioApi } from "./api.js?v=rf26-service-inventory-1";
import { ScenarioCanvas } from "./canvas.js?v=canvas-links-live-20261013";
import { ScenarioEventStream } from "./events.js?v=field-live-3";
import { Inspector } from "./inspector.js?v=rf26-service-inventory-1";
import { ScenarioLogStream } from "./logs.js?v=field-live-3";
import { configureNodeCatalog, listNodeTypes } from "./nodes/registry.js?v=field-live-3";
import { RuntimePanel } from "./runtime.js?v=rf38-fc23-write-20261013";
import { initWorkspace, showWorkspace } from "./workspace.js?v=ui-polish-20261009-v2";
import { FieldEditor } from "./field-editor.js?v=nearest-field-arrows-20261009";
import { buildHmiSynoptic } from "./hmi-synoptic.js?v=hmi-map-compact-ux-20261010";
import { clearHmiInputEdited, markHmiInputEdited, updateHmiLiveView, updateHmiSynoptic } from "./hmi-live-view.js?v=hmi-map-compact-ux-20261010";
import { ParticipantTerminal } from "./participant-terminal.js?v=participant-terminal-1";
import { scadaTrend } from "./scada-trend.mjs?v=scada-quality-gaps-1";

import "./nodes/plc-s7.js?v=canvas-node-cards-20261013";
import "./nodes/modbus-tcp.js?v=canvas-node-cards-20261013";
import "./nodes/virtual.js?v=canvas-node-cards-20261013";
import "./components/valve.js?v=field-live-3";
import "./components/analog_sensor.js?v=field-seed-1";
import "./components/remote_input.js?v=field-live-3";
import "./components/modbus.js?v=field-live-3";
import "./components/discrete.js?v=field-live-3";
import "./components/pump.js?v=field-live-3";
import "./components/io_channel.js?v=s7-addresses-1";


initWorkspace();
document.addEventListener("input", (event) => {
  if (event.target.matches("input[data-hmi-value], input[data-scada-value]")) markHmiInputEdited(event.target);
});
document.addEventListener("change", (event) => {
  if (event.target.matches("input[data-hmi-value], input[data-scada-value]")) markHmiInputEdited(event.target);
});

const elements = {
  name: document.getElementById("scenario-name"),
  subnet: document.getElementById("scenario-subnet"),
  status: document.getElementById("scenario-status"),
  list: document.getElementById("scenario-list"),
  palette: document.getElementById("node-palette"),
  apiIndicator: document.getElementById("api-indicator"),
  eventConnection: document.getElementById("event-connection"),
  eventLog: document.getElementById("event-log"),
  save: document.getElementById("save-button"),
  deploy: document.getElementById("deploy-button"),
  restart: document.getElementById("restart-button"),
  pause: document.getElementById("pause-button"),
  resume: document.getElementById("resume-button"),
  stop: document.getElementById("stop-button"),
  create: document.getElementById("new-scenario-button"),
  remove: document.getElementById("delete-scenario-button"),
  toast: document.getElementById("toast"),
  importButton: document.getElementById("import-scenario-button"),
  importFile: document.getElementById("import-scenario-file"),
  exportButton: document.getElementById("export-scenario-button"),
  exportDeploymentButton: document.getElementById("export-deployment-button"),
  cloneButton: document.getElementById("clone-scenario-button"),
  migrateFieldCopyButton: document.getElementById("migrate-field-copy-button"),
  shareButton: document.getElementById("share-scenario-button"),
  devicePicker: document.getElementById("device-selector"),
  feedback: document.getElementById("editor-feedback"),
  zoneList: document.getElementById("zone-list"),
  addZone: document.getElementById("add-zone-button"),
};

const participantTerminal = new ParticipantTerminal(document.getElementById("participant-terminal-dialog"));

const state = {
  scenarioId: null,
  status: "stopped",
  dirty: false,
  busy: false,
  scenarios: [],
  activity: [],
  fieldObjects: [],
  signalConnections: [],
  networkRoutes: [],
  deviceServices: null,
  egressRules: [],
  networkRouteEndpoints: [],
  zones: [],
  hmiBusy: false,
  hmiConsoleBusy: false,
  hmiConsoleGeneration: 0,
  hmiConsoleNodeId: "",
};

let toastTimer;

function showToast(message, type = "success") {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.feedback.hidden = false;
  elements.feedback.textContent = message;
  elements.feedback.className = `editor-feedback ${type}`;
  elements.toast.className = `toast visible ${type === "error" ? "error" : ""}`;
  toastTimer = setTimeout(() => elements.toast.classList.remove("visible"), 3600);
}

function setBusy(isBusy) {
  state.busy = isBusy;
  elements.save.disabled = isBusy || state.status !== "stopped";
  elements.deploy.disabled = isBusy || state.status !== "stopped";
  elements.restart.disabled = isBusy || !["running", "partial"].includes(state.status);
  elements.pause.disabled = isBusy || state.status !== "running";
  elements.resume.disabled = isBusy || state.status !== "paused";
  elements.stop.disabled = isBusy || !["running", "paused", "partial", "error"].includes(state.status);
  elements.create.disabled = isBusy;
  elements.remove.disabled = isBusy || !state.scenarioId || state.status !== "stopped";
  elements.exportButton.disabled = isBusy || !state.scenarioId;
  elements.exportDeploymentButton.disabled = isBusy || !state.scenarioId;
  elements.cloneButton.disabled = isBusy || !state.scenarioId;
  if (elements.migrateFieldCopyButton) {
    const available = hasLegacyFieldComponents() && canEditScenario();
    elements.migrateFieldCopyButton.disabled = isBusy || !available;
    elements.migrateFieldCopyButton.hidden = !available;
  }
  if (elements.shareButton) elements.shareButton.disabled = isBusy || !state.scenarioId;
  document.getElementById("history-button").disabled = isBusy || !state.scenarioId;
  elements.importButton.disabled = isBusy;
  elements.list.querySelectorAll("button").forEach((button) => { button.disabled = isBusy; });
  const locked = isBusy || state.status !== "stopped" || !canEditScenario();
  elements.name.disabled = locked;
  elements.subnet.disabled = locked;
  renderZones();
  elements.palette.querySelectorAll("button").forEach((button) => { button.disabled = locked; button.draggable = !locked; });
  canvas.setLocked(locked);
  inspector.setLocked(locked);
  document.getElementById("editing-notice").hidden = state.status === "stopped";
  applyRoleControls();
}

function scenarioAccessRole() {
  const selected = state.scenarios.find((scenario) => scenario.id === state.scenarioId);
  if (selected?.access_role === "owner") return window.anvilUser?.role || "designer";
  return selected?.access_role || window.anvilUser?.role || "admin";
}

function canEditScenario() {
  return ["admin", "instructor", "designer"].includes(scenarioAccessRole());
}

function canWriteScenario() {
  return scenarioAccessRole() !== "designer";
}

function canOperateScenario() {
  return ["admin", "instructor"].includes(scenarioAccessRole());
}

function syncHmiConsole() {
  const select = document.querySelector("[data-hmi-console-device]");
  const navigation = document.querySelector("[data-hmi-navigation]");
  if (!select || !navigation) return;
  const hmis = canvas.getDevices().filter((device) => device.device_type === "hmi");
  const previous = state.hmiConsoleNodeId || select.value;
  select.replaceChildren(new Option("Selecciona una HMI…", ""), ...hmis.map((device) => new Option(device.name, device.node_id)));
  state.hmiConsoleNodeId = hmis.some((device) => device.node_id === previous) ? previous : hmis[0]?.node_id || "";
  select.value = state.hmiConsoleNodeId;
  navigation.hidden = hmis.length === 0;
  const status = document.querySelector("[data-hmi-console-status]");
  const output = document.querySelector("[data-hmi-console-values]");
  if (!hmis.length) {
    status.textContent = "Añade una HMI al escenario y configura los dispositivos que debe adquirir.";
  } else if (!state.scenarioId) {
    status.textContent = "Guarda el escenario para habilitar la consola de operación.";
  }
}

async function refreshHmiConsole() {
  const status = document.querySelector("[data-hmi-console-status]");
  const output = document.querySelector("[data-hmi-console-values]");
  const nodeId = document.querySelector("[data-hmi-console-device]")?.value;
  state.hmiConsoleNodeId = nodeId || "";
  if (!nodeId) {
    status.textContent = "Selecciona una HMI configurada.";
    return;
  }
  if (state.hmiConsoleBusy || state.hmiBusy) return;
  if (!state.scenarioId || state.dirty || !["running", "partial"].includes(state.status)) {
    status.textContent = state.dirty ? "Guarda los cambios antes de operar la HMI." : "Despliega el escenario para leer o escribir señales.";
    return;
  }
  const scenarioId = state.scenarioId;
  const generation = ++state.hmiConsoleGeneration;
  state.hmiConsoleBusy = true;
  if (!output.querySelector(".hmi-live-grid")) status.textContent = "Consultando señales y alarmas configuradas…";
  try {
    const hmiSnapshot = await inspector.onReadHmi(nodeId, output);
    if (generation !== state.hmiConsoleGeneration || scenarioId !== state.scenarioId) return;
    if (!hmiSnapshot) {
      status.textContent = "Falló la lectura HMI; no se presentan valores nuevos.";
      return;
    }
    const fields = canvas.getFieldObjects();
    if (fields.length && hmiSnapshot) {
      const hmi = canvas.getDevices().find((device) => device.node_id === nodeId && device.device_type === "hmi");
      updateHmiSynoptic(output, buildHmiSynoptic({
        hmi,
        fieldObjects: fields,
        processConnections: canvas.processConnections,
        signalConnections: canvas.signalConnections,
        snapshot: hmiSnapshot,
      }));
    } else {
      output.querySelector(":scope > .hmi-synoptic")?.remove();
    }
    status.textContent = "Lectura HMI · actualización automática cada 2 segundos";
  } finally {
    if (generation === state.hmiConsoleGeneration) state.hmiConsoleBusy = false;
  }
}

function applyRoleControls() {
  const role = scenarioAccessRole();
  const canEdit = ["admin", "instructor", "designer"].includes(role);
  const canOperate = canOperateScenario();
  const canShare = ["admin", "instructor", "designer"].includes(role);
  const canExport = role !== "participant";
  for (const element of [elements.create, elements.save, elements.importButton]) {
    if (element) element.hidden = !canEdit;
  }
  for (const element of [elements.deploy, elements.restart, elements.pause, elements.resume, elements.stop]) {
    if (element) element.hidden = !canOperate;
  }
  for (const element of [elements.remove, elements.cloneButton, elements.exportButton, elements.exportDeploymentButton]) {
    if (element) element.hidden = !canEdit;
  }
  const history = document.getElementById("history-button");
  if (history) history.hidden = !canExport;
  if (elements.shareButton) elements.shareButton.hidden = !canShare || !state.scenarioId;
  runtimePanel.setCapabilities({
    capture: canOperate,
    exercise: canOperate,
    auditExport: canExport,
    evidenceExport: ["admin", "instructor"].includes(role),
    write: canWriteScenario(),
  });
  inspector.element.querySelectorAll("[data-node-action], [data-field-fault], [data-force-field], [data-release-field]").forEach((control) => {
    control.hidden = !canOperate;
  });
  inspector.element.querySelectorAll("[data-hmi-write], [data-scada-write], [data-scada-ack], [data-hmi-ack]").forEach((control) => {
    control.hidden = !canWriteScenario();
  });
  inspector.element.querySelectorAll("[data-participant-terminal]").forEach((control) => {
    const allowed = control.dataset.terminalKind === "engineering"
      ? ["admin", "instructor", "designer"]
      : ["admin", "instructor", "participant"];
    control.hidden = !allowed.includes(role);
    control.disabled = control.dataset.terminalEnabled !== "true" || !state.scenarioId || !["running", "partial"].includes(state.status);
  });
}

function statusLabel(status) {
  return {
    stopped: "Detenido",
    deploying: "Desplegando",
    running: "En ejecución",
    paused: "En pausa",
    partial: "Parcial",
    error: "Error",
  }[status] || status;
}

function setLiveMessage(output, message, className = "hmi-refresh-error") {
  let notice = output.querySelector(`:scope > .${className}`);
  if (!notice) {
    notice = document.createElement("p");
    notice.className = className;
    notice.setAttribute("role", "status");
    output.prepend(notice);
  }
  notice.textContent = message;
}

function updateStatus(status, containers = []) {
  state.status = status;
  elements.status.className = `status-pill status-${status}`;
  elements.status.querySelector("span:last-child").textContent = statusLabel(status);
  canvas.syncRuntimeStatus(containers);
  inspector.setRuntimeStatus(status, containers);
  runtimePanel.setRunning(["running", "partial", "error"].includes(status), status === "paused");
  setBusy(state.busy);
  if (fieldUI.selected) fieldUI.render();
}

function setEventConnection(label, mode = "") {
  elements.eventConnection.textContent = label;
  elements.eventConnection.className = `event-connection ${mode}`.trim();
}

function addActivity(message, tone = "") {
  state.activity.unshift({
    message,
    tone,
    time: new Date().toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
  });
  state.activity = state.activity.slice(0, 12);
  elements.eventLog.innerHTML = state.activity
    .map(
      (item) => `
        <div class="event-item ${item.tone}">
          <time>${item.time}</time><span>${escapeHtml(item.message)}</span>
        </div>`,
    )
    .join("");
}

function clearActivity(message = "Selecciona un escenario para recibir eventos.") {
  state.activity = [];
  elements.eventLog.innerHTML = `<div class="event-log-empty">${escapeHtml(message)}</div>`;
}

function markDirty() {
  state.dirty = true;
  elements.save.textContent = "Guardar cambios";
  updateDevicePicker();
}

function renderZones() {
  const levels = ["L0", "L1", "L2", "L3", "L4", "L5", "DMZ"];
  const disabled = state.status !== "stopped" || state.busy || !canEditScenario() ? "disabled" : "";
  elements.zoneList.innerHTML = state.zones.map((zone, index) => `<fieldset class="zone-row" data-zone-index="${index}">
    <legend>${escapeHtml(zone.name || `Zona ${index + 1}`)}</legend>
    <label><span>ID</span><input ${disabled} data-zone-key="zone_id" value="${escapeHtml(zone.zone_id)}" required pattern="[A-Za-z0-9_.-]+" maxlength="32"></label>
    <label><span>Nombre</span><input ${disabled} data-zone-key="name" value="${escapeHtml(zone.name)}" required maxlength="80"></label>
    <label><span>Nivel Purdue</span><select ${disabled} data-zone-key="purdue_level">${levels.map(level => `<option ${zone.purdue_level === level ? "selected" : ""}>${level}</option>`).join("")}</select></label>
    <label><span>Subred IPv4</span><input ${disabled} data-zone-key="subnet" value="${escapeHtml(zone.subnet)}" required placeholder="172.30.10.0/24"></label>
    <button ${disabled} type="button" class="tiny-button" data-remove-zone="${index}" aria-label="Eliminar zona ${escapeHtml(zone.name)}">Eliminar zona</button>
  </fieldset>`).join("");
  if (elements.addZone) elements.addZone.disabled = state.status !== "stopped" || state.busy || !canEditScenario();
  const hint = document.getElementById("canvas-network-hint");
  if (hint) hint.textContent = state.zones.length ? `${state.zones.length} redes internas · sin rutas entre zonas` : "Una red compartida por escenario";
}

function markClean() {
  state.dirty = false;
  elements.save.textContent = "Guardar";
}

function suggestIp() {
  const selected = inspector.data;
  const zone = state.zones.find(item => item.zone_id === selected?.zone_id) || state.zones[0];
  return nextAvailableIp(zone?.subnet || elements.subnet.value, canvas.getDevices().map((device) => device.ip));
}

function ipInSubnet(address, subnet) {
  const parse = value => {
    const [ip, prefixText] = value.split("/");
    const parts = ip.split(".").map(Number);
    if (parts.length !== 4 || parts.some(part => !Number.isInteger(part) || part < 0 || part > 255)) return null;
    return { value: parts.reduce((result, part) => result * 256 + part, 0), prefix: prefixText === undefined ? 32 : Number(prefixText) };
  };
  const ip = parse(address), net = parse(subnet);
  if (!ip || !net || net.prefix < 0 || net.prefix > 32) return false;
  const size = 2 ** (32 - net.prefix);
  return Math.floor(ip.value / size) === Math.floor(net.value / size);
}

function updateDevicePicker() {
  const selected = elements.devicePicker.value;
  elements.devicePicker.innerHTML = '<option value="">Selecciona un dispositivo…</option>' + canvas.getDevices().map((device) =>
    `<option value="${escapeHtml(device.node_id)}">${escapeHtml(device.name)} · ${escapeHtml(device.device_type)}</option>`).join("");
  elements.devicePicker.innerHTML += canvas.getFieldObjects().map(field => `<option value="${escapeHtml(field.object_id)}">${escapeHtml(field.name)} · campo</option>`).join('');
  elements.devicePicker.value = selected;
}

const inspector = new Inspector(document.getElementById("inspector"), {
  getDevices: () => canvas.getDevices(),
  getDeviceServices: () => state.deviceServices,
  isDirty: () => state.dirty,
  getZones: () => state.zones,
  getNetworkRoutes: () => state.networkRoutes,
  onExportDeviceProfile: exportDeviceProfile,
  onImportDeviceProfile: importDeviceProfileFromUi,
  onNetworkRoutesChange(routes) {
    state.networkRoutes = routes;
    canvas.setNetworkRoutes(routes);
    state.networkRouteEndpoints = [];
    canvas.setNetworkRouteEndpoints([]);
    markDirty();
  },
  onChange(data) {
    canvas.updateSelected(data);
    markDirty();
  },
  onRemove() {
    canvas.removeSelected();
    markDirty();
  },
  onPublishComponent(component, deviceType) {
    const dialog = document.getElementById("component-library-dialog");
    const form = document.getElementById("publish-component-form");
    form.hidden = false;
    form.elements.title.value = component.name || "Componente compartido";
    form.elements.description.value = `${component.type} reutilizable para ${deviceType}`;
    form.elements.device_type.value = deviceType;
    form.elements.component_type.value = component.type;
    form.elements.config.value = JSON.stringify(component);
    dialog.showModal();
    loadComponentLibrary();
  },
  async onReadHmi(nodeId, output) {
    if (state.hmiBusy) return;
    if (!state.scenarioId || state.dirty || !["running", "partial"].includes(state.status)) {
      // Keep the last successful snapshot visible while the runtime is stopped
      // or the draft has unsaved changes. A refresh must not erase operator data.
      let message = output.querySelector(":scope > .hmi-refresh-error");
      if (!message) {
        message = document.createElement("p");
        message.className = "hmi-refresh-error";
        message.setAttribute("role", "status");
        output.prepend(message);
      }
      message.textContent = "Guarda los cambios y despliega el escenario antes de leer valores nuevos.";
      return;
    }
    state.hmiBusy = true;
    output.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(`/api/scenarios/${state.scenarioId}/hmi/${encodeURIComponent(nodeId)}/values`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      output.querySelector(":scope > .hmi-refresh-error")?.remove();
      delete output.dataset.refreshError;
      const valuesHtml = result.values.length
        ? result.values.map((item) => {
          const valueControl = canWriteScenario() && item.access === "read_write" && item.data_type === "BOOL"
            ? `<label class="hmi-command"><input data-hmi-value data-data-type="BOOL" type="checkbox" ${item.value ? "checked" : ""}><span>Activar</span></label>`
            : canWriteScenario() && item.access === "read_write" && item.data_type === "UINT16"
              ? `<label class="hmi-command"><span class="sr-only">Nuevo valor para ${escapeHtml(item.address)}</span><input data-hmi-value data-data-type="UINT16" type="number" min="0" max="65535" step="1" value="${escapeHtml(item.value)}"></label>`
              : canWriteScenario() && item.access === "read_write" && item.data_type === "REAL"
                ? `<label class="hmi-command"><span class="sr-only">Nueva consigna para ${escapeHtml(item.address)}${item.unit ? ` (${escapeHtml(item.unit)})` : ""}</span><input data-hmi-value data-data-type="REAL" type="number" step="any" ${Number.isFinite(item.min_value) ? `min="${item.min_value}"` : ""} ${Number.isFinite(item.max_value) ? `max="${item.max_value}"` : ""} value="${escapeHtml(item.value)}"></label>`
              : "";
          return `<article class="hmi-point" data-hmi-live-key="point:${escapeHtml(item.node_id)}:${escapeHtml(item.address)}" data-hmi-target-node="${escapeHtml(item.node_id)}" data-hmi-address="${escapeHtml(item.address)}">
            <div><strong>${escapeHtml(item.name || item.component_name || item.address)}</strong><small>${escapeHtml(item.node_id)} · ${escapeHtml(item.address)}</small></div>
            <output>${escapeHtml(item.value)}${item.unit ? ` ${escapeHtml(item.unit)}` : ""}</output>
            ${item.range_diagnostic ? `<small class="hmi-quality" data-quality="out_of_range" role="status">Fuera de rango · ${escapeHtml(item.range_diagnostic.message)}</small>` : item.communication ? `<small class="hmi-quality">${escapeHtml(item.communication.quality)}</small>` : ""}
            ${valueControl ? `${valueControl}<button class="tiny-button" type="button" data-hmi-write>Escribir</button>` : '<small class="hmi-readonly">Solo lectura</small>'}
          </article>`;
        }).join("")
        : "<p>No hay valores legibles en los Node IDs configurados.</p>";
      const alarmsHtml = (result.alarms || []).map(alarm => `<article class="hmi-point" data-hmi-live-key="alarm:${escapeHtml(alarm.target_node_id)}:${escapeHtml(alarm.address)}:${alarm.hmi_owned ? "hmi" : escapeHtml(alarm.scada_node_id || "scada")}"><div><strong>${alarm.active ? "Alarma activa" : alarm.pending ? "Condición pendiente" : "Normal"}</strong><small>${escapeHtml(alarm.target_node_id)} · ${escapeHtml(alarm.address)} · ${escapeHtml(alarm.value ?? "sin dato")}${alarm.hmi_owned ? " · regla HMI" : " · regla SCADA"}${alarm.pending ? ` · retardo ${escapeHtml(alarm.delay_ms)} ms` : ""}${alarm.stale ? " · último estado conocido, esperando lectura" : ""}</small></div><output>${alarm.stale ? "Lectura pendiente" : alarm.pending ? "Esperando retardo" : alarm.active ? alarm.acknowledged ? "Reconocida" : "Sin reconocer" : "—"}</output>${canWriteScenario() && alarm.active && !alarm.acknowledged && !alarm.stale ? `<button class="tiny-button" type="button" data-hmi-ack ${alarm.scada_node_id ? `data-scada="${escapeHtml(alarm.scada_node_id)}"` : ""} data-target="${escapeHtml(alarm.target_node_id)}" data-address="${escapeHtml(alarm.address)}">Reconocer</button>` : ""}</article>`).join("");
      const alarmHistoryHtml = (result.alarm_history || []).map(item => `<article class="hmi-point" data-hmi-live-key="history:${escapeHtml(item.target_node_id)}:${escapeHtml(item.address)}:${escapeHtml(item.occurred_at)}:${escapeHtml(item.transition)}"><div><strong>${item.transition === "activated" ? "Activada" : "Normalizada"}</strong><small>${escapeHtml(item.target_node_id)} · ${escapeHtml(item.address)} · ${escapeHtml(item.occurred_at)}</small></div><output>${escapeHtml(item.value ?? "—")}</output></article>`).join("");
      output.removeAttribute("aria-busy");
      updateHmiLiveView(output, `<div class="hmi-live-grid">${valuesHtml}${alarmsHtml ? `<h3 data-hmi-live-key="section:alarms">Alarmas</h3>${alarmsHtml}` : ""}${alarmHistoryHtml ? `<h3 data-hmi-live-key="section:history">Historial de transiciones HMI</h3>${alarmHistoryHtml}` : ""}</div>`);
      return result;
    } catch (error) {
      output.removeAttribute("aria-busy");
      output.dataset.refreshError = error.message;
      let message = output.querySelector(":scope > .hmi-refresh-error");
      if (!message) {
        message = document.createElement("p");
        message.className = "hmi-refresh-error";
        message.setAttribute("role", "status");
        output.prepend(message);
      }
      message.textContent = `Error al leer; se mantiene la última lectura: ${error.message}`;
      return null;
    } finally {
      output.removeAttribute("aria-busy");
      state.hmiBusy = false;
    }
  },
  async onWriteHmi(nodeId, point, output) {
    if (state.hmiBusy) {
      output.setAttribute("aria-busy", "true");
      return;
    }
    if (!state.scenarioId || state.dirty || !["running", "partial"].includes(state.status)) {
      output.dataset.refreshError = "La escritura requiere un escenario guardado y desplegado.";
      return;
    }
    state.hmiBusy = true;
    let wrote = false;
    try {
      const response = await fetch(`/api/scenarios/${state.scenarioId}/hmi/${encodeURIComponent(nodeId)}/write`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(point),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      showToast(`Escritura aplicada a ${point.target_node_id} · ${point.address}`);
      wrote = true;
    } catch (error) {
      let message = output.querySelector(":scope > .hmi-refresh-error");
      if (!message) {
        message = document.createElement("p");
        message.className = "hmi-refresh-error";
        message.setAttribute("role", "status");
        output.prepend(message);
      }
      message.textContent = `Error al escribir: ${error.message}`;
    } finally {
      state.hmiBusy = false;
    }
    if (wrote) {
      const card = [...output.querySelectorAll("[data-hmi-target-node][data-hmi-address]")]
        .find((item) => item.dataset.hmiTargetNode === point.target_node_id && item.dataset.hmiAddress === point.address);
      const input = card?.querySelector("input[data-hmi-value]");
      if (input) clearHmiInputEdited(input);
      await inspector.onReadHmi(nodeId, output);
    } else {
      let message = output.querySelector(":scope > .hmi-refresh-error");
      if (!message) {
        message = document.createElement("p");
        message.className = "hmi-refresh-error";
        message.setAttribute("role", "status");
        output.prepend(message);
      }
      message.textContent = "No se pudo completar la escritura; se mantiene la última lectura.";
    }
  },
  async onAcknowledgeHmi(nodeId, alarm, output) {
    if (state.hmiBusy) return;
    state.hmiBusy = true;
    try {
      const response = await fetch(`/api/scenarios/${state.scenarioId}/hmi/${encodeURIComponent(nodeId)}/alarms/acknowledge`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(alarm),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      state.hmiBusy = false;
      await inspector.onReadHmi(nodeId, output);
    } catch (error) { setLiveMessage(output, `No se pudo reconocer la alarma: ${error.message}`); }
    finally { state.hmiBusy = false; }
  },
  async onReadScada(nodeId, output) {
    if (state.hmiBusy) return;
    if (!state.scenarioId || state.dirty) {
      setLiveMessage(output, "Guarda los cambios antes de consultar el SCADA.");
      return;
    }
    const isLive = ["running", "partial"].includes(state.status);
    state.hmiBusy = true;
    output.setAttribute("aria-busy", "true");
    if (!output.querySelector(":scope > .hmi-live-grid")) setLiveMessage(output, isLive ? "Consultando adquisición…" : "Cargando historial SCADA…");
    try {
      const base = `/api/scenarios/${state.scenarioId}/scada/${encodeURIComponent(nodeId)}`;
      const historyResponse = await fetch(`${base}/history?limit=100`);
      const history = await historyResponse.json();
      if (!historyResponse.ok) throw new Error(history.detail || `HTTP ${historyResponse.status}`);
      const aggregateResponse = await fetch(`${base}/history/aggregate?bucket_seconds=5`);
      const aggregate = await aggregateResponse.json();
      if (!aggregateResponse.ok) throw new Error(aggregate.detail || `HTTP ${aggregateResponse.status}`);
      const liveData = isLive && history.live;
      let snapshot;
      if (liveData) {
        const snapshotResponse = await fetch(`${base}/snapshot`);
        snapshot = await snapshotResponse.json();
        if (!snapshotResponse.ok) throw new Error(snapshot.detail || `HTTP ${snapshotResponse.status}`);
      } else {
        const last = history.samples?.at(-1);
        snapshot = { values: last?.values || [], alarms: [] };
      }
      const rules = liveData ? inspector.data?.config?.alarms || [] : [];
      output.querySelector(":scope > .hmi-refresh-error")?.remove();
      output.removeAttribute("aria-busy");
      const points = (snapshot.values || []).map((point) => liveData ? point : { ...point, access: "read" });
      const trendFor = (point) => {
        const series = aggregate.series?.find(item => item.node_id === point.node_id && item.address === point.address);
        return scadaTrend(series, history.samples, point, aggregate);
      };
      const alarmHtml = (snapshot.alarms || []).map(alarm => `<article class="hmi-point" data-hmi-live-key="scada-alarm:${escapeHtml(alarm.target_node_id)}:${escapeHtml(alarm.address)}"><div><strong>${alarm.active ? "Alarma activa" : alarm.pending ? "Condición pendiente" : "Normal"}</strong><small>${escapeHtml(alarm.target_node_id)} · ${escapeHtml(alarm.address)} · valor ${escapeHtml(alarm.value ?? "sin dato")}${alarm.pending ? ` · retardo ${escapeHtml(alarm.delay_ms)} ms` : ""}${alarm.stale ? " · último estado conocido, esperando lectura" : ""}</small></div><output>${alarm.stale ? "Lectura pendiente" : alarm.pending ? "Esperando retardo" : alarm.active ? alarm.acknowledged ? "Reconocida" : "Sin reconocer" : "—"}</output>${canWriteScenario() && alarm.active && !alarm.acknowledged && !alarm.stale ? `<button class="tiny-button" data-scada-ack data-target="${escapeHtml(alarm.target_node_id)}" data-address="${escapeHtml(alarm.address)}">Reconocer</button>` : ""}</article>`).join("");
      const exportLink = `<p><a class="button button-secondary" href="${base}/history/export.csv">Descargar histórico CSV</a></p>`;
      const bufferWarning = liveData && Number(snapshot.dropped_samples) > 0
        ? `<p class="hmi-quality" role="status">El buffer durable ha descartado ${escapeHtml(snapshot.dropped_samples)} muestras por alcanzar su capacidad. Revisa el histórico para detectar huecos.</p>`
        : "";
      const gridMarkup = `<div class="hmi-live-grid">${points.map(point => {
        const valueControl = liveData && canWriteScenario() && point.access === "read_write" && point.data_type === "BOOL"
          ? `<label class="hmi-command"><input data-scada-value data-data-type="BOOL" type="checkbox" ${point.value ? "checked" : ""}><span>Activar</span></label>`
          : liveData && canWriteScenario() && point.access === "read_write" && point.data_type === "UINT16"
            ? `<label class="hmi-command"><span class="sr-only">Nuevo valor para ${escapeHtml(point.address)}</span><input data-scada-value data-data-type="UINT16" type="number" min="0" max="65535" step="1" value="${escapeHtml(point.value)}"></label>`
            : liveData && canWriteScenario() && point.access === "read_write" && point.data_type === "REAL"
              ? `<label class="hmi-command"><span class="sr-only">Nueva consigna para ${escapeHtml(point.address)}${point.unit ? ` (${escapeHtml(point.unit)})` : ""}</span><input data-scada-value data-data-type="REAL" type="number" step="any" ${Number.isFinite(point.min_value) ? `min="${point.min_value}"` : ""} ${Number.isFinite(point.max_value) ? `max="${point.max_value}"` : ""} value="${escapeHtml(point.value)}"></label>`
            : "";
        return `<article class="hmi-point" data-hmi-live-key="scada-point:${escapeHtml(point.node_id)}:${escapeHtml(point.address)}" data-scada-target-node="${escapeHtml(point.node_id)}" data-scada-address="${escapeHtml(point.address)}"><div><strong>${escapeHtml(point.name)}</strong><small>${escapeHtml(point.node_id)} · ${escapeHtml(point.address)} · ${escapeHtml(point.quality)}</small>${point.range_diagnostic ? `<small class="hmi-quality" data-quality="out_of_range" role="status">Fuera de rango · ${escapeHtml(point.range_diagnostic.message)}</small>` : ""}${point.data_type === "REAL" || point.data_type === "UINT16" ? trendFor(point) : ""}</div><output>${escapeHtml(point.value ?? "—")}</output>${valueControl ? `${valueControl}<button class="tiny-button" type="button" data-scada-write>Escribir</button>` : '<small class="hmi-readonly">Solo lectura</small>'}</article>`;
      }).join("") || (liveData ? "<p>Esperando la primera lectura.</p>" : "<p>No hay muestras históricas guardadas.</p>")}${alarmHtml ? `<h3 data-hmi-live-key="section:scada-alarms">Alarmas</h3>${alarmHtml}` : liveData && rules.length ? "<p>Reglas configuradas; esperando muestras.</p>" : liveData ? "<p>No hay reglas de alarma configuradas.</p>" : ""}</div>`;
      const staticMarkup = `${exportLink}${bufferWarning}${liveData ? "" : '<p class="hmi-readonly">Historial guardado · sin lectura ni alarmas en vivo.</p>'}`;
      if (!output.querySelector(":scope > .hmi-live-grid")) output.innerHTML = `${staticMarkup}${gridMarkup}`;
      else {
        const existingExport = output.querySelector(":scope > p a[href$='/history/export.csv']");
        if (existingExport) existingExport.href = `${base}/history/export.csv`;
        let warning = output.querySelector(":scope > .hmi-quality[role='status']");
        const nextWarning = new DOMParser().parseFromString(bufferWarning, "text/html").body.firstElementChild;
        if (nextWarning) { if (warning) warning.textContent = nextWarning.textContent; else output.insertBefore(nextWarning, output.querySelector(":scope > .hmi-live-grid")); }
        else warning?.remove();
        updateHmiLiveView(output, gridMarkup);
      }
    } catch (error) {
      setLiveMessage(output, `Error en adquisición SCADA; se conserva la última lectura: ${error.message}`);
    } finally {
      state.hmiBusy = false;
      output.removeAttribute("aria-busy");
    }
  },
  async onAcknowledgeScada(nodeId, alarm, output) {
    try {
      const response = await fetch(`/api/scenarios/${state.scenarioId}/scada/${encodeURIComponent(nodeId)}/alarms/acknowledge`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(alarm),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      await inspector.onReadScada(nodeId, output);
    } catch (error) { setLiveMessage(output, `No se pudo reconocer la alarma: ${error.message}`); }
  },
  async onWriteScada(nodeId, point, output) {
    if (state.hmiBusy) {
      setLiveMessage(output, "Espera a que termine la lectura o escritura actual.");
      return;
    }
    if (!state.scenarioId || state.dirty || !["running", "partial"].includes(state.status)) {
      setLiveMessage(output, "Guarda los cambios y despliega el escenario antes de escribir.");
      return;
    }
    state.hmiBusy = true;
    let wrote = false;
    try {
      const response = await fetch(`/api/scenarios/${state.scenarioId}/scada/${encodeURIComponent(nodeId)}/write`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(point),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      showToast(`SCADA escribió en ${point.target_node_id} · ${point.address}`);
      wrote = true;
    } catch (error) {
      setLiveMessage(output, `Error en escritura SCADA: ${error.message}`);
    } finally {
      state.hmiBusy = false;
    }
    if (wrote) {
      const card = [...output.querySelectorAll("[data-scada-target-node][data-scada-address]")]
        .find((item) => item.dataset.scadaTargetNode === point.target_node_id && item.dataset.scadaAddress === point.address);
      const input = card?.querySelector("input[data-scada-value]");
      if (input) clearHmiInputEdited(input);
      await inspector.onReadScada(nodeId, output);
    }
  },
});

const canvas = new ScenarioCanvas(document.getElementById("drawflow"), {
  onSelect(data) {
    if (data?.object_id) { inspector.render(null); fieldUI.select(data); }
    else { fieldUI.selected = null; inspector.render(data); }
    document.getElementById('heading-config').textContent = data?.object_id ? 'Configura el elemento de campo' : 'Configura el dispositivo';
    elements.devicePicker.value = data?.node_id || data?.object_id || "";
    applyRoleControls();
  },
  onChange() { markDirty(); syncHmiConsole(); },
  suggestIp,
  onError: (message) => showToast(message, "error"),
});

document.querySelector("[data-hmi-console-device]")?.addEventListener("change", () => refreshHmiConsole());
document.querySelector("[data-hmi-console-refresh]")?.addEventListener("click", () => refreshHmiConsole());
document.querySelector("[data-hmi-configure]")?.addEventListener("click", () => {
  const nodeId = document.querySelector("[data-hmi-console-device]")?.value;
  if (nodeId) canvas.selectDevice(nodeId);
  showWorkspace("config", { focus: true });
});
document.addEventListener("click", (event) => {
  const terminalButton = event.target.closest("[data-participant-terminal]");
  if (terminalButton) {
    if (!terminalButton.disabled && state.scenarioId && ["running", "partial"].includes(state.status)) {
      const device = canvas.getDevices().find((item) => item.node_id === terminalButton.dataset.participantTerminal);
      const engineering = device?.device_type === "engineering-station";
      const enabled = engineering ? device.config?.engineering_access_enabled === true
        : device?.device_type === "attacker-station" && device.config?.participant_access_enabled === true;
      if (enabled) {
        participantTerminal.open(state.scenarioId, device.node_id, device.name, engineering ? "engineering" : "participant");
      }
    }
    return;
  }
  const output = document.querySelector("[data-hmi-console-values]");
  if (!output?.contains(event.target)) return;
  const ack = event.target.closest("[data-hmi-ack]");
  const write = event.target.closest("[data-hmi-write]");
  const nodeId = document.querySelector("[data-hmi-console-device]")?.value;
  if (!nodeId) return;
  if (ack) {
    inspector.onAcknowledgeHmi(nodeId, {
      scada_node_id: ack.dataset.scada,
      target_node_id: ack.dataset.target,
      address: ack.dataset.address,
    }, output);
  } else if (write) {
    const row = write.closest("[data-hmi-target-node]");
    const input = row?.querySelector("[data-hmi-value]");
    if (!row || !input) return;
    const value = input.type === "checkbox" ? input.checked : input.value.trim() === "" ? NaN : Number(input.value);
    const validReal = input.dataset.dataType === "REAL" && Number.isFinite(value) && Math.abs(value) <= 3.402823466e38;
    const validRegister = input.dataset.dataType === "UINT16" && Number.isInteger(value) && value >= 0 && value <= 65535;
    if (input.type !== "checkbox" && !validReal && !validRegister) {
      input.setCustomValidity(input.dataset.dataType === "REAL" ? "Introduce un número REAL finito." : "Introduce un entero entre 0 y 65535.");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    inspector.onWriteHmi(nodeId, { target_node_id: row.dataset.hmiTargetNode, address: row.dataset.hmiAddress, value }, output);
  }
});

canvas.element.addEventListener("network-routes-change", (event) => {
  state.networkRoutes = event.detail;
  state.networkRouteEndpoints = [];
  canvas.setNetworkRoutes(state.networkRoutes);
  canvas.setNetworkRouteEndpoints([]);
  markDirty();
});

canvas.element.addEventListener("network-egress-rules-change", (event) => {
  state.egressRules = event.detail;
  canvas.setEgressRules(state.egressRules);
  markDirty();
});

const runtimePanel = new RuntimePanel(document.getElementById("runtime-panel"), {
  onSnapshot: (snapshot) => canvas.processMap.sync(snapshot),
});

const fieldUI = new FieldEditor(canvas, document.getElementById('inspector'), {
  onChange: markDirty, onError: message => showToast(message,'error'),
  getStatus: () => state.status, getScenarioId: () => state.scenarioId, isDirty: () => state.dirty,
  canOperate: canOperateScenario,
  getZones: () => state.zones,
});

setInterval(() => {
  if (document.body.dataset.workspace === "hmi") {
    refreshHmiConsole();
    return;
  }
  const selected = inspector.data;
  const output = inspector.element.querySelector("[data-hmi-values], [data-scada-values]");
  if (selected?.device_type === "hmi" && output && !state.dirty && ["running", "partial"].includes(state.status)) {
    inspector.onReadHmi(selected.node_id, output);
  } else if (selected?.device_type === "scada" && output && !state.dirty && ["running", "partial"].includes(state.status)) {
    inspector.onReadScada(selected.node_id, output);
  }
}, 2000);

const eventStream = new ScenarioEventStream({
  onOpen() { setEventConnection("En vivo", "live"); },
  onEvent(event) {
    if (event.type === "error") {
      addActivity(event.detail || "Error en el canal de eventos", "error");
      showToast(event.detail || "Error en el canal de eventos", "error");
      return;
    }
    if (event.scenario_id !== state.scenarioId) return;
    if (event.type === "heartbeat") {
      setEventConnection("En vivo", "live");
      return;
    }
    const previousStatus = state.status;
    updateStatus(event.status, event.containers);
    const scenario = state.scenarios.find((item) => item.id === state.scenarioId);
    if (scenario) scenario.status = event.status;
    renderScenarioList();
    if (event.type === "snapshot") {
      addActivity(`Estado sincronizado: ${statusLabel(event.status)}`, "info");
    } else if (previousStatus !== event.status) {
      const tone = event.status === "running" ? "success" : event.status === "error" ? "error" : "";
      addActivity(`Estado: ${statusLabel(event.status)}`, tone);
    }
  },
  onReconnect(delay) {
    setEventConnection("Reconectando", "reconnecting");
    if (delay >= 4000) addActivity("Reconectando canal de eventos…", "error");
  },
  onClose() { setEventConnection("Desconectado", "reconnecting"); },
});

const logStream = new ScenarioLogStream({
  onOpen() { runtimePanel.setLogConnection("Logs en vivo", "live"); },
  onEvent(event) {
    if (event.type === "error") {
      runtimePanel.setLogConnection("Error de logs", "error");
      return;
    }
    if (event.scenario_id !== state.scenarioId) return;
    runtimePanel.setLogConnection("Logs en vivo", "live");
    if (event.type !== "heartbeat") runtimePanel.renderLogs(event);
  },
  onReconnect() { runtimePanel.setLogConnection("Reconectando logs", "reconnecting"); },
  onClose() { runtimePanel.setLogConnection("Logs desconectados", "reconnecting"); },
});

async function renderPalette() {
  try { configureNodeCatalog(await scenarioApi.catalog()); }
  catch (error) { showToast(`No se pudo actualizar el catálogo: ${error.message}`, "error"); }
  elements.palette.innerHTML = listNodeTypes().sort((a,b) => Number(b.type.startsWith('field-')) - Number(a.type.startsWith('field-')))
    .map(
      (definition, index, definitions) => `${index === 0 ? '<header class="catalog-group"><h2>Elementos de campo independientes</h2><p>Añade sensores, actuadores, tuberías, distribuidores y colectores al mapa; conecta las señales a un PLC después.</p></header>' : !definition.type.startsWith('field-') && definitions[index - 1].type.startsWith('field-') ? '<header class="catalog-group"><h2>Controladores y dispositivos de red</h2><p>Conecta los elementos de campo a estos dispositivos.</p></header>' : ''}
        <button type="button" class="palette-node" draggable="true" data-node-type="${definition.type}" aria-label="Añadir ${escapeHtml(definition.label)}">
          <span class="palette-icon">${definition.icon}</span>
          <span><strong>${escapeHtml(definition.label)}</strong><small>${escapeHtml(definition.description)}</small></span>
          <span class="drag-handle" aria-hidden="true">＋</span>
        </button>`,
    )
    .join("");
  elements.palette.querySelectorAll("[data-node-type]").forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-anvil-node", item.dataset.nodeType);
      event.dataTransfer.effectAllowed = "copy";
    });
    item.addEventListener("click", () => {
      if (state.busy || state.status !== "stopped") return;
      try {
      const index = canvas.count() + 1;
      const columns = window.innerWidth >= 1200 ? 3 : window.innerWidth >= 900 ? 2 : 1;
      canvas.addNodeType(
        item.dataset.nodeType,
        40 + ((index - 1) % columns) * 290,
        95 + Math.floor((index - 1) / columns) * 190,
        suggestIp(index),
      );
      markDirty();
      showWorkspace("config");
      } catch (error) { showToast(error.message, "error"); }
    });
  });
}

function renderScenarioList() {
  applyRoleControls();
  elements.exportButton.disabled = !state.scenarioId || state.busy;
  elements.cloneButton.disabled = !state.scenarioId || state.busy;
  if (elements.migrateFieldCopyButton) {
    const available = hasLegacyFieldComponents() && canEditScenario();
    elements.migrateFieldCopyButton.disabled = state.busy || !available;
    elements.migrateFieldCopyButton.hidden = !available;
  }
  if (elements.shareButton) {
    const current = state.scenarios.find((scenario) => scenario.id === state.scenarioId);
    const user = window.anvilUser;
    const canManage = user && (current?.access_role === "admin" || current?.access_role === "instructor" || current?.access_role === "owner");
    elements.shareButton.disabled = !state.scenarioId || state.busy || !canManage;
    elements.shareButton.hidden = !canManage;
  }
  if (!state.scenarios.length) {
    elements.list.innerHTML = '<div class="empty-list">Todavía no hay escenarios guardados.</div>';
    return;
  }
  elements.list.innerHTML = state.scenarios
    .map(
      (scenario) => `
        <button class="scenario-item ${scenario.id === state.scenarioId ? "active" : ""}"
          type="button" data-scenario-id="${scenario.id}" ${state.busy ? "disabled" : ""} aria-current="${scenario.id === state.scenarioId ? "true" : "false"}">
          <strong>${escapeHtml(scenario.name)}</strong>
          <span class="mini-status ${scenario.status}" title="${statusLabel(scenario.status)}"></span>
          <small>${scenario.devices.length} dispositivo${scenario.devices.length === 1 ? "" : "s"} · ${statusLabel(scenario.status)}</small>
        </button>`,
    )
    .join("");
}

function hasLegacyFieldComponents() {
  const scenario = state.scenarios.find((item) => item.id === state.scenarioId);
  const legacyTypes = new Set(["valve", "motor", "digital_input", "alarm", "pump", "analog_sensor"]);
  return Boolean(scenario?.devices?.some((device) => device.device_type === "plc-s7"
    && device.config?.components?.some((component) => legacyTypes.has(component.type))));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadTemplateLibrary() {
  const list = document.getElementById("templates-list");
  list.innerHTML = "<p>Cargando plantillas…</p>";
  try {
    const templates = await scenarioApi.templates();
    list.dataset.templates = JSON.stringify(templates);
    list.innerHTML = templates.map((template) => `
      <article class="template-card">
        <div class="template-card-heading"><span class="eyebrow">${escapeHtml(template.category)}</span><span class="template-complexity">${escapeHtml(template.complexity)} · v${escapeHtml(template.revision || 1)}</span></div>
        <h3>${escapeHtml(template.title)}</h3>
        <p>${escapeHtml(template.summary)}</p>
        <div class="template-tags">${(template.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
        ${template.shared && (window.anvilUser?.role === "admin" || template.created_by === window.anvilUser?.id) ? `<button class="button button-secondary" type="button" data-edit-template="${escapeHtml(template.id)}">Editar</button><button class="button button-secondary" type="button" data-delete-template="${escapeHtml(template.id)}">Retirar de biblioteca</button>` : ""}
        ${template.shared ? `<button class="button button-secondary" type="button" data-template-history="${escapeHtml(template.id)}">Historial</button>` : ""}
        <button class="button button-primary" type="button" data-instantiate-template="${escapeHtml(template.id)}">Crear escenario desde plantilla</button>
      </article>`).join("") || "<p>No hay plantillas disponibles.</p>";
  } catch (error) {
    list.innerHTML = `<p role="alert">No se pudo cargar la biblioteca: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadSharedTemplateHistory(templateId) {
  const list = document.getElementById("shared-template-history-list");
  list.dataset.templateId = templateId;
  list.innerHTML = "<p>Cargando versiones…</p>";
  document.getElementById("shared-template-history-dialog").showModal();
  try {
    const versions = await scenarioApi.templateRevisions(templateId);
    const canManage = versions.length > 0 && (window.anvilUser?.role === "admin"
      || versions.some((version) => version.created_by === window.anvilUser?.id));
    list.innerHTML = versions.map((version) => `
      <article class="template-card">
        <div class="template-card-heading"><span class="eyebrow">Versión ${escapeHtml(version.version)}</span><time>${escapeHtml(new Date(version.created_at).toLocaleString())}</time></div>
        <h3>${escapeHtml(version.title)}</h3><p>${escapeHtml(version.summary)}</p>
        <p>${escapeHtml(version.category)} · ${escapeHtml(version.complexity)} · schema ${escapeHtml(version.schema_version)}</p>
        ${canManage ? `<button class="button button-secondary" type="button" data-restore-template-revision="${escapeHtml(version.version)}">Restaurar como nueva versión</button>` : ""}
      </article>`).join("") || "<p>No hay versiones disponibles.</p>";
  } catch (error) {
    list.innerHTML = `<p role="alert">No se pudo cargar el historial: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadComponentLibrary() {
  const list = document.getElementById("component-library-list");
  list.innerHTML = "<p>Cargando componentes…</p>";
  try {
    const presets = await scenarioApi.componentTemplates();
    list.innerHTML = presets.map((preset) => {
      const canAdd = inspector.data?.device_type === preset.device_type && state.status === "stopped" && !state.busy;
      const canManage = window.anvilUser?.role === "admin" || preset.created_by === window.anvilUser?.id;
      return `<article class="template-card">
        <div class="template-card-heading"><span class="eyebrow">${escapeHtml(preset.device_type)} · ${escapeHtml(preset.component_type)}</span><span class="template-complexity">v${escapeHtml(preset.revision || 1)} · schema ${escapeHtml(preset.schema_version)}</span></div>
        <h3>${escapeHtml(preset.title)}</h3><p>${escapeHtml(preset.description)}</p>
        <code>${escapeHtml(JSON.stringify(preset.config))}</code>
        ${canManage ? `<button class="button button-secondary" type="button" data-edit-component-preset="${escapeHtml(preset.id)}">Editar</button><button class="button button-secondary" type="button" data-delete-component-preset="${escapeHtml(preset.id)}">Retirar</button>` : ""}
        <button class="button button-secondary" type="button" data-component-history="${escapeHtml(preset.id)}">Historial</button>
        <button class="button button-primary" type="button" data-add-component-preset="${escapeHtml(preset.id)}" ${canAdd ? "" : "disabled"}>${canAdd ? "Añadir al dispositivo seleccionado" : "Selecciona un dispositivo compatible detenido"}</button>
      </article>`;
    }).join("") || "<p>No hay componentes compartidos todavía.</p>";
    list.dataset.presets = JSON.stringify(presets);
  } catch (error) {
    list.innerHTML = `<p role="alert">No se pudieron cargar los componentes: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadSharedComponentHistory(presetId) {
  const list = document.getElementById("component-preset-history-list");
  list.dataset.presetId = presetId;
  list.innerHTML = "<p>Cargando versiones…</p>";
  document.getElementById("component-preset-history-dialog").showModal();
  try {
    const revisions = await scenarioApi.componentTemplateRevisions(presetId);
    const canManage = revisions.length > 0 && (window.anvilUser?.role === "admin"
      || revisions.some((revision) => revision.created_by === window.anvilUser?.id));
    list.innerHTML = revisions.map((revision) => `
      <article class="template-card">
        <div class="template-card-heading"><span class="eyebrow">Versión ${escapeHtml(revision.version)}</span><time>${escapeHtml(new Date(revision.created_at).toLocaleString())}</time></div>
        <h3>${escapeHtml(revision.title)}</h3><p>${escapeHtml(revision.description)}</p>
        <p>${escapeHtml(revision.device_type)} · ${escapeHtml(revision.component_type)} · schema ${escapeHtml(revision.schema_version)}</p>
        <pre><code>${escapeHtml(JSON.stringify(revision.config, null, 2))}</code></pre>
        ${canManage ? `<button class="button button-secondary" type="button" data-restore-component-preset="${escapeHtml(revision.version)}">Restaurar como nueva versión</button>` : ""}
      </article>`).join("") || "<p>No hay versiones disponibles.</p>";
  } catch (error) {
    list.innerHTML = `<p role="alert">No se pudo cargar el historial: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadScenarioList() {
  state.scenarios = await scenarioApi.list();
  renderScenarioList();
}

async function refreshIoMap(scenarioId) {
  const [ioMap, scenario] = await Promise.all([scenarioApi.ioMap(scenarioId), scenarioApi.get(scenarioId)]);
  if (state.scenarioId === scenarioId) runtimePanel.activateScenario(scenario, ioMap);
}

function resetScenario() {
  fieldUI.selected = null;
  fieldUI.snapshot = null;
  eventStream.disconnect();
  logStream.disconnect();
  state.hmiConsoleGeneration += 1;
  state.hmiConsoleBusy = false;
  state.scenarioId = null;
  state.fieldObjects = [];
  state.signalConnections = [];
  state.networkRoutes = [];
  state.deviceServices = null;
  state.egressRules = [];
  state.networkRouteEndpoints = [];
  canvas.setNetworkConnections([]);
  canvas.setNetworkRoutes([]);
  canvas.setEgressRules([]);
  canvas.setNetworkRouteEndpoints([]);
  state.zones = [];
  renderZones();
  elements.name.value = "Nueva línea OT";
  elements.subnet.value = "172.30.100.0/24";
  canvas.clear();
  state.hmiConsoleNodeId = "";
  syncHmiConsole();
  updateDevicePicker();
  inspector.render(null);
  runtimePanel.reset();
  updateStatus("stopped");
  setEventConnection("Sin escenario");
  clearActivity();
  markClean();
  renderScenarioList();
  setBusy(false);
  showWorkspace("editor");
}

async function openScenario(scenarioId, { confirmed = false } = {}) {
  if (state.busy) return false;
  if (!confirmed && state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos?")) return;
  setBusy(true);
  try {
    const [scenario, ioMap, routeEndpoints, deviceServices] = await Promise.all([
      scenarioApi.get(scenarioId),
      scenarioApi.ioMap(scenarioId),
      scenarioApi.networkRoutes(scenarioId),
      scenarioApi.services(scenarioId),
    ]);
    eventStream.disconnect();
    logStream.disconnect();
    state.scenarioId = scenario.id;
    state.hmiConsoleGeneration += 1;
    state.hmiConsoleBusy = false;
    document.querySelector("[data-hmi-console-values]")?.replaceChildren();
    fieldUI.selected = null;
    fieldUI.snapshot = null;
    state.fieldObjects = scenario.field_objects || [];
    state.signalConnections = scenario.signal_connections || [];
    state.networkRoutes = scenario.network_routes || [];
    state.deviceServices = deviceServices || [];
    state.egressRules = scenario.network?.egress_rules || [];
    state.networkRouteEndpoints = routeEndpoints || [];
    canvas.setNetworkRoutes(state.networkRoutes);
    canvas.setEgressRules(state.egressRules);
    canvas.setNetworkRouteEndpoints(state.networkRouteEndpoints);
    canvas.setNetworkConnections(scenario.network_connections || []);
    elements.name.value = scenario.name;
    elements.subnet.value = scenario.network.subnet;
    state.zones = scenario.network.zones || [];
    renderZones();
    canvas.loadTopology(scenario.canvas, scenario.devices, scenario.field_objects || [], scenario.signal_connections || [], scenario.process_connections || []);
    updateDevicePicker();
    syncHmiConsole();
    inspector.render(null);
    runtimePanel.activateScenario(scenario, ioMap);
    runtimePanel.renderLogs({ containers: [] });
    updateStatus(scenario.status);
    clearActivity("Conectando con el escenario…");
    setEventConnection("Conectando", "reconnecting");
    runtimePanel.setLogConnection("Conectando logs", "reconnecting");
    eventStream.connect(scenario.id);
    logStream.connect(scenario.id);
    markClean();
    renderScenarioList();
    showWorkspace("editor");
    requestAnimationFrame(() => canvas.processMap.fit());
    return true;
  } catch (error) {
    showToast(error.message, "error");
    return false;
  } finally {
    setBusy(false);
  }
}

function buildPayload() {
  const name = elements.name.value.trim();
  const subnet = elements.subnet.value.trim();
  const devices = canvas.getDevices();
  if (!name) throw new Error("El escenario necesita un nombre");
  if (!subnet) throw new Error("Indica la subred del escenario");
  if (!devices.length && !canvas.getFieldObjects().length) throw new Error("Añade al menos un dispositivo o elemento de campo al lienzo");
  const nodeIds = new Set();
  const addresses = new Set();
  devices.forEach((device) => {
    if (!device.name.trim()) throw new Error("Todos los dispositivos necesitan un nombre");
    if (!device.ip.trim()) throw new Error(`El dispositivo '${device.name}' necesita una IP`);
    const items = device.device_type === "modbus-tcp" ? device.config.points : device.config.components;
    if (device.device_type === 'modbus-tcp' && !items?.length) throw new Error(`Añade al menos un punto a '${device.name}'`);
    if (device.device_type === "modbus-tcp") {
      const seen = new Set();
      for (const point of items) {
        if (!point.name?.trim()) throw new Error(`Todos los puntos de '${device.name}' necesitan nombre`);
        if (point.address === null || point.address === "") continue;
        const key = `${point.type}:${point.address}`;
        if (seen.has(key)) throw new Error(`Dirección Modbus duplicada: ${key}`);
        seen.add(key);
      }
    }
    if (nodeIds.has(device.node_id)) throw new Error(`Node ID duplicado: ${device.node_id}`);
    if (addresses.has(device.ip)) throw new Error(`Dirección IP duplicada: ${device.ip}`);
    nodeIds.add(device.node_id);
    addresses.add(device.ip);
  });
  const zones = state.zones;
  const usedIps = new Set();
  for (const device of devices) {
    const previousIp = device.ip;
    const previousZone = device.zone_id;
    const zone = zones.find(item => item.zone_id === device.zone_id) || zones[0];
    device.zone_id = zone?.zone_id || "default";
    if (zone && (!ipInSubnet(device.ip, zone.subnet) || usedIps.has(device.ip))) {
      device.ip = nextAvailableIp(zone.subnet, [...usedIps]);
    }
    usedIps.add(device.ip);
    if (device.ip !== previousIp || device.zone_id !== previousZone) {
      canvas.updateDevice(device);
      if (inspector.data?.node_id === device.node_id) inspector.render(device);
    }
  }
  const fields = canvas.getFieldObjects().map(field => ({ ...field, zone_id: zones.length ? (zones.some(zone => zone.zone_id === field.zone_id) ? field.zone_id : zones[0].zone_id) : "default" }));
  const requiredRouteTargets = devices.filter(item => ["scada", "hmi", "plc-s7"].includes(item.device_type))
    .flatMap(source => {
      const targets = source.device_type === "plc-s7"
        ? (source.config.components || []).filter(component => component.type === "remote_input").map(component => component.source_node_id)
        : (source.config.target_node_ids || "").split(",").map(value => value.trim()).filter(Boolean);
      return targets
        .filter(targetId => devices.find(target => target.node_id === targetId)?.zone_id !== source.zone_id)
        .map(targetId => {
          const target = devices.find(device => device.node_id === targetId);
          return { source_node_id: source.node_id, target_node_id: targetId,
            protocol: source.device_type === "plc-s7" || target?.device_type === "plc-s7" ? "s7" : "modbus-tcp" };
        });
    });
  const network_routes = state.networkRoutes.filter(route => route.protocol === "tcp"
    ? devices.some(device => device.node_id === route.source_node_id && device.zone_id !== devices.find(target => target.node_id === route.target_node_id)?.zone_id)
      && devices.some(device => device.node_id === route.target_node_id)
    : requiredRouteTargets.some(required => required.source_node_id === route.source_node_id
      && required.target_node_id === route.target_node_id && required.protocol === route.protocol));
  for (const required of requiredRouteTargets) {
    if (!network_routes.some(route => route.source_node_id === required.source_node_id
        && route.target_node_id === required.target_node_id && route.protocol === required.protocol)) {
      throw new Error("Configura la ruta TCP entre zonas desde el inspector o quita el objetivo remoto.");
    }
  }
  return { name, network: { subnet, zones, egress_rules: state.egressRules }, devices, canvas: canvas.exportTopology(), field_objects: fields, signal_connections: canvas.signalConnections, process_connections: canvas.processConnections, network_routes, network_connections: canvas.networkConnections };
}

elements.addZone?.addEventListener("click", () => {
  if (state.status !== "stopped") return;
  const index = state.zones.length + 1;
  state.zones.push({ zone_id: `zone-${index}`, name: `Zona ${index}`, purdue_level: "L1", subnet: `172.30.${Math.min(10 + index, 240)}.0/24` });
  renderZones();
  markDirty();
});
elements.zoneList?.addEventListener("input", (event) => {
  const input = event.target.closest("[data-zone-key]");
  if (!input || state.status !== "stopped") return;
  const row = input.closest("[data-zone-index]");
  state.zones[Number(row.dataset.zoneIndex)][input.dataset.zoneKey] = input.value;
  markDirty();
});
elements.zoneList?.addEventListener("change", (event) => {
  const input = event.target.closest("[data-zone-key]");
  if (!input || state.status !== "stopped") return;
  const row = input.closest("[data-zone-index]");
  state.zones[Number(row.dataset.zoneIndex)][input.dataset.zoneKey] = input.value;
  markDirty();
});
elements.zoneList?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-zone]");
  if (!button || state.status !== "stopped") return;
  state.zones.splice(Number(button.dataset.removeZone), 1);
  renderZones();
  markDirty();
});

async function saveScenario({ silent = false } = {}) {
  if (state.status !== "stopped") throw new Error("Detén el escenario antes de modificarlo");
  for (const input of document.querySelectorAll("#inspector input, #inspector select")) {
    if (!input.reportValidity()) throw new Error("Revisa el campo señalado antes de guardar.");
  }
  const payload = buildPayload();
  const saved = state.scenarioId
    ? await scenarioApi.update(state.scenarioId, payload)
    : await scenarioApi.create(payload);
  state.scenarioId = saved.id;
  updateStatus(saved.status);
  markClean();
  const [routeEndpoints, deviceServices] = await Promise.all([
    scenarioApi.networkRoutes(saved.id),
    scenarioApi.services(saved.id),
  ]);
  state.deviceServices = deviceServices || [];
  if (inspector.data?.node_id) {
    const updatedDevice = canvas.getDevices().find(item => item.node_id === inspector.data.node_id);
    if (updatedDevice) inspector.render(updatedDevice);
  }
  state.networkRouteEndpoints = routeEndpoints;
  canvas.setNetworkRouteEndpoints(state.networkRouteEndpoints);
  await loadScenarioList();
  eventStream.connect(saved.id);
  logStream.connect(saved.id);
  await refreshIoMap(saved.id);
  if (!silent) {
    addActivity("Escenario guardado", "success");
    showToast("Escenario guardado");
  }
  return saved;
}

async function deployScenario() {
  setBusy(true);
  try {
    const saved = await saveScenario({ silent: true });
    const report = await scenarioApi.diagnostics(saved.id);
    if (report.errors.length) {
      const details = report.errors.map((item) => `${item.path ? `${item.path}: ` : ""}${item.message}`).join("\n");
      throw new Error(`El escenario no supera la validación:\n${details}`);
    }
    if (report.warnings.length) {
      const details = report.warnings.slice(0, 12).map((item) => `• ${item.node_id ? `${item.node_id}: ` : ""}${item.message}`).join("\n");
      const remainder = report.warnings.length > 12 ? `\n…y ${report.warnings.length - 12} aviso(s) más.` : "";
      if (!confirm(`Revisión previa: ${report.warnings.length} aviso(s).\n\n${details}${remainder}\n\n¿Desplegar de todas formas?`)) {
        showToast("Despliegue cancelado; corrige los avisos o vuelve a desplegar.");
        return;
      }
    }
    updateStatus("deploying");
    addActivity("Orden de despliegue enviada", "info");
    const runtime = await scenarioApi.deploy(saved.id);
    updateStatus(runtime.status, runtime.containers);
    await loadScenarioList();
    const elapsed = runtime.deployment_duration_seconds;
    if (Number.isFinite(elapsed)) {
      const duration = elapsed < 60
        ? `${elapsed.toFixed(1)} s`
        : `${Math.floor(elapsed / 60)} min ${Math.round(elapsed % 60)} s`;
      addActivity(`Despliegue completado en ${duration}`, "success");
      showToast(`Escenario desplegado en ${duration}`);
    } else {
      showToast("Escenario desplegado correctamente");
    }
    try {
      const runtimeDiagnostics = await scenarioApi.diagnostics(saved.id);
      const checkedRoutes = runtimeDiagnostics.runtime?.routes_checked || 0;
      for (const route of runtimeDiagnostics.runtime?.routes || []) {
        if (route.protocol !== "field-binding") continue;
        for (const binding of route.bindings || []) {
          const status = binding.status === "good"
            ? `correcto${Number.isFinite(binding.age_ms) ? ` · ${binding.age_ms} ms` : ""}`
            : binding.status || "sin estado";
          addActivity(
            `Enlace ${binding.address}: ${binding.field_id}.${binding.field_port} · ${status}`,
            binding.status === "good" ? "success" : "error",
          );
        }
      }
      if (checkedRoutes) {
        const routeWarnings = runtimeDiagnostics.warnings.filter((item) => item.code === "runtime_route_unreachable");
        if (routeWarnings.length) {
          for (const item of routeWarnings.slice(0, 4)) {
            addActivity(`Sonda de conectividad: ${item.node_id ? `${item.node_id}: ` : ""}${item.message}`, "error");
          }
          const remainder = routeWarnings.length > 4 ? ` (${routeWarnings.length - 4} más)` : "";
          showToast(`${routeWarnings.length} ruta(s) de red no respondieron${remainder}. Revisa actividad.`, "error");
        } else {
          addActivity(`Sondas de conectividad correctas: ${checkedRoutes} ruta(s)`, "success");
        }
      }
    } catch (diagnosticError) {
      addActivity(`No se pudo comprobar la conectividad en ejecución: ${diagnosticError.message}`, "info");
    }
  } catch (error) {
    showToast(error.message, "error");
    addActivity(error.message, "error");
    if (state.scenarioId) {
      const runtime = await scenarioApi.status(state.scenarioId).catch(() => null);
      if (runtime) updateStatus(runtime.status, runtime.containers);
    }
  } finally {
    setBusy(false);
  }
}

async function stopScenario() {
  if (!state.scenarioId) return;
  setBusy(true);
  try {
    addActivity("Orden de parada enviada", "info");
    const runtime = await scenarioApi.stop(state.scenarioId);
    updateStatus(runtime.status, runtime.containers);
    await loadScenarioList();
    showToast("Escenario detenido y recursos liberados");
  } catch (error) {
    showToast(error.message, "error");
    addActivity(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function restartScenario() {
  if (!state.scenarioId || !confirm("Se reiniciarán todos los dispositivos. El estado volátil del proceso puede volver a sus valores iniciales. ¿Continuar?")) return;
  setBusy(true);
  try {
    addActivity("Reinicio del escenario solicitado", "info");
    const runtime = await scenarioApi.restart(state.scenarioId);
    updateStatus(runtime.status, runtime.containers);
    await loadScenarioList();
    addActivity("Escenario reiniciado", "success");
    showToast("Escenario reiniciado");
  } catch (error) {
    const runtime = await scenarioApi.status(state.scenarioId).catch(() => null);
    if (runtime) updateStatus(runtime.status, runtime.containers);
    addActivity(`No se pudo reiniciar el escenario: ${error.message}`, "error");
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function setScenarioPaused(paused) {
  if (!state.scenarioId) return;
  setBusy(true);
  try {
    const runtime = paused
      ? await scenarioApi.pause(state.scenarioId)
      : await scenarioApi.resume(state.scenarioId);
    updateStatus(runtime.status, runtime.containers);
    await loadScenarioList();
    const verb = paused ? "pausado" : "reanudado";
    addActivity(`Escenario ${verb}`, "success");
    showToast(`Escenario ${verb}`);
  } catch (error) {
    addActivity(error.message, "error");
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function removeScenario() {
  if (!state.scenarioId || !confirm("¿Eliminar este escenario de forma permanente?")) return;
  setBusy(true);
  try {
    await scenarioApi.remove(state.scenarioId);
    eventStream.disconnect();
    logStream.disconnect();
    await loadScenarioList();
    resetScenario();
    showToast("Escenario eliminado");
    showWorkspace("projects");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function exportScenario() {
  if (!state.scenarioId) return;
  try {
    const payload = await scenarioApi.exportScenario(state.scenarioId);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${payload.scenario.name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "anvil-scenario"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showToast(error.message, "error"); }
}

async function exportDeviceProfile(nodeId) {
  if (!state.scenarioId) return;
  try {
    const profile = await scenarioApi.exportDeviceProfile(state.scenarioId, nodeId);
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${profile.device_type}-${nodeId}-profile.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showToast(`No se pudo exportar el perfil: ${error.message}`, "error"); }
}

async function importDeviceProfileFromUi(profile, sourceNodeId) {
  if (!state.scenarioId || !canEditScenario()) return showToast("No tienes permiso para editar este escenario.", "error");
  if (state.status !== "stopped") return showToast("Detén el escenario antes de importar un perfil.", "error");
  if (state.dirty) return showToast("Guarda los cambios del escenario antes de importar el perfil.", "error");
  const source = canvas.getDevices().find((item) => item.node_id === sourceNodeId);
  const zoneId = source?.zone_id || "default";
  const defaultId = `${profile?.device_type || "device"}-copy`;
  const nodeId = prompt("Identificador del dispositivo nuevo", defaultId);
  if (!nodeId) return;
  const zone = state.zones.find((item) => item.zone_id === zoneId);
  const subnet = zone?.subnet || elements.subnet.value;
  let defaultIp;
  try {
    defaultIp = nextAvailableIp(subnet, canvas.getDevices().map((item) => item.ip));
  } catch (error) { return showToast(error.message, "error"); }
  const ip = prompt(`IP para ${nodeId} en ${zone?.name || "la red del escenario"}`, defaultIp);
  if (!ip) return;
  setBusy(true);
  try {
    const updated = await scenarioApi.importDeviceProfile(state.scenarioId, {
      profile, node_id: nodeId.trim(), ip: ip.trim(), zone_id: zoneId,
    });
    await loadScenarioList();
    setBusy(false);
    await openScenario(updated.id, { confirmed: true });
    showToast(`Perfil importado como ${nodeId}`);
  } catch (error) { showToast(`No se pudo importar el perfil: ${error.message}`, "error"); }
  finally { setBusy(false); }
}

async function exportDeploymentBundle() {
  if (!state.scenarioId || state.busy) return;
  setBusy(true);
  try {
    const response = await fetch(`/api/scenarios/${state.scenarioId}/deployment-bundle`);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Error HTTP ${response.status}`);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `anvil-${state.scenarioId}-deployment.zip`;
    link.click();
    URL.revokeObjectURL(url);
    showToast("Paquete Docker descargado");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function importScenario(file) {
  try {
    if (state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos antes de importar?")) return;
    const payload = JSON.parse(await file.text());
    setBusy(true);
    const created = await scenarioApi.importScenario(payload);
    await loadScenarioList();
    setBusy(false);
    await openScenario(created.id, { confirmed: true });
    showToast("Escenario importado");
  } catch (error) { showToast(`No se pudo importar: ${error.message}`, "error"); }
  finally { elements.importFile.value = ""; setBusy(false); }
}

async function cloneScenario() {
  if (!state.scenarioId || state.busy) return;
  if (state.dirty && !confirm("Se clonará la última versión guardada. ¿Continuar y abrir la copia?")) return;
  setBusy(true);
  try {
    const cloned = await scenarioApi.clone(state.scenarioId);
    await loadScenarioList();
    setBusy(false);
    await openScenario(cloned.id, { confirmed: true });
    showToast("Escenario clonado");
  } catch (error) { showToast(error.message, "error"); }
  finally { setBusy(false); }
}

async function migrateLegacyFieldsToCopy() {
  if (!state.scenarioId || state.busy || !hasLegacyFieldComponents()) return;
  if (!confirm("Se creará una copia detenida. El PLC conservará sus direcciones S7 y reglas; el escenario original no cambiará. ¿Continuar?")) return;
  setBusy(true);
  try {
    const copied = await scenarioApi.migrateLegacyFieldCopy(state.scenarioId);
    await loadScenarioList();
    setBusy(false);
    await openScenario(copied.id, { confirmed: true });
    showToast("Copia migrada a elementos de campo independientes");
  } catch (error) { showToast(`No se pudo migrar: ${error.message}`, "error"); }
  finally { setBusy(false); }
}

function bindActions() {
  document.getElementById("inspector").addEventListener("anvil-profile-error", (event) => {
    showToast(`El archivo no contiene JSON válido: ${event.detail}`, "error");
  });
  document.getElementById("inspector").addEventListener("anvil-node-lifecycle", async (event) => {
    if (!state.scenarioId || state.busy) return;
    const { nodeId, action } = event.detail;
    setBusy(true);
    try {
      const runtime = await scenarioApi.nodeLifecycle(state.scenarioId, nodeId, action);
      updateStatus(runtime.status, runtime.containers);
      const labels = { start: "iniciado", stop: "detenido", restart: "reiniciado", pause: "pausado", resume: "reanudado", recreate: "reconstruido" };
      addActivity(`${nodeId}: ${labels[action] || action}`, "success");
      showToast(`Nodo ${labels[action] || action}`);
    } catch (error) {
      addActivity(error.message, "error");
      showToast(error.message, "error");
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("drawflow").addEventListener("open-field-catalog", () => showWorkspace("catalog", { focus: true }));
  document.getElementById("drawflow").addEventListener("configure-component", (event) => {
    const selected = canvas.exportNodes()[canvas.selectedId]?.data;
    if (!selected || selected.object_id) return;
    inspector.render(selected, event.detail.index);
    showWorkspace("config", { focus: true });
    const card = document.querySelector("#inspector .component-card");
    card?.scrollIntoView({ block: "center" });
    card?.querySelector("input, select")?.focus();
  });
  document.getElementById("drawflow").addEventListener("dblclick", (event) => {
    const node = event.target.closest(".drawflow-node");
    if (node) { canvas.selectNode(node.id.slice(5)); showWorkspace("config", { focus: true }); }
  });
  const help = document.getElementById("help-dialog");
  document.getElementById("help-button").addEventListener("click", () => help.showModal());
  const historyDialog = document.getElementById("history-dialog");
  const historyList = document.getElementById("history-list");
  document.getElementById("history-button").addEventListener("click", async () => {
    if (!state.scenarioId || state.busy) return;
    historyDialog.showModal();
    historyList.innerHTML = "<p>Cargando historial…</p>";
    try {
      const revisions = await scenarioApi.revisions(state.scenarioId);
      historyList.innerHTML = revisions.map((revision) => `
        <article class="template-card">
          <div class="template-card-heading"><span class="eyebrow">Versión ${escapeHtml(revision.version)}</span><time>${escapeHtml(new Date(revision.created_at).toLocaleString())}</time></div>
          <h3>${escapeHtml(revision.name)}</h3>
          <p>${escapeHtml(revision.device_count)} dispositivos · ${escapeHtml(revision.field_object_count)} elementos de campo</p>
          <button class="button button-secondary" type="button" data-restore-revision="${escapeHtml(revision.id)}" ${state.status !== "stopped" ? "disabled" : ""}>Restaurar esta versión</button>
        </article>`).join("") || "<p>Este escenario aún no tiene versiones guardadas.</p>";
    } catch (error) {
      historyList.innerHTML = `<p role="alert">No se pudo cargar el historial: ${escapeHtml(error.message)}</p>`;
    }
  });
  historyList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-restore-revision]");
    if (!button || !state.scenarioId || state.busy || state.status !== "stopped") return;
    if (state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos?")) return;
    if (!confirm("¿Restaurar esta versión? El estado actual se conservará en el historial como una nueva versión.")) return;
    const scenarioId = state.scenarioId;
    setBusy(true);
    button.disabled = true;
    try {
      const restored = await scenarioApi.restoreRevision(scenarioId, button.dataset.restoreRevision);
      historyDialog.close();
      await loadScenarioList();
      setBusy(false);
      await openScenario(restored.id, { confirmed: true });
      showToast(`Restaurada la versión anterior. Se guardó como versión nueva.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(false);
      button.disabled = false;
    }
  });
  const templateDialog = document.getElementById("templates-dialog");
  const publishTemplateForm = document.getElementById("publish-template-form");
  if (["admin", "instructor"].includes(window.anvilUser?.role)) publishTemplateForm.hidden = false;
  document.querySelectorAll("#templates-button, [data-open-templates]").forEach((button) => button.addEventListener("click", () => {
    if (state.busy || state.status !== "stopped") return;
    publishTemplateForm.hidden = !["admin", "instructor"].includes(window.anvilUser?.role);
    templateDialog.showModal();
    loadTemplateLibrary();
  }));
  document.getElementById("templates-list").addEventListener("click", async (event) => {
    const editTemplate = event.target.closest("[data-edit-template]");
    if (editTemplate) {
      const templates = JSON.parse(event.currentTarget.dataset.templates || "[]");
      const template = templates.find((item) => item.id === editTemplate.dataset.editTemplate);
      if (!template) return;
      const form = document.getElementById("edit-shared-template-form");
      form.elements.template_id.value = template.id;
      form.elements.expected_revision.value = template.revision || 1;
      form.elements.title.value = template.title;
      form.elements.category.value = template.category;
      form.elements.summary.value = template.summary;
      form.elements.complexity.value = template.complexity;
      form.elements.tags.value = (template.tags || []).join(", ");
      form.elements.scenario_id.innerHTML = `<option value="">Conservar contenido actual</option>${state.scenarios.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
      document.getElementById("edit-shared-template-error").hidden = true;
      document.getElementById("edit-shared-template-dialog").showModal();
      return;
    }
    const historyButton = event.target.closest("[data-template-history]");
    if (historyButton) {
      await loadSharedTemplateHistory(historyButton.dataset.templateHistory);
      return;
    }
    const removeTemplate = event.target.closest("[data-delete-template]");
    if (removeTemplate) {
      if (!confirm("¿Retirar esta plantilla compartida? Los escenarios ya creados desde ella no se modificarán.")) return;
      removeTemplate.disabled = true;
      try {
        await scenarioApi.deleteTemplate(removeTemplate.dataset.deleteTemplate);
        await loadTemplateLibrary();
        showToast("Plantilla retirada de la biblioteca.");
      } catch (error) { showToast(error.message, "error"); removeTemplate.disabled = false; }
      return;
    }
    const button = event.target.closest("[data-instantiate-template]");
    if (!button || state.busy || state.status !== "stopped") return;
    if (state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos y crear el escenario desde esta plantilla?")) return;
    setBusy(true);
    button.disabled = true;
    try {
      const scenario = await scenarioApi.instantiateTemplate(button.dataset.instantiateTemplate);
      templateDialog.close();
      await loadScenarioList();
      setBusy(false);
      const opened = await openScenario(scenario.id, { confirmed: true });
      if (opened) showToast(`Escenario «${scenario.name}» creado desde plantilla.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(false);
      button.disabled = false;
    }
  });
  const editSharedTemplateForm = document.getElementById("edit-shared-template-form");
  editSharedTemplateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const error = document.getElementById("edit-shared-template-error");
    const button = form.querySelector('[type="submit"]');
    error.hidden = true;
    button.disabled = true;
    try {
      const payload = {
        expected_revision: Number(form.elements.expected_revision.value),
        title: form.elements.title.value, category: form.elements.category.value,
        summary: form.elements.summary.value, complexity: form.elements.complexity.value,
        tags: form.elements.tags.value.split(",").map((tag) => tag.trim()).filter(Boolean),
      };
      if (form.elements.scenario_id.value) {
        if (form.elements.scenario_id.value === state.scenarioId && state.dirty) await saveScenario({ silent: true });
        payload.scenario_id = form.elements.scenario_id.value;
      }
      await scenarioApi.updateTemplate(form.elements.template_id.value, payload);
      document.getElementById("edit-shared-template-dialog").close();
      await loadTemplateLibrary();
      showToast("Plantilla actualizada; se guardó una nueva versión.");
    } catch (errorCause) {
      error.textContent = errorCause.message;
      error.hidden = false;
    } finally { button.disabled = false; }
  });
  document.getElementById("shared-template-history-list").addEventListener("click", async (event) => {
    const restore = event.target.closest("[data-restore-template-revision]");
    if (!restore) return;
    if (!confirm(`¿Restaurar la versión ${restore.dataset.restoreTemplateRevision}? Se guardará como una versión nueva.`)) return;
    restore.disabled = true;
    try {
      await scenarioApi.restoreTemplateRevision(document.getElementById("shared-template-history-list").dataset.templateId, restore.dataset.restoreTemplateRevision);
      await loadTemplateLibrary();
      await loadSharedTemplateHistory(document.getElementById("shared-template-history-list").dataset.templateId);
      showToast("Versión restaurada como una nueva revisión.");
    } catch (error) { restore.disabled = false; showToast(error.message, "error"); }
  });
  const componentDialog = document.getElementById("component-library-dialog");
  const componentForm = document.getElementById("publish-component-form");
  componentForm.hidden = !["admin", "instructor"].includes(window.anvilUser?.role);
  document.getElementById("component-library-button").addEventListener("click", () => {
    componentForm.hidden = !["admin", "instructor"].includes(window.anvilUser?.role);
    componentDialog.showModal();
    loadComponentLibrary();
  });
  document.getElementById("component-library-list").addEventListener("click", async (event) => {
    const editPreset = event.target.closest("[data-edit-component-preset]");
    if (editPreset) {
      const presets = JSON.parse(event.currentTarget.dataset.presets || "[]");
      const preset = presets.find((item) => item.id === editPreset.dataset.editComponentPreset);
      if (!preset) return;
      const form = document.getElementById("edit-component-preset-form");
      form.elements.preset_id.value = preset.id;
      form.elements.expected_revision.value = preset.revision || 1;
      form.elements.title.value = preset.title;
      form.elements.description.value = preset.description;
      form.elements.config.value = JSON.stringify(preset.config, null, 2);
      document.getElementById("edit-component-preset-error").hidden = true;
      document.getElementById("edit-component-preset-dialog").showModal();
      return;
    }
    const historyButton = event.target.closest("[data-component-history]");
    if (historyButton) {
      await loadSharedComponentHistory(historyButton.dataset.componentHistory);
      return;
    }
    const remove = event.target.closest("[data-delete-component-preset]");
    if (remove) {
      if (!confirm("¿Retirar este preset compartido? Los escenarios guardados no cambiarán.")) return;
      remove.disabled = true;
      try {
        await scenarioApi.deleteComponentTemplate(remove.dataset.deleteComponentPreset);
        await loadComponentLibrary();
        showToast("Preset retirado de la biblioteca.");
      } catch (error) { remove.disabled = false; showToast(error.message, "error"); }
      return;
    }
    const add = event.target.closest("[data-add-component-preset]");
    if (!add || state.status !== "stopped" || state.busy) return;
    try {
      const presets = JSON.parse(event.currentTarget.dataset.presets || "[]");
      const preset = presets.find((item) => item.id === add.dataset.addComponentPreset);
      if (!preset) throw new Error("El preset ya no está disponible; recarga la biblioteca.");
      inspector.addSharedComponent(preset);
      componentDialog.close();
      showToast(`Componente «${preset.title}» añadido al dispositivo.`);
    } catch (error) { showToast(error.message, "error"); }
  });
  const editComponentPresetForm = document.getElementById("edit-component-preset-form");
  editComponentPresetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const error = document.getElementById("edit-component-preset-error");
    const button = form.querySelector('[type="submit"]');
    error.hidden = true;
    button.disabled = true;
    try {
      await scenarioApi.updateComponentTemplate(form.elements.preset_id.value, {
        expected_revision: Number(form.elements.expected_revision.value),
        title: form.elements.title.value, description: form.elements.description.value,
        config: JSON.parse(form.elements.config.value),
      });
      document.getElementById("edit-component-preset-dialog").close();
      await loadComponentLibrary();
      showToast("Preset actualizado; se guardó una nueva versión.");
    } catch (cause) {
      error.textContent = cause instanceof SyntaxError ? "La configuración debe ser JSON válido." : cause.message;
      error.hidden = false;
    } finally { button.disabled = false; }
  });
  document.getElementById("component-preset-history-list").addEventListener("click", async (event) => {
    const restore = event.target.closest("[data-restore-component-preset]");
    if (!restore) return;
    if (!confirm(`¿Restaurar la versión ${restore.dataset.restoreComponentPreset}? Se guardará como una nueva versión.`)) return;
    restore.disabled = true;
    const presetId = document.getElementById("component-preset-history-list").dataset.presetId;
    try {
      await scenarioApi.restoreComponentTemplateRevision(presetId, restore.dataset.restoreComponentPreset);
      await loadComponentLibrary();
      await loadSharedComponentHistory(presetId);
      showToast("Versión restaurada como una nueva revisión.");
    } catch (error) { restore.disabled = false; showToast(error.message, "error"); }
  });
  componentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = componentForm.querySelector('[type="submit"]');
    const error = document.getElementById("publish-component-error");
    error.hidden = true;
    button.disabled = true;
    try {
      await scenarioApi.publishComponentTemplate({
        title: componentForm.elements.title.value,
        description: componentForm.elements.description.value,
        device_type: componentForm.elements.device_type.value,
        component_type: componentForm.elements.component_type.value,
        config: JSON.parse(componentForm.elements.config.value),
      });
      componentForm.reset();
      await loadComponentLibrary();
      showToast("Preset compartido con los usuarios de Anvil.");
    } catch (errorCause) {
      error.textContent = errorCause.message;
      error.hidden = false;
    } finally { button.disabled = false; }
  });
  publishTemplateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("publish-template-error");
    error.hidden = true;
    if (!state.scenarioId) { error.textContent = "Guarda primero el escenario actual."; error.hidden = false; return; }
    const button = publishTemplateForm.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(publishTemplateForm));
      if (state.dirty) await saveScenario({ silent: true });
      await scenarioApi.publishTemplate({
        scenario_id: state.scenarioId, title: values.title, category: values.category,
        summary: values.summary, complexity: values.complexity,
        tags: values.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      });
      publishTemplateForm.reset();
      publishTemplateForm.querySelector('[name="complexity"]').value = "Intermedia";
      await loadTemplateLibrary();
      showToast("Plantilla publicada para los usuarios de Anvil.");
    } catch (errorCause) {
      error.textContent = errorCause.message;
      error.hidden = false;
    } finally { button.disabled = false; }
  });
  elements.devicePicker.addEventListener("change", () => canvas.selectDevice(elements.devicePicker.value));
  window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
  elements.name.addEventListener("input", markDirty);
  elements.subnet.addEventListener("input", markDirty);
  elements.list.addEventListener("click", (event) => {
    const item = event.target.closest("[data-scenario-id]");
    if (item) openScenario(item.dataset.scenarioId);
  });
  elements.create.addEventListener("click", () => {
    if (state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos?")) return;
    resetScenario();
  });
  elements.save.addEventListener("click", async () => {
    setBusy(true);
    try { await saveScenario(); }
    catch (error) { showToast(error.message, "error"); }
    finally { setBusy(false); }
  });
  elements.deploy.addEventListener("click", deployScenario);
  elements.restart.addEventListener("click", restartScenario);
  elements.stop.addEventListener("click", stopScenario);
  elements.pause.addEventListener("click", () => setScenarioPaused(true));
  elements.resume.addEventListener("click", () => setScenarioPaused(false));
  elements.remove.addEventListener("click", removeScenario);
  elements.importButton.addEventListener("click", () => elements.importFile.click());
  elements.importFile.addEventListener("change", () => {
    const [file] = elements.importFile.files || [];
    if (file) importScenario(file);
  });
  elements.exportButton.addEventListener("click", exportScenario);
  elements.exportDeploymentButton.addEventListener("click", exportDeploymentBundle);
  elements.cloneButton.addEventListener("click", cloneScenario);
  elements.migrateFieldCopyButton?.addEventListener("click", migrateLegacyFieldsToCopy);
  if (elements.shareButton) elements.shareButton.addEventListener("click", openScenarioAccess);
}

async function openScenarioAccess() {
  if (!state.scenarioId || state.busy) return;
  const dialog = document.getElementById("scenario-access-dialog");
  const form = document.getElementById("scenario-access-form");
  const error = document.getElementById("scenario-access-error");
  error.hidden = true;
  form.elements.email.value = "";
  await renderScenarioMembers();
  dialog.showModal();
}

async function renderScenarioMembers() {
  const list = document.getElementById("scenario-access-list");
  list.textContent = "Cargando…";
  try {
    const members = await scenarioApi.members(state.scenarioId);
    list.replaceChildren();
    if (!members.length) {
      const empty = document.createElement("p");
      empty.textContent = "Aún no se ha compartido con otros usuarios.";
      list.append(empty);
      return;
    }
    for (const member of members) {
      const row = document.createElement("article");
      row.className = "scenario-member-row";
      const identity = document.createElement("span");
      identity.textContent = member.email;
      const role = document.createElement("span");
      role.className = "scenario-member-role";
      role.textContent = member.role;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "tiny-button";
      remove.textContent = "Quitar acceso";
      remove.setAttribute("aria-label", `Quitar acceso a ${member.email}`);
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        try {
          await scenarioApi.removeMember(state.scenarioId, member.user_id);
          await renderScenarioMembers();
          showToast(`Acceso retirado a ${member.email}.`);
        } catch (cause) {
          showToast(cause.message, "error");
          remove.disabled = false;
        }
      });
      row.append(identity, role, remove);
      list.append(row);
    }
  } catch (cause) {
    list.textContent = `No se pudo cargar el acceso: ${cause.message}`;
  }
}

document.getElementById("scenario-access-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.scenarioId) return;
  const form = event.currentTarget;
  const error = document.getElementById("scenario-access-error");
  const button = form.querySelector('button[type="submit"]');
  error.hidden = true;
  button.disabled = true;
  try {
    await scenarioApi.grantMember(state.scenarioId, form.elements.email.value, form.elements.role.value);
    form.reset();
    await renderScenarioMembers();
    showToast("Acceso al escenario actualizado.");
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

async function initialize() {
  bindActions();
  resetScenario();
  showWorkspace("projects");
  // The scenario list must load even if catalog metadata is unavailable.
  const results = await Promise.allSettled([
    renderPalette(),
    scenarioApi.health(),
    loadScenarioList(),
  ]);
  const healthy = results[1].status === "fulfilled";
  elements.apiIndicator.textContent = healthy ? "API conectada" : "API sin conexión";
  elements.apiIndicator.classList.add(healthy ? "online" : "offline");
  if (results[2].status === "rejected") {
    elements.list.replaceChildren();
    const notice = document.createElement("p");
    notice.textContent = `No se pudieron cargar los escenarios: ${results[2].reason.message}`;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button button-secondary";
    retry.textContent = "Reintentar carga";
    retry.addEventListener("click", () => loadScenarioList().catch((error) => showToast(error.message, "error")));
    elements.list.append(notice, retry);
  }
  for (const result of results) if (result.status === "rejected") showToast(result.reason.message, "error");
}

initialize().catch((error) => {
  window.dispatchEvent(new CustomEvent("anvil-startup-error", { detail: error }));
});
