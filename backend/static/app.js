import { scenarioApi } from "./api.js";
import { ScenarioCanvas } from "./canvas.js";
import { ScenarioEventStream } from "./events.js";
import { Inspector } from "./inspector.js";
import { listNodeTypes } from "./nodes/registry.js";

import "./nodes/plc-s7.js";
import "./components/valve.js";
import "./components/analog_sensor.js";


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
  stop: document.getElementById("stop-button"),
  create: document.getElementById("new-scenario-button"),
  remove: document.getElementById("delete-scenario-button"),
  toast: document.getElementById("toast"),
};

const state = {
  scenarioId: null,
  status: "stopped",
  dirty: false,
  busy: false,
  scenarios: [],
  activity: [],
};

let toastTimer;

function showToast(message, type = "success") {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast visible ${type === "error" ? "error" : ""}`;
  toastTimer = setTimeout(() => {
    elements.toast.classList.remove("visible");
  }, 3600);
}

function setBusy(isBusy) {
  state.busy = isBusy;
  elements.save.disabled = isBusy || state.status !== "stopped";
  elements.deploy.disabled = isBusy || state.status !== "stopped";
  elements.stop.disabled = isBusy || !["running", "partial", "error"].includes(state.status);
  elements.create.disabled = isBusy;
  elements.remove.disabled = isBusy || !state.scenarioId || state.status !== "stopped";
}

function statusLabel(status) {
  return {
    stopped: "Detenido",
    deploying: "Desplegando",
    running: "En ejecución",
    partial: "Parcial",
    error: "Error",
  }[status] || status;
}

function updateStatus(status, containers = []) {
  state.status = status;
  elements.status.className = `status-pill status-${status}`;
  elements.status.querySelector("span:last-child").textContent = statusLabel(status);
  canvas.syncRuntimeStatus(containers);
  setBusy(state.busy);
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
          <time>${item.time}</time>
          <span>${escapeHtml(item.message)}</span>
        </div>
      `,
    )
    .join("");
}

function clearActivity(message = "Selecciona un escenario para recibir eventos.") {
  state.activity = [];
  elements.eventLog.innerHTML = `<div class="event-log-empty">${escapeHtml(message)}</div>`;
}

function markDirty() {
  state.dirty = true;
  elements.save.textContent = "Guardar •";
}

function markClean() {
  state.dirty = false;
  elements.save.textContent = "Guardar";
}

function suggestIp(index) {
  const [address, prefixValue] = elements.subnet.value.trim().split("/");
  const octets = address?.split(".").map(Number);
  const prefix = Number(prefixValue);
  if (
    octets?.length !== 4 ||
    octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255) ||
    !Number.isInteger(prefix) ||
    prefix < 0 ||
    prefix > 30
  ) {
    return `172.30.100.${index + 9}`;
  }

  const raw = octets.reduce((total, part) => total * 256 + part, 0);
  const hostCount = 2 ** (32 - prefix);
  const network = Math.floor(raw / hostCount) * hostCount;
  const candidate = network + Math.min(index + 9, hostCount - 2);
  return [24, 16, 8, 0].map((shift) => Math.floor(candidate / 2 ** shift) % 256).join(".");
}

const inspector = new Inspector(document.getElementById("inspector"), {
  onChange(data) {
    canvas.updateSelected(data);
    markDirty();
  },
  onRemove() {
    canvas.removeSelected();
    markDirty();
  },
});

const canvas = new ScenarioCanvas(document.getElementById("drawflow"), {
  onSelect(data) {
    inspector.render(data);
  },
  onChange() {
    markDirty();
  },
});

const eventStream = new ScenarioEventStream({
  onOpen() {
    setEventConnection("En vivo", "live");
  },
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
  onClose() {
    setEventConnection("Desconectado", "reconnecting");
  },
});

function renderPalette() {
  elements.palette.innerHTML = listNodeTypes()
    .map(
      (definition) => `
        <div class="palette-node" draggable="true" data-node-type="${definition.type}" tabindex="0">
          <span class="palette-icon">${definition.icon}</span>
          <span>
            <strong>${definition.label}</strong>
            <small>${definition.description}</small>
          </span>
          <span class="drag-handle" aria-hidden="true">⠿</span>
        </div>
      `,
    )
    .join("");

  elements.palette.querySelectorAll("[data-node-type]").forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-anvil-node", item.dataset.nodeType);
      event.dataTransfer.effectAllowed = "copy";
    });
    item.addEventListener("dblclick", () => {
      const index = canvas.count() + 1;
      canvas.addNodeType(
        item.dataset.nodeType,
        120 + ((index - 1) % 3) * 40,
        130 + ((index - 1) % 3) * 40,
        suggestIp(index),
      );
      markDirty();
    });
  });
}

