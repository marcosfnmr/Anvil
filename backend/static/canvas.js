import { MapViewport } from "./map-viewport.js?v=field-live-3";
import { ProcessMap } from "./process-map.js?v=canvas-links-live-20261013";
import { getNodeType } from "./nodes/registry.js?v=field-live-3";
import { s7Signals } from "./components/remote_input.js?v=field-live-3";

function clone(value) {
  return globalThis.structuredClone
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

export class ScenarioCanvas {
  constructor(element, { onSelect, onChange, suggestIp, onError } = {}) {
    if (!globalThis.Drawflow) {
      throw new Error("No se pudo cargar el editor local. Recarga la página.");
    }

    this.element = element;
    this.onSelect = onSelect || (() => {});
    this.onChange = onChange || (() => {});
    this.suggestIp = suggestIp;
    this.onError = onError || (() => {});
    this.locked = false;
    this.selectedId = null;
    this.signalConnections = [];
    this.processConnections = [];
    this.networkRoutes = [];
    this.egressRules = [];
    this.networkConnections = [];
    this.editor = new Drawflow(element);
    this.editor.reroute = true;
    this.editor.reroute_fix_curvature = true;
    this.editor.zoom_min = 0.2;
    this.editor.zoom_max = 1.6;
    this.editor.start();

    this.editor.on("nodeSelected", (id) => {
      this.selectedId = String(id);
      this.onSelect(clone(this.editor.getNodeFromId(id).data));
    });
    this.editor.on("nodeUnselected", () => {
      this.selectedId = null;
      this.onSelect(null);
    });
    this.editor.on("nodeRemoved", () => {
      const nodes = this.exportNodes();
      const remaining = new Set(Object.values(nodes).map((node) => node.data.node_id));
      const fields = new Set(Object.values(nodes).map((node) => node.data.object_id));
      this.signalConnections = this.signalConnections.filter((link) => [link.source, link.target].every((endpoint) => endpoint.kind === 'field' ? fields.has(endpoint.object_id) : remaining.has(endpoint.node_id)));
      this.processConnections = this.processConnections.filter((link) => [link.source, link.target].every((endpoint) => fields.has(endpoint.object_id)));
      const retainedRoutes = this.networkRoutes.filter((route) => remaining.has(route.source_node_id) && remaining.has(route.target_node_id));
      if (retainedRoutes.length !== this.networkRoutes.length) {
        this.networkRoutes = retainedRoutes;
        this.element.dispatchEvent(new CustomEvent('network-routes-change', { detail: this.networkRoutes }));
      }
      const retainedEgressRules = this.egressRules.filter((rule) => remaining.has(rule.source_node_id));
      if (retainedEgressRules.length !== this.egressRules.length) {
        this.egressRules = retainedEgressRules;
        this.element.dispatchEvent(new CustomEvent('network-egress-rules-change', { detail: this.egressRules }));
      }
      this.networkConnections = this.networkConnections.filter((link) => remaining.has(link.source_node_id) && remaining.has(link.target_node_id));
      Object.entries(nodes).forEach(([id, node]) => {
        const targets = node.data.config?.target_node_ids;
        if (!targets) return;
        const next = targets.split(",").map((target) => target.trim()).filter((target) => remaining.has(target)).join(",");
        if (next !== targets) {
          node.data.config.target_node_ids = next;
          this.editor.updateNodeDataFromId(id, clone(node.data));
        }
      });
      this.selectedId = null;
      this.onSelect(null);
      this.onChange();
      this.updateEmptyState();
      this.processMap?.render();
      this.onFieldChange?.();
    });
    this.editor.on("mouseMove", () => {
      // Drawflow emits nodeMoved only when the drag ends. Its own connection
      // paths move continuously, but Anvil's SVG relationships otherwise lag
      // behind until mouseup. Coalesce both custom link layers to one frame.
      if (!this.editor.drag || this.linkRefreshFrame) return;
      this.linkRefreshFrame = requestAnimationFrame(() => {
        this.linkRefreshFrame = null;
        this.processMap?.drawLinks();
        this.onFieldChange?.();
      });
    });
    this.editor.on("nodeMoved", () => {
      if (this.linkRefreshFrame) cancelAnimationFrame(this.linkRefreshFrame);
      this.linkRefreshFrame = null;
      this.processMap?.drawLinks();
      this.onFieldChange?.();
      this.onChange();
    });
    this.processMap = new ProcessMap(this);
    this.viewport = new MapViewport(this);

    this.bindDropTarget();
  }

  bindDropTarget() {
    this.element.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });

    this.element.addEventListener("drop", (event) => {
      event.preventDefault();
      if (this.locked) return;
      const type = event.dataTransfer.getData("application/x-anvil-node");
      if (!type) return;

      const rect = this.element.getBoundingClientRect();
      const zoom = this.editor.zoom || 1;
      try {
        this.addNodeType(type,
          (event.clientX - rect.left - this.editor.canvas_x) / zoom,
          (event.clientY - rect.top - this.editor.canvas_y) / zoom,
          this.suggestIp?.());
      } catch (error) { this.onError(error.message); }
    });
  }

  addNodeType(type, x = 120, y = 120, suggestedIp) {
    if (this.locked) return;
    const definition = getNodeType(type);
    const index = this.count() + 1;
    const data = definition.create(index, suggestedIp);
    const id = this.addDevice(data, x, y);
    this.selectNode(id);
    return id;
  }

  addDevice(data, x, y) {
    const definition = getNodeType(data.object_id ? `field-${data.type}` : data.device_type);
    const id = this.editor.addNode(
      data.object_id ? `field-${data.type}` : data.device_type,
      0,
      0,
      x,
      y,
      definition.className || "",
      clone(data),
      definition.render(data),
      false,
    );
    this.updateEmptyState();
    this.processMap?.render();
    this.onFieldChange?.();
    this.onChange();
    return id;
  }

  load(devices = []) {
    this.editor.clear();
    this.selectedId = null;
    devices.forEach((device, index) => {
      const column = index % 3;
      const row = Math.floor(index / 3);
      this.addDevice(device, 90 + column * 280, 110 + row * 190);
    });
    this.updateEmptyState();
    this.processMap.load();
  }

  clear() {
    this.signalConnections = [];
    this.processConnections = [];
    this.egressRules = [];
    this.networkConnections = [];
    this.editor.clear();
    this.selectedId = null;
    this.processMap.load();
    this.updateEmptyState();
  }

  count() {
    return Object.keys(this.exportNodes()).length;
  }

  exportNodes() {
    return this.editor.export().drawflow.Home?.data || {};
  }

  getDevices() {
    return Object.values(this.exportNodes()).filter((node) => !node.data.object_id).map((node) => clone(node.data));
  }

  getFieldObjects() {
    return Object.values(this.exportNodes()).filter((node) => node.data.object_id).map((node) => ({ ...clone(node.data), position: { x: node.pos_x, y: node.pos_y } }));
  }

  exportTopology() {
    const exported = this.editor.export();
    return { ...clone(exported.drawflow?.Home || { data: {} }), ...clone(this.processMap.export()) };
  }

  setNetworkRoutes(routes = []) {
    this.networkRoutes = clone(routes);
    this.processMap?.renderNetworkConnections();
    this.processMap?.drawLinks();
  }

  setEgressRules(rules = []) {
    this.egressRules = clone(rules);
    this.processMap.egressRules = this.egressRules;
    this.processMap.renderNetworkConnections();
  }

  setNetworkRouteEndpoints(endpoints = []) {
    this.networkRouteEndpoints = clone(endpoints);
    this.processMap?.renderNetworkConnections();
  }

  setNetworkConnections(connections = []) {
    this.networkConnections = clone(connections);
    this.processMap?.render();
  }

  loadTopology(topology, devices = [], fields = [], connections = [], processConnections = []) {
    this.signalConnections = clone(connections);
    this.processConnections = clone(processConnections);
    if (!topology || !topology.data || typeof topology.data !== "object") {
      this.load(devices);
      fields.forEach((field, i) => this.addDevice(field, field.position?.x ?? 80 + i * 280, field.position?.y ?? 350));
      this.onFieldChange?.();
      return;
    }
    this.editor.clear();
    this.selectedId = null;
    // The persisted devices are authoritative. Canvas data supplies layout only.
    // Never import saved HTML, node classes or a second copy of configuration.
    const positions = new Map(Object.values(topology.data)
      .filter((node) => node && typeof node === "object")
      .map((node) => [node.data?.node_id, node]));
    devices.forEach((device, index) => {
      const position = positions.get(device.node_id);
      const x = Number.isFinite(position?.pos_x) ? position.pos_x : 60 + (index % 2) * 260;
      const y = Number.isFinite(position?.pos_y) ? position.pos_y : 110 + Math.floor(index / 2) * 170;
      this.addDevice(device, x, y);
    });
    fields.forEach((field, i) => this.addDevice(field, field.position?.x ?? 80 + i * 280, field.position?.y ?? 350));
    this.updateEmptyState();
    this.processMap.load(topology);
    this.onFieldChange?.();
  }

  selectNode(id) {
    this.element.querySelectorAll(".drawflow-node.selected").forEach((node) => node.classList.remove("selected"));
    this.selectedId = String(id);
    const node = this.element.querySelector(`#node-${id}`);
    node?.classList.add("selected");
    this.editor.node_selected = node;
    this.onSelect(clone(this.editor.getNodeFromId(id).data));
  }

  selectDevice(nodeId) {
    const entry = Object.entries(this.exportNodes()).find(([, node]) => node.data.node_id === nodeId || node.data.object_id === nodeId);
    if (entry) this.selectNode(entry[0]);
  }

  setLocked(locked) {
    this.locked = locked;
    this.editor.editor_mode = locked ? "view" : "edit";
    this.processMap?.renderNetworkConnections();
  }

  updateSelected(data) {
    if (!this.selectedId) return;
    this.editor.updateNodeDataFromId(this.selectedId, clone(data));
    if (data.device_type === 'plc-s7') {
      const channels = new Map(s7Signals(data).filter(p=>p.channel_id).map(p=>[p.channel_id,p.address]));
      for (const link of this.signalConnections) for (const endpoint of [link.source,link.target]) {
        if (endpoint.kind==='device' && endpoint.node_id===data.node_id && endpoint.channel_id && channels.has(endpoint.channel_id)) endpoint.address=channels.get(endpoint.channel_id);
      }
    }
    this.refreshNode(this.selectedId, data);
    this.processMap.render();
    this.onFieldChange?.();
    this.onChange();
  }

  updateDevice(data) {
    const entry = Object.entries(this.exportNodes()).find(([, node]) => node.data.node_id === data.node_id);
    if (!entry) return;
    const [id] = entry;
    this.editor.updateNodeDataFromId(id, clone(data));
    this.refreshNode(id, data);
  }

  refreshNode(id, data) {
    const definition = getNodeType(data.object_id ? `field-${data.type}` : data.device_type);
    const content = this.element.querySelector(`#node-${id} .drawflow_content_node`);
    if (content) content.innerHTML = definition.render(data);
  }

  removeSelected() {
    if (!this.selectedId || this.locked) return;
    this.editor.removeNodeId(`node-${this.selectedId}`);
  }

  syncRuntimeStatus(containers = []) {
    const statusLabels = {
      created: "Preparado", running: "En marcha", paused: "En pausa",
      restarting: "Reiniciando", starting: "Iniciando", exited: "Detenido",
      dead: "Detenido", unhealthy: "Sin salud", missing: "No encontrado",
      error: "Error",
    };
    const statusByNode = new Map(containers.map((item) => [item.node_id, item]));
    Object.entries(this.exportNodes()).forEach(([id, node]) => {
      const element = this.element.querySelector(`#node-${id}`);
      if (!element) return;
      [...element.classList]
        .filter((className) => className.startsWith("runtime-"))
        .forEach((className) => element.classList.remove(className));
      const runtime = statusByNode.get(node.data.node_id);
      if (runtime?.status) element.classList.add(`runtime-${runtime.status}`);
      const indicator = element.querySelector(".node-state");
      if (indicator) {
        const detail = runtime?.health_error || runtime?.runtime_error;
        const label = statusLabels[runtime?.status] || "Sin desplegar";
        indicator.textContent = label;
        indicator.title = detail ? `${label}: ${detail}` : `Estado del contenedor: ${label}`;
        indicator.setAttribute("aria-label", indicator.title);
      }
      let metrics = element.querySelector("[data-runtime-metrics]");
      if (runtime && runtime.status !== "missing") {
        if (!metrics) {
          metrics = document.createElement("small");
          metrics.className = "node-runtime-metrics";
          metrics.dataset.runtimeMetrics = "";
          metrics.setAttribute("aria-label", "Consumo del contenedor");
          element.querySelector(".drawflow_content_node")?.append(metrics);
        }
        const usedMemory = runtime.memory_usage_bytes === null || runtime.memory_usage_bytes === undefined
          ? "—" : (runtime.memory_usage_bytes / 1048576).toFixed(1);
        const limitMemory = runtime.memory_limit_bytes === null || runtime.memory_limit_bytes === undefined
          ? "—" : (runtime.memory_limit_bytes / 1048576).toFixed(1);
        const memory = `${usedMemory}/${limitMemory} MiB`;
        const cpu = runtime.cpu_percent === null || runtime.cpu_percent === undefined
          ? "—" : `${runtime.cpu_percent.toFixed(1)} %`;
        const pids = runtime.pids === null || runtime.pids === undefined ? "—" : String(runtime.pids);
        const sampledAt = runtime.resource_metrics_sampled_at
          ? Date.parse(runtime.resource_metrics_sampled_at) : NaN;
        const sampleAge = Number.isFinite(sampledAt)
          ? `${Math.max(0, Math.floor((Date.now() - sampledAt) / 1000))} s`
          : null;
        const metricState = runtime.resource_metrics_status === "good"
          ? `muestra hace ${sampleAge ?? "—"}`
          : runtime.resource_metrics_status === "unavailable" ? "métricas no disponibles"
            : runtime.resource_metrics_status === "missing" ? "runtime ausente"
              : runtime.resource_metrics_status === "not_applicable" ? "sin contenedor"
                : "sin muestra";
        const metricSummary = `CPU ${cpu} · Mem ${memory} · Proc. ${pids} · ${metricState}`;
        metrics.textContent = metricSummary;
        metrics.title = runtime.resource_metrics_sampled_at
          ? `${metricSummary} (UTC ${new Date(sampledAt).toISOString()})` : metricSummary;
        metrics.setAttribute("aria-label", metricSummary);
      } else if (metrics) {
        metrics.remove();
      }
    });
  }

  updateEmptyState() {
    document.getElementById("empty-canvas")?.classList.toggle("hidden", this.count() > 0);
    const summary = document.getElementById("canvas-summary");
    if (summary) {
      const count = this.count();
      summary.textContent = `${this.getDevices().length} dispositivos · ${this.getFieldObjects().length} elementos de campo`;
    }
  }
}
