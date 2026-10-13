import { ExerciseEditor } from "./exercise-editor.js?v=rf38-fc23-write-20261013";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export class RuntimePanel {
  constructor(root, { onSnapshot = () => {} } = {}) {
    this.onSnapshot = onSnapshot;
    this.root = root;
    this.ioMap = root.querySelector("#io-map");
    this.logs = root.querySelector("#runtime-logs");
    this.protocol = root.querySelector("#runtime-protocol");
    this.history = root.querySelector("#signal-history");
    this.audit = root.querySelector("#runtime-audit");
    this.captureNode = root.querySelector("[data-capture-node]");
    this.captureZone = root.querySelector("[data-capture-zone]");
    this.captureLinkSource = root.querySelector("[data-capture-link-source]");
    this.captureLinkTarget = root.querySelector("[data-capture-link-target]");
    this.captureScope = root.querySelector("[data-capture-scope]");
    this.captureStatus = root.querySelector("[data-capture-status]");
    this.evidenceCaptureCheckbox = root.querySelector("[data-evidence-include-capture]");
    this.evidenceExportButton = root.querySelector("[data-evidence-export]");
    this.evidenceExportBusy = false;
    this.exerciseDefinition = root.querySelector("[data-exercise-definition]");
    this.exerciseAdvanced = root.querySelector(".exercise-advanced");
    this.exerciseAdvancedDirty = false;
    this.exerciseEditor = new ExerciseEditor(root.querySelector("[data-exercise-builder]"));
    this.exerciseStatus = root.querySelector("[data-exercise-status]");
    this.exerciseResults = root.querySelector("[data-exercise-results]");
    this.exerciseRun = null;
    this.exercisePollTimer = null;
    this.connection = root.querySelector("#log-connection");
    this.valueStatus = root.querySelector("#value-status");
    this.deviceFilter = root.querySelector("[data-telemetry-device]");
    this.deviceFilter?.addEventListener("change", () => this.renderIoMap());
    this.scenarioId = null;
    this.generation = 0;
    this.running = false;
    this.errors = new Map();
    this.writing = new Set();
    this.drafts = new Map();
    this.points = [];
    this.devices = new Map();
    this.values = new Map();
    this.changes = [];
    this.protocolEvents = [];
    this.protocolKeys = new Set();
    this.auditEvents = [];
    this.auditCursor = null;
    this.captureId = null;
    this.captureGeneration = 0;
    this.capabilities = { capture: true, exercise: true, auditExport: true, evidenceExport: false };
    this.pollTimer = null;
    this.refreshing = false;
    root.querySelectorAll("[data-runtime-tab]").forEach((button) => {
      button.setAttribute("role", "tab");
      button.id = `tab-${button.dataset.runtimeTab}`;
      const view = root.querySelector(`[data-runtime-view="${button.dataset.runtimeTab}"]`);
      button.setAttribute("aria-controls", view.id);
      view.setAttribute("role", "tabpanel");
      view.setAttribute("aria-labelledby", button.id);
      view.tabIndex = 0;
      button.addEventListener("click", () => this.selectTab(button.dataset.runtimeTab));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...root.querySelectorAll("[data-runtime-tab]:not([hidden])")];
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (tabs.indexOf(button) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        this.selectTab(tabs[next].dataset.runtimeTab);
        tabs[next].focus();
      });
    });
    root.addEventListener("click", (event) => this.onPanelClick(event));
    root.querySelector("[data-exercise-create]")?.addEventListener("click", () => this.createExercise());
    root.querySelector("[data-exercise-start]")?.addEventListener("click", () => this.startExercise());
    root.querySelector("[data-exercise-cancel]")?.addEventListener("click", () => this.cancelExercise());
    this.evidenceCaptureCheckbox?.addEventListener("change", () => this.renderCaptureControls());
    this.captureScope?.addEventListener("change", () => { this.renderCaptureTarget(); this.renderCaptureControls(); });
    this.captureLinkSource?.addEventListener("change", () => { this.renderCaptureLinkPeers(); this.renderCaptureControls(); });
    this.captureLinkTarget?.addEventListener("change", () => this.renderCaptureControls());
    this.exerciseAdvanced?.addEventListener("toggle", () => {
      if (this.exerciseAdvanced.open && !this.exerciseAdvancedDirty && this.exerciseDefinition) {
        this.exerciseDefinition.value = JSON.stringify(this.exerciseEditor.toDefinition(), null, 2);
      }
    });
    this.exerciseDefinition?.addEventListener("input", () => { this.exerciseAdvancedDirty = true; });
    root.addEventListener("input", (event) => {
      if (event.target.matches("[data-register-value]")) this.drafts.set(event.target.dataset.registerValue, event.target.value);
    });
    this.setCapabilities(this.capabilities);
    this.reset();
  }

  setCapabilities(capabilities = {}) {
    this.capabilities = { ...this.capabilities, ...capabilities };
    const captureTab = this.root.querySelector('[data-runtime-tab="capture"]');
    if (captureTab) captureTab.hidden = !this.capabilities.capture;
    const captureView = this.root.querySelector('[data-runtime-view="capture"]');
    if (captureView) captureView.hidden = !this.capabilities.capture;
    for (const selector of ['[data-capture-start]', '[data-capture-export]']) {
      const button = this.root.querySelector(selector);
      if (button) button.hidden = !this.capabilities.capture;
    }
    const includeCaptureLabel = this.evidenceCaptureCheckbox?.closest("label");
    if (includeCaptureLabel) includeCaptureLabel.hidden = !this.capabilities.evidenceExport;
    if (this.evidenceExportButton) this.evidenceExportButton.hidden = !this.capabilities.evidenceExport;
    for (const selector of ['[data-exercise-builder]', '.exercise-advanced', '[data-exercise-create]', '[data-exercise-start]', '[data-exercise-cancel]']) {
      const control = this.root.querySelector(selector);
      if (control) control.hidden = !this.capabilities.exercise;
    }
    const exerciseHelp = this.root.querySelector('#runtime-exercise > .capture-explainer');
    if (exerciseHelp) exerciseHelp.textContent = this.capabilities.exercise
      ? 'Define una cronología JSON con escrituras, incidentes de proceso o acciones de ciclo de vida. Los objetivos pueden exigir que una señal se mantenga durante un tiempo.'
      : 'Consulta el estado y los resultados de los ejercicios de este escenario.';
    this.renderIoMap();
    if (this.audit) this.renderAudit();
  }

  activateScenario(scenario, ioMap) {
    this.reset();
    this.scenarioId = scenario.id;
    this.points = ioMap.points || [];
    this.fieldObjects = scenario.field_objects || [];
    this.devices = new Map(scenario.devices.map((device) => [device.node_id, device.device_type]));
    this.deviceNames = new Map(scenario.devices.map((device) => [device.node_id, device.name]));
    for (const field of scenario.field_objects || []) this.deviceNames.set(field.object_id, field.name);
    this.exerciseEditor.setContext({ points: this.points, devices: scenario.devices, fields: this.fieldObjects, routes: scenario.network_routes || [] });
    if (this.deviceFilter) {
      this.deviceFilter.innerHTML = '<option value="">Todos los dispositivos</option>' + [...new Set(this.points.map((point) => point.node_id))].map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(this.deviceNames.get(id) || id)}</option>`).join("");
    }
    if (this.captureNode) {
      const captureDevices = scenario.devices.filter((device) => device.device_type !== "attacker-station");
      this.captureDevices = captureDevices;
      const options = captureDevices.map((device) => `<option value="${escapeHtml(device.node_id)}">${escapeHtml(device.name)}</option>`).join("");
      this.captureNode.innerHTML = options;
      if (this.captureLinkSource) this.captureLinkSource.innerHTML = options;
      this.renderCaptureLinkPeers();
    }
    if (this.captureZone) {
      const zones = scenario.network.zones?.length ? scenario.network.zones : [{ zone_id: "default", name: "Red del escenario" }];
      this.captureZone.innerHTML = zones.map((zone) => `<option value="${escapeHtml(zone.zone_id)}">${escapeHtml(zone.name)}</option>`).join("");
    }
    this.renderCaptureTarget();
    this.captureId = null;
    this.captureGeneration += 1;
    this.renderCaptureControls();
    this.loadExercises();
    this.renderIoMap();
    this.setRunning(["running", "partial", "error"].includes(scenario.status), scenario.status === "paused");
    if (this.running) this.loadProtocolSnapshot();
  }

  setRunning(running, paused = false) {
    if (this.running && !running) {
      this.generation += 1;
      this.refreshing = false;
      this.values.clear();
    }
    this.running = running;
    if (!running) {
      if (!paused) {
        this.captureId = null;
        this.captureGeneration += 1;
      }
      if (this.captureStatus) this.captureStatus.textContent = !this.scenarioId
        ? "Despliega el escenario y selecciona un dispositivo."
        : paused ? "Escenario en pausa." : "Escenario detenido.";
    } else if (this.captureId && this.captureStatus) {
      this.captureStatus.textContent = "Captura en curso.";
    }
    this.renderIoMap();
    this.renderCaptureControls();
    this.renderExerciseControls();
    this.syncPolling();
    if (!running) this.setConnection(!this.scenarioId
      ? "Guarda un escenario para ver sus señales."
      : paused ? "Escenario en pausa · reanuda para operar." : "Escenario detenido · despliega para leer y escribir.");
  }

  selectTab(tab) {
    if (this.root.querySelector(`[data-runtime-tab="${tab}"]`)?.hidden) tab = "map";
    if (this.deviceFilter) this.deviceFilter.closest("label").hidden = tab !== "map";
    this.root.querySelectorAll("[data-runtime-tab]").forEach((button) => {
      const selected = button.dataset.runtimeTab === tab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    this.root.querySelectorAll("[data-runtime-view]").forEach((view) => {
      view.hidden = view.dataset.runtimeView !== tab;
    });
    if (tab === "audit") this.loadAudit();
    if (tab === "exercise") this.loadExercises();
  }

  setConnection(label, mode = "") {
    this.valueStatus.textContent = label;
    this.valueStatus.className = `value-status ${mode}`;
  }

  setLogConnection(label, mode = "") {
    this.connection.textContent = label;
    this.connection.className = `runtime-connection ${mode}`.trim();
  }

  syncPolling() {
    if (!this.scenarioId || !this.running) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
      return;
    }
    if (this.pollTimer) return;
    this.refreshValues();
    this.pollTimer = setInterval(() => this.refreshValues(), 2000);
  }

  async refreshValues(onlyNodeId = null) {
    if (!this.scenarioId || !this.running || this.refreshing) return;
    this.refreshing = true;
    const scenarioId = this.scenarioId;
    const generation = this.generation;
    const nodes = [...new Set(this.points.map((point) => point.node_id))].filter((id) => !onlyNodeId || id === onlyNodeId);
    try {
      const results = await Promise.allSettled(nodes.map(async (nodeId) => {
        const kind = this.devices.get(nodeId) === "plc-s7" ? "plcs" : "modbus";
        const response = await fetch(`/api/scenarios/${scenarioId}/${kind}/${encodeURIComponent(nodeId)}/values`, { signal: AbortSignal.timeout(30000) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        return result;
      }));
      if (generation !== this.generation || scenarioId !== this.scenarioId) return;
      results.forEach((result, index) => {
        const nodeId = nodes[index];
        if (result.status === "rejected") {
          this.errors.set(nodeId, result.reason.message);
          this.points.filter((point) => point.node_id === nodeId).forEach((point) => this.values.delete(`${nodeId}:${point.address}`));
          return;
        }
        this.errors.delete(nodeId);
        result.value.values.forEach((item) => {
          const key = `${item.node_id}:${item.address}`;
          const previous = this.values.get(key);
          if (previous && previous.value !== item.value) this.pushChange({
            timestamp: new Date().toISOString(), node_id: item.node_id, address: item.address,
            operation: "observed", previous: previous.value, value: item.value, raw_hex: item.raw_hex,
          });
          this.values.set(key, item);
        });
      });
      this.renderIoMap();
      this.setConnection(this.errors.size ? `No se pueden leer ${this.errors.size} dispositivos. Consulta el detalle de cada señal.` : `Valores actualizados · ${new Date().toLocaleTimeString("es-ES")}`, this.errors.size ? "error" : "live");
    } finally {
      if (generation === this.generation) this.refreshing = false;
    }
  }

  renderIoMap() {
    this.onSnapshot({ points: this.points, values: this.values, errors: this.errors, running: this.running });
    if (!this.points.length) {
      this.ioMap.innerHTML = '<div class="runtime-empty">Añade un PLC S7 o Modbus y guarda para calcular su mapa de señales.</div>';
      delete this.ioMap.dataset.signature;
      return;
    }
    const signature = JSON.stringify([this.generation, this.scenarioId, this.points, this.capabilities.write]);
    if (this.ioMap.dataset.signature !== signature) {
      this.ioMap.dataset.signature = signature;
      this.ioMap.innerHTML = this.points.map((point) => {
        const key = `${point.node_id}:${point.address}`;
        return `<article class="io-point">
          <div class="io-point-address">${escapeHtml(point.address)}</div>
          <div class="io-point-copy"><strong>${escapeHtml(point.component_name)}</strong><span>${escapeHtml(point.device_name)} · ${escapeHtml(point.data_type)} ${escapeHtml(point.unit || "")}</span></div>
          <div class="io-point-value"><span data-current-value>—</span><small data-raw-value></small></div>
          <span class="io-access ${point.access}">${point.access === "read_write" && this.capabilities.write ? "Lectura / escritura" : "Solo lectura"}</span>
          <p class="point-error" data-point-error hidden></p>
          ${point.source_node_id ? `<p class="point-error" data-link-status>Entrada remota: ${escapeHtml(this.deviceNames?.get(point.source_node_id) || point.source_node_id)} · ${escapeHtml(point.source_address)}</p>` : ""}
          <div class="io-controls">
            <button class="io-action secondary" data-read-node="${escapeHtml(point.node_id)}" aria-label="Leer ${escapeHtml(point.device_name)}" type="button">Leer</button>
            ${point.access === "read_write" && this.capabilities.write ? `${point.data_type !== "BOOL" ? `<label class="register-editor">Nuevo valor<input type="number" min="0" max="65535" step="1" required data-register-value="${escapeHtml(key)}" aria-label="Nuevo valor de ${escapeHtml(point.component_name)} en ${escapeHtml(point.device_name)}" /></label>` : ""}
              <button class="io-action" data-write-address="${escapeHtml(point.address)}" data-node-id="${escapeHtml(point.node_id)}" type="button">Escribir</button>` : ""}
          </div>
        </article>`;
      }).join("");
    }
    [...this.ioMap.querySelectorAll(".io-point")].forEach((row, index) => {
      const point = this.points[index];
      row.hidden = !!this.deviceFilter?.value && point.node_id !== this.deviceFilter.value;
      const key = `${point.node_id}:${point.address}`;
      const current = this.values.get(key);
      row.querySelector("[data-current-value]").textContent = current && current.communication?.quality !== "waiting" ? (typeof current.value === "boolean" ? (current.value ? "ON" : "OFF") : String(current.value)) : "—";
      row.querySelector("[data-raw-value]").textContent = current?.raw_hex || "";
      const error = row.querySelector("[data-point-error]");
      error.hidden = !this.errors.has(point.node_id);
      error.textContent = this.errors.get(point.node_id) || "";
      const link = row.querySelector("[data-link-status]");
      if (link) {
        const communication = current?.communication;
        link.dataset.quality = communication?.quality || "waiting";
        const label = { good: "Dato vigente", stale: "Dato obsoleto · se conserva el último valor", waiting: "Esperando la primera lectura" }[communication?.quality] || "Sin lectura del enlace";
        link.textContent = `${this.deviceNames?.get(point.source_node_id) || point.source_node_id} · ${point.source_address} → ${label}${communication?.error ? ` · ${communication.error}` : ""}`;
      }
      row.querySelector("[data-read-node]").disabled = !this.running;
      const button = row.querySelector("[data-write-address]");
      if (button) {
        button.disabled = !this.running || !current || this.writing.has(key);
        button.textContent = this.writing.has(key) ? "Escribiendo…" : point.data_type === "BOOL" ? (current?.value ? "Apagar" : "Encender") : "Escribir";
        button.setAttribute("aria-label", `${button.textContent} ${point.component_name} en ${point.device_name}`);
      }
      const input = row.querySelector("[data-register-value]");
      if (input) {
        input.disabled = !this.running || !current || this.writing.has(key);
        if (document.activeElement !== input) input.value = this.drafts.get(key) ?? current?.value ?? "";
      }
    });
  }

  async onPanelClick(event) {
    if (event.target.closest("[data-capture-start]")) { await this.startPacketCapture(); return; }
    if (event.target.closest("[data-capture-export]")) { await this.exportPacketCapture(); return; }
    if (event.target.closest("[data-evidence-export]")) { await this.exportEvidenceBundle(); return; }
    if (event.target.closest("[data-audit-refresh]")) { await this.loadAudit(); return; }
    if (event.target.closest("[data-audit-more]")) { await this.loadAudit({ append: true }); return; }
    const readButton = event.target.closest("[data-read-node]");
    if (readButton) { await this.refreshValues(readButton.dataset.readNode); return; }
    const button = event.target.closest("[data-write-address]");
    if (!button || !this.scenarioId || !this.running || button.disabled) return;
    const scenarioId = this.scenarioId;
    const generation = this.generation;
    const nodeId = button.dataset.nodeId;
    const address = button.dataset.writeAddress;
    const key = `${nodeId}:${address}`;
    const point = this.points.find((item) => item.node_id === nodeId && item.address === address);
    const input = button.closest(".io-point").querySelector("[data-register-value]");
    if (input && !input.reportValidity()) return;
    const value = point.data_type === "BOOL" ? !this.values.get(key).value : Number(input.value);
    this.writing.add(key);
    this.renderIoMap();
    try {
      const kind = this.devices.get(nodeId) === "plc-s7" ? "plcs" : "modbus";
      const response = await fetch(`/api/scenarios/${scenarioId}/${kind}/${encodeURIComponent(nodeId)}/write`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address, value }), signal: AbortSignal.timeout(30000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      if (generation !== this.generation || scenarioId !== this.scenarioId) return;
      const previous = this.values.get(key)?.value;
      this.values.set(key, result);
      this.drafts.delete(key);
      this.pushChange({ timestamp: new Date().toISOString(), node_id: nodeId, address, operation: "write", previous, value: result.value, raw_hex: result.raw_hex });
      this.setConnection(`Escritura confirmada: ${point.component_name} = ${result.value}`, "live");
      if (kind === "plcs") await this.loadProtocolSnapshot();
    } catch (error) {
      if (generation === this.generation) this.setConnection(error.message, "error");
    } finally {
      if (generation === this.generation) { this.writing.delete(key); this.renderIoMap(); }
    }
  }

  renderCaptureControls() {
    const start = this.root.querySelector("[data-capture-start]");
    const exportButton = this.root.querySelector("[data-capture-export]");
    const scope = this.captureScope?.value || "device";
    const target = scope === "zone" ? this.captureZone?.value : scope === "link"
      ? this.captureLinkSource?.value && this.captureLinkTarget?.value && this.captureLinkSource.value !== this.captureLinkTarget.value
      : this.captureNode?.value;
    if (start) start.disabled = !this.running || !target || !!this.captureId;
    if (exportButton) exportButton.disabled = !this.running || !this.captureId;
    if (this.evidenceCaptureCheckbox) this.evidenceCaptureCheckbox.disabled = !this.captureId || !this.capabilities.evidenceExport || this.evidenceExportBusy;
    if (this.evidenceExportButton) this.evidenceExportButton.disabled = !this.scenarioId || !this.capabilities.evidenceExport || this.evidenceExportBusy || (!!this.evidenceCaptureCheckbox?.checked && !this.captureId);
  }

  renderCaptureTarget() {
    const scope = this.captureScope?.value || "device";
    const zoneMode = scope === "zone";
    const linkMode = scope === "link";
    const deviceWrap = this.root.querySelector("[data-capture-device-wrap]");
    const zoneWrap = this.root.querySelector("[data-capture-zone-wrap]");
    if (deviceWrap) deviceWrap.hidden = zoneMode || linkMode;
    if (zoneWrap) zoneWrap.hidden = !zoneMode;
    this.root.querySelectorAll("[data-capture-link-wrap]").forEach((wrap) => { wrap.hidden = !linkMode; });
  }

  renderCaptureLinkPeers() {
    if (!this.captureLinkTarget) return;
    const source = this.captureDevices?.find((device) => device.node_id === this.captureLinkSource?.value);
    const zoneId = source?.zone_id || "default";
    const peers = (this.captureDevices || []).filter((device) => device.node_id !== source?.node_id
      && (device.zone_id || "default") === zoneId);
    const previous = this.captureLinkTarget.value;
    this.captureLinkTarget.innerHTML = peers.map((device) => `<option value="${escapeHtml(device.node_id)}">${escapeHtml(device.name)}</option>`).join("");
    if (peers.some((device) => device.node_id === previous)) this.captureLinkTarget.value = previous;
  }

  async exportEvidenceBundle() {
    if (!this.scenarioId || !this.capabilities.evidenceExport || this.evidenceExportBusy) return;
    const scenarioId = this.scenarioId;
    const generation = this.captureGeneration;
    const captureId = this.evidenceCaptureCheckbox?.checked ? this.captureId : null;
    if (this.evidenceCaptureCheckbox?.checked && !captureId) return;
    const params = new URLSearchParams();
    if (captureId) params.set("capture_id", captureId);
    const query = params.size ? `?${params}` : "";
    this.evidenceExportBusy = true;
    this.renderCaptureControls();
    if (this.captureStatus) this.captureStatus.textContent = captureId
      ? "Preparando ZIP. La captura seleccionada se detendrá y consumirá…"
      : "Preparando ZIP de evidencias…";
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/evidence-bundle.zip${query}`, { signal: AbortSignal.timeout(120000) });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || `HTTP ${response.status}`);
      }
      const manifestHash = response.headers.get("X-Anvil-Manifest-SHA256");
      if (!manifestHash || !/^[a-f0-9]{64}$/.test(manifestHash)) {
        throw new Error("La respuesta no incluye un hash verificable del manifiesto");
      }
      const blob = await response.blob();
      if (generation !== this.captureGeneration || scenarioId !== this.scenarioId) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `anvil-evidence-${scenarioId}.zip`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (captureId) {
        this.captureId = null;
        if (this.evidenceCaptureCheckbox) this.evidenceCaptureCheckbox.checked = false;
      }
      if (this.captureStatus) this.captureStatus.textContent = captureId
        ? `ZIP descargado · ${(blob.size / 1024).toFixed(1)} KiB. Hash SHA-256 del manifiesto (guárdalo aparte): ${manifestHash}. La captura se consumió y su contenedor se eliminó.`
        : `ZIP descargado · ${(blob.size / 1024).toFixed(1)} KiB. Hash SHA-256 del manifiesto (guárdalo aparte): ${manifestHash}.`;
    } catch (error) {
      if (generation === this.captureGeneration && this.captureStatus) this.captureStatus.textContent = `No se pudo descargar el ZIP: ${error.message}`;
    } finally {
      if (generation === this.captureGeneration) {
        this.evidenceExportBusy = false;
        this.renderCaptureControls();
      }
    }
  }

  async startPacketCapture() {
    const scope = this.captureScope?.value || "device";
    const zoneMode = scope === "zone";
    const linkMode = scope === "link";
    const target = zoneMode ? this.captureZone?.value : linkMode
      ? this.captureLinkSource?.value && this.captureLinkTarget?.value && this.captureLinkSource.value !== this.captureLinkTarget.value
      : this.captureNode?.value;
    if (!this.scenarioId || !this.running || !target || this.captureId) return;
    const scenarioId = this.scenarioId;
    const generation = this.captureGeneration;
    const nodeId = zoneMode || linkMode ? null : target;
    const button = this.root.querySelector("[data-capture-start]");
    button.disabled = true;
    if (this.captureStatus) this.captureStatus.textContent = "Iniciando captura aislada…";
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/captures`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(zoneMode ? { zone_id: target } : linkMode
          ? { link_source_node_id: this.captureLinkSource.value, link_target_node_id: this.captureLinkTarget.value }
          : { node_id: target }), signal: AbortSignal.timeout(30000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      if (generation !== this.captureGeneration || scenarioId !== this.scenarioId) return;
      this.captureId = result.capture_id;
      if (this.captureStatus) this.captureStatus.textContent = zoneMode
        ? `Capturando el bridge Docker del segmento ${this.captureZone.selectedOptions[0]?.textContent || target}. Detén y descarga para finalizar.`
        : linkMode
          ? `Capturando tráfico entre ${this.deviceNames?.get(this.captureLinkSource.value) || this.captureLinkSource.value} y ${this.deviceNames?.get(this.captureLinkTarget.value) || this.captureLinkTarget.value}. Detén y descarga para finalizar.`
        : `Capturando el enlace de ${this.deviceNames?.get(nodeId) || nodeId}. Detén y descarga para finalizar.`;
    } catch (error) {
      if (generation === this.captureGeneration && this.captureStatus) this.captureStatus.textContent = `No se pudo iniciar la captura: ${error.message}`;
    } finally {
      if (generation === this.captureGeneration) this.renderCaptureControls();
    }
  }

  async exportPacketCapture() {
    if (!this.scenarioId || !this.captureId) return;
    const scenarioId = this.scenarioId;
    const captureId = this.captureId;
    const generation = this.captureGeneration;
    const button = this.root.querySelector("[data-capture-export]");
    button.disabled = true;
    if (this.captureStatus) this.captureStatus.textContent = "Cerrando captura y preparando PCAP…";
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/captures/${captureId}/pcap`, { signal: AbortSignal.timeout(30000) });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      if (generation !== this.captureGeneration || scenarioId !== this.scenarioId) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `anvil-capture-${captureId}.pcap`;
      anchor.click();
      URL.revokeObjectURL(url);
      this.captureId = null;
      if (this.captureStatus) this.captureStatus.textContent = `PCAP descargado · ${(blob.size / 1024).toFixed(1)} KiB. El contenedor de captura se eliminó.`;
    } catch (error) {
      if (generation === this.captureGeneration && this.captureStatus) this.captureStatus.textContent = `No se pudo exportar el PCAP: ${error.message}`;
    } finally {
      if (generation === this.captureGeneration) this.renderCaptureControls();
    }
  }

  async loadExercises() {
    if (!this.scenarioId) return;
    const scenarioId = this.scenarioId;
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/exercises`);
      const runs = await response.json();
      if (!response.ok) throw new Error(runs.detail || `HTTP ${response.status}`);
      if (scenarioId !== this.scenarioId) return;
      if (!this.exerciseRun && runs.length) this.exerciseRun = runs[0];
      if (this.exerciseRun) {
        const latest = runs.find((item) => item.id === this.exerciseRun.id);
        if (latest) this.exerciseRun = latest;
      }
      if (this.exerciseRun?.definition) {
        this.exerciseEditor.load(this.exerciseRun.definition);
        this.exerciseDefinition.value = JSON.stringify(this.exerciseRun.definition, null, 2);
        this.exerciseAdvancedDirty = false;
      }
      this.renderExerciseControls();
      if (this.exerciseRun?.status === "paused") this.exerciseStatus.textContent = `Ejercicio suspendido por la pausa del escenario · ${this.exerciseRun.definition.name}`;
      if (["running", "paused"].includes(this.exerciseRun?.status)) this.scheduleExerciseRefresh();
    } catch (error) {
      if (this.exerciseStatus) this.exerciseStatus.textContent = `No se pudieron cargar los ejercicios: ${error.message}`;
    }
  }

  async createExercise() {
    if (!this.scenarioId || !this.exerciseDefinition) return;
    try {
      const advanced = this.root.querySelector(".exercise-advanced")?.open;
      const definition = advanced && this.exerciseDefinition.value.trim()
        ? JSON.parse(this.exerciseDefinition.value)
        : this.exerciseEditor.toDefinition();
      const response = await fetch(`/api/scenarios/${this.scenarioId}/exercises`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(definition),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      this.exerciseRun = result;
      this.exerciseStatus.textContent = `Definición guardada · ${result.definition.name}. Despliega el escenario para iniciar.`;
      this.renderExerciseControls();
    } catch (error) {
      this.exerciseStatus.textContent = `No se pudo guardar: ${error.message}`;
    }
  }

  async startExercise() {
    if (!this.scenarioId || !this.exerciseRun || this.exerciseRun.status !== "ready") return;
    try {
      const response = await fetch(`/api/scenarios/${this.scenarioId}/exercises/${this.exerciseRun.id}/start`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      this.exerciseRun = result;
      this.exerciseStatus.textContent = `Ejercicio en curso · ${result.definition.name}`;
      this.renderExerciseControls();
      this.scheduleExerciseRefresh();
    } catch (error) {
      this.exerciseStatus.textContent = `No se pudo iniciar: ${error.message}`;
    }
  }

  async cancelExercise() {
    if (!this.scenarioId || !["running", "paused"].includes(this.exerciseRun?.status)) return;
    try {
      const response = await fetch(`/api/scenarios/${this.scenarioId}/exercises/${this.exerciseRun.id}/cancel`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      this.exerciseRun = result;
      clearTimeout(this.exercisePollTimer);
      this.exerciseStatus.textContent = `Ejercicio ${result.status}.`;
      this.renderExerciseControls();
    } catch (error) {
      this.exerciseStatus.textContent = `No se pudo cancelar: ${error.message}`;
    }
  }

  scheduleExerciseRefresh() {
    clearTimeout(this.exercisePollTimer);
    this.exercisePollTimer = setTimeout(() => this.refreshExercise(), 750);
  }

  async refreshExercise() {
    if (!this.scenarioId || !this.exerciseRun) return;
    const scenarioId = this.scenarioId;
    const runId = this.exerciseRun.id;
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/exercises/${runId}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      if (scenarioId !== this.scenarioId || runId !== this.exerciseRun?.id) return;
      this.exerciseRun = result;
      this.renderExerciseControls();
      if (result.status === "running") this.scheduleExerciseRefresh();
      else if (result.status === "paused") {
        this.exerciseStatus.textContent = `Ejercicio suspendido por la pausa del escenario · ${result.definition.name}`;
        this.scheduleExerciseRefresh();
      } else this.exerciseStatus.textContent = `Ejercicio ${result.status} · ${result.definition.name}`;
    } catch (error) {
      this.exerciseStatus.textContent = `No se pudo actualizar: ${error.message}`;
    }
  }

  renderExerciseControls() {
    const create = this.root.querySelector("[data-exercise-create]");
    const start = this.root.querySelector("[data-exercise-start]");
    const cancel = this.root.querySelector("[data-exercise-cancel]");
    const status = this.exerciseRun?.status;
    if (create) create.disabled = !this.scenarioId || ["running", "paused"].includes(status);
    if (start) start.disabled = !this.running || status !== "ready";
    if (cancel) cancel.disabled = !["running", "paused"].includes(status);
    if (this.exerciseResults) {
      if (!this.exerciseRun) this.exerciseResults.replaceChildren();
      else {
        const events = this.exerciseRun.event_results || [];
        const objectives = this.exerciseRun.objective_results || [];
        const score = Number.isInteger(this.exerciseRun.score) ? ` · Puntuación ${this.exerciseRun.score}/100` : "";
        const result = this.exerciseRun.passed === null || this.exerciseRun.passed === undefined
          ? "" : this.exerciseRun.passed ? " · Superado" : " · No superado";
        this.exerciseResults.innerHTML = `<p>${events.length} evento(s) ejecutado(s), ${objectives.filter((item) => item.passed).length}/${objectives.length} objetivo(s) cumplidos.${score}${result}</p><pre>${escapeHtml(JSON.stringify({ events, objectives }, null, 2))}</pre>`;
      }
    }
  }

  pushChange(change) {
    this.changes.unshift(change);
    this.changes = this.changes.slice(0, 100);
    this.renderHistory();
  }

  renderHistory() {
    if (!this.history) return;
    if (!this.changes.length) {
      this.history.innerHTML = '<div class="runtime-empty">Los cambios observados aparecerán aquí.</div>';
      return;
    }
    this.history.innerHTML = this.changes.map((change) => `
      <article class="history-item ${change.operation}">
        <time>${escapeHtml(new Date(change.timestamp).toLocaleTimeString("es-ES"))}</time>
        <strong>${escapeHtml(change.address)}</strong>
        <span>${escapeHtml(change.node_id)} · ${change.operation === "write" ? "Escritura" : "Cambio observado"} · ${escapeHtml(change.previous ?? "—")} → ${escapeHtml(change.value)}</span>
        <code>${escapeHtml(change.raw_hex || "")}</code>
      </article>`).join("");
  }

  renderProtocol(payload) {
    if (!this.protocol) return;
    if (payload?.operations) payload.operations.forEach((item) => this.addProtocolEvent(item));
    if (!this.protocolEvents.length) {
      this.protocol.innerHTML = '<div class="runtime-empty">Lee o escribe un punto para ver la actividad S7.</div>';
      return;
    }
    this.protocol.innerHTML = this.protocolEvents.map((item) => `
      <article class="protocol-item ${item.operation}">
        <header><span>${escapeHtml(item.operation.toUpperCase())}</span><time>${escapeHtml(item.timestamp)}</time></header>
        <strong>${escapeHtml(item.address || `${item.area ?? "DB"}${item.db ? item.db : ""}`)}</strong>
        <span>${escapeHtml(item.node_id)} · ${escapeHtml(item.source || "Anvil")}</span>
        <code>${escapeHtml(item.raw_hex || "—")}</code>
        <small>${escapeHtml(item.byte_count ?? 0)} bytes · ${escapeHtml(item.result)}</small>
      </article>`).join("");
  }

  addProtocolEvent(item) {
    const key = JSON.stringify(item);
    if (this.protocolKeys.has(key)) return;
    this.protocolKeys.add(key);
    this.protocolEvents.unshift(item);
    this.protocolEvents = this.protocolEvents.slice(0, 100);
    if (this.protocolKeys.size > 250) {
      this.protocolKeys = new Set(this.protocolEvents.map((entry) => JSON.stringify(entry)));
    }
  }

  renderLogs(payload) {
    const containers = payload?.containers || [];
    this.logs.replaceChildren();
    if (!containers.length) {
      const empty = document.createElement("div");
      empty.className = "runtime-empty";
      empty.textContent = "Despliega el escenario para ver la salida de los PLC.";
      this.logs.append(empty);
    } else {
      containers.forEach((container) => {
        const block = document.createElement("article");
        block.className = "log-block";
        const title = document.createElement("strong");
        title.textContent = container.name;
        const output = document.createElement("pre");
        output.textContent = container.lines.length
          ? container.lines.join("\n") : "Esperando salida del proceso…";
        block.append(title, output);
        this.logs.append(block);
        output.scrollTop = output.scrollHeight;
        container.lines.forEach((line) => {
          const match = line.match(/S7 (READ|WRITE) source=(\S+) (DB(\d+)|([MIQ])) bytes\[(\d+):(\d+)\] data=(.*?) result=(\w+) code=(\d+)/);
          if (!match) return;
          const [, operation, source, , dbRaw, areaRaw, offset, end, raw, result] = match;
          const area = dbRaw ? "DB" : areaRaw;
          const db = dbRaw ? Number(dbRaw) : null;
          this.addProtocolEvent({
            timestamp: line.split(" ", 1)[0], operation: operation.toLowerCase(),
            source, node_id: container.node_id, area, db,
            address: area === "DB" ? `DB${db}.BYTE${offset}` : `${area}.BYTE${offset}`, byte_count: Number(end) - Number(offset),
            raw_hex: raw === "-" ? "" : raw, result,
          });
        });
      });
    }
    this.renderProtocol();
  }

  async loadProtocolSnapshot() {
    if (!this.scenarioId) return;
    const generation = this.generation;
    try {
      const response = await fetch(`/api/scenarios/${this.scenarioId}/protocol`);
      const payload = response.ok ? await response.json() : null;
      if (generation === this.generation && payload) this.renderProtocol(payload);
    } catch (_error) {
      // The live log socket remains the source of protocol events.
    }
  }

  async loadAudit({ append = false } = {}) {
    if (!this.scenarioId || !this.audit) {
      if (this.audit) this.audit.innerHTML = '<div class="runtime-empty">Guarda un escenario para consultar la auditoría.</div>';
      return;
    }
    const scenarioId = this.scenarioId;
    const generation = this.generation;
    if (!append) this.audit.innerHTML = '<div class="runtime-empty">Cargando eventos…</div>';
    const query = append && this.auditCursor ? `?limit=100&before_id=${this.auditCursor}` : "?limit=100";
    try {
      const response = await fetch(`/api/scenarios/${scenarioId}/audit-events${query}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      if (generation !== this.generation || scenarioId !== this.scenarioId) return;
      this.auditEvents = append ? [...this.auditEvents, ...payload.events] : payload.events;
      this.auditCursor = payload.next_before_id;
      this.renderAudit();
    } catch (error) {
      if (generation === this.generation) this.audit.innerHTML = `<div class="runtime-empty">No se pudo leer la auditoría: ${escapeHtml(error.message)}</div>`;
    }
  }

  renderAudit() {
    if (!this.audit) return;
    const exportHref = `/api/scenarios/${this.scenarioId}/audit-events/export`;
    const exportLink = this.capabilities.auditExport ? `<a class="tiny-button" href="${exportHref}">Exportar JSONL</a>` : "";
    const toolbar = `<div class="audit-toolbar">${exportLink}<button class="tiny-button" type="button" data-audit-refresh>Actualizar</button></div>`;
    if (!this.auditEvents.length) {
      this.audit.innerHTML = `${toolbar}<div class="runtime-empty">Aún no hay eventos registrados para este escenario.</div>`;
      return;
    }
    const events = this.auditEvents.map((event) => `<article class="audit-item"><header><strong>${escapeHtml(event.event_type)}</strong><time>${escapeHtml(new Date(event.timestamp).toLocaleString("es-ES"))}</time></header><span>${escapeHtml(event.actor)}</span><code>${escapeHtml(JSON.stringify(event.details))}</code></article>`).join("");
    const more = this.auditCursor ? '<button class="tiny-button" type="button" data-audit-more>Cargar anteriores</button>' : '<p class="runtime-empty">Fin del historial disponible.</p>';
    this.audit.innerHTML = `${toolbar}${events}${more}`;
  }

  reset() {
    if (this.deviceFilter) this.deviceFilter.innerHTML = '<option value="">Todos los dispositivos</option>';
    clearInterval(this.pollTimer);
    clearTimeout(this.exercisePollTimer);
    this.pollTimer = null;
    this.exercisePollTimer = null;
    this.exerciseRun = null;
    this.points = [];
    this.fieldObjects = [];
    this.generation += 1;
    this.scenarioId = null;
    this.running = false;
    this.refreshing = false;
    this.devices.clear();
    this.errors.clear();
    this.drafts.clear();
    this.writing.clear();
    this.changes = [];
    this.protocolEvents = [];
    this.protocolKeys.clear();
    this.auditEvents = [];
    this.auditCursor = null;
    this.captureId = null;
    this.captureGeneration += 1;
    this.evidenceExportBusy = false;
    if (this.captureNode) this.captureNode.replaceChildren();
    if (this.captureZone) this.captureZone.replaceChildren();
    if (this.evidenceCaptureCheckbox) this.evidenceCaptureCheckbox.checked = false;
    if (this.captureStatus) this.captureStatus.textContent = "Despliega el escenario y selecciona un dispositivo.";
    if (this.exerciseStatus) this.exerciseStatus.textContent = "Guarda un escenario para preparar un ejercicio.";
    if (this.exerciseDefinition) this.exerciseDefinition.value = "";
    this.exerciseAdvancedDirty = false;
    if (this.exerciseResults) this.exerciseResults.replaceChildren();
    this.exerciseEditor.load({ name: "Ejercicio OT", events: [], objectives: [] });
    if (this.audit) this.audit.innerHTML = '<div class="runtime-empty">Selecciona un escenario para consultar la auditoría.</div>';
    this.renderHistory();
    this.values.clear();
    this.renderIoMap();
    this.renderLogs({ containers: [] });
    this.setConnection("Sin escenario");
    this.renderExerciseControls();
    this.selectTab("map");
  }
}