function renderScenarioList() {
  if (!state.scenarios.length) {
    elements.list.innerHTML = '<div class="empty-list">Todavía no hay escenarios guardados.</div>';
    return;
  }

  elements.list.innerHTML = state.scenarios
    .map(
      (scenario) => `
        <button
          class="scenario-item ${scenario.id === state.scenarioId ? "active" : ""}"
          type="button"
          data-scenario-id="${scenario.id}"
        >
          <strong>${escapeHtml(scenario.name)}</strong>
          <span class="mini-status ${scenario.status}" title="${statusLabel(scenario.status)}"></span>
          <small>${scenario.devices.length} dispositivo${scenario.devices.length === 1 ? "" : "s"}</small>
        </button>
      `,
    )
    .join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadScenarioList() {
  state.scenarios = await scenarioApi.list();
  renderScenarioList();
}

function resetScenario() {
  eventStream.disconnect();
  state.scenarioId = null;
  elements.name.value = "Nueva línea OT";
  elements.subnet.value = "172.30.100.0/24";
  canvas.clear();
  inspector.render(null);
  updateStatus("stopped");
  setEventConnection("Sin escenario");
  clearActivity();
  markClean();
  renderScenarioList();
  setBusy(false);
}

async function openScenario(scenarioId) {
  if (state.dirty && !confirm("Hay cambios sin guardar. ¿Quieres descartarlos?")) return;
  setBusy(true);
  try {
    const scenario = await scenarioApi.get(scenarioId);
    eventStream.disconnect();
    state.scenarioId = scenario.id;
    elements.name.value = scenario.name;
    elements.subnet.value = scenario.network.subnet;
    canvas.load(scenario.devices);
    inspector.render(null);
    updateStatus(scenario.status);
    clearActivity("Conectando con el escenario…");
    setEventConnection("Conectando", "reconnecting");
    eventStream.connect(scenario.id);
    markClean();
    renderScenarioList();
  } catch (error) {
    showToast(error.message, "error");
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
  if (!devices.length) throw new Error("Añade al menos un dispositivo al lienzo");

  const nodeIds = new Set();
  const addresses = new Set();
  devices.forEach((device) => {
    if (!device.name.trim()) throw new Error("Todos los dispositivos necesitan un nombre");
    if (!device.ip.trim()) throw new Error(`El dispositivo '${device.name}' necesita una IP`);
    if (!device.config.components.length) {
      throw new Error(`Añade al menos un componente a '${device.name}'`);
    }
    if (nodeIds.has(device.node_id)) throw new Error(`Node ID duplicado: ${device.node_id}`);
    if (addresses.has(device.ip)) throw new Error(`Dirección IP duplicada: ${device.ip}`);
    nodeIds.add(device.node_id);
    addresses.add(device.ip);
  });

  return { name, network: { subnet }, devices };
}

async function saveScenario({ silent = false } = {}) {
  if (state.status !== "stopped") {
    throw new Error("Detén el escenario antes de modificarlo");
  }
  const payload = buildPayload();
  const saved = state.scenarioId
    ? await scenarioApi.update(state.scenarioId, payload)
    : await scenarioApi.create(payload);
  state.scenarioId = saved.id;
  updateStatus(saved.status);
  markClean();
  await loadScenarioList();
  eventStream.connect(saved.id);
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
    updateStatus("deploying");
    addActivity("Orden de despliegue enviada", "info");
    const runtime = await scenarioApi.deploy(saved.id);
    updateStatus(runtime.status, runtime.containers);
    await loadScenarioList();
    showToast("Escenario desplegado correctamente");
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

async function removeScenario() {
  if (!state.scenarioId || !confirm("¿Eliminar este escenario de forma permanente?")) return;
  setBusy(true);
  try {
    await scenarioApi.remove(state.scenarioId);
    eventStream.disconnect();
    await loadScenarioList();
    resetScenario();
    showToast("Escenario eliminado");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function bindActions() {
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
    try {
      await saveScenario();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(false);
    }
  });
  elements.deploy.addEventListener("click", deployScenario);
  elements.stop.addEventListener("click", stopScenario);
  elements.remove.addEventListener("click", removeScenario);
}

async function initialize() {
  renderPalette();
  bindActions();
  resetScenario();

  try {
    await scenarioApi.health();
    elements.apiIndicator.textContent = "API conectada";
    elements.apiIndicator.classList.add("online");
    await loadScenarioList();
  } catch (error) {
    elements.apiIndicator.textContent = "API sin conexión";
    elements.apiIndicator.classList.add("offline");
    showToast(error.message, "error");
  }
}

initialize();
