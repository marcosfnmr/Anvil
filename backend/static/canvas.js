import { getNodeType } from "./nodes/registry.js";

function clone(value) {
  return globalThis.structuredClone
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

export class ScenarioCanvas {
  constructor(element, { onSelect, onChange } = {}) {
    if (!globalThis.Drawflow) {
      throw new Error("No se pudo cargar Drawflow desde el CDN");
    }

    this.element = element;
    this.onSelect = onSelect || (() => {});
    this.onChange = onChange || (() => {});
    this.selectedId = null;
    this.editor = new Drawflow(element);
    this.editor.reroute = true;
    this.editor.reroute_fix_curvature = true;
    this.editor.zoom_min = 0.45;
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
      this.selectedId = null;
      this.onSelect(null);
      this.onChange();
      this.updateEmptyState();
    });
    this.editor.on("nodeMoved", () => this.onChange());

    this.bindDropTarget();
  }

  bindDropTarget() {
    this.element.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });

    this.element.addEventListener("drop", (event) => {
      event.preventDefault();
      const type = event.dataTransfer.getData("application/x-anvil-node");
      if (!type) return;

      const rect = this.element.getBoundingClientRect();
      const zoom = this.editor.zoom || 1;
      this.addNodeType(
        type,
        (event.clientX - rect.left) / zoom,
        (event.clientY - rect.top) / zoom,
      );
    });
  }

  addNodeType(type, x = 120, y = 120, suggestedIp) {
    const definition = getNodeType(type);
    const index = this.count() + 1;
    const data = definition.create(index, suggestedIp);
    return this.addDevice(data, x, y);
  }

  addDevice(data, x, y) {
    const definition = getNodeType(data.device_type);
    const id = this.editor.addNode(
      data.device_type,
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
  }

  clear() {
    this.editor.clear();
    this.selectedId = null;
    this.updateEmptyState();
  }

  count() {
    return Object.keys(this.exportNodes()).length;
  }

  exportNodes() {
    return this.editor.export().drawflow.Home?.data || {};
  }

  getDevices() {
    return Object.values(this.exportNodes()).map((node) => clone(node.data));
  }

  updateSelected(data) {
    if (!this.selectedId) return;
    this.editor.updateNodeDataFromId(this.selectedId, clone(data));
    this.refreshNode(this.selectedId, data);
    this.onChange();
  }

  refreshNode(id, data) {
    const definition = getNodeType(data.device_type);
    const content = this.element.querySelector(`#node-${id} .drawflow_content_node`);
    if (content) content.innerHTML = definition.render(data);
  }

  removeSelected() {
    if (!this.selectedId) return;
    this.editor.removeNodeId(`node-${this.selectedId}`);
  }

  syncRuntimeStatus(containers = []) {
    const statusByNode = new Map(containers.map((item) => [item.node_id, item.status]));
    Object.entries(this.exportNodes()).forEach(([id, node]) => {
      const element = this.element.querySelector(`#node-${id}`);
      if (!element) return;
      [...element.classList]
        .filter((className) => className.startsWith("runtime-"))
        .forEach((className) => element.classList.remove(className));
      const runtimeStatus = statusByNode.get(node.data.node_id);
      if (runtimeStatus) element.classList.add(`runtime-${runtimeStatus}`);
    });
  }

  updateEmptyState() {
    document.getElementById("empty-canvas")?.classList.toggle("hidden", this.count() > 0);
    const summary = document.getElementById("canvas-summary");
    if (summary) {
      const count = this.count();
      summary.textContent = `${count} dispositivo${count === 1 ? "" : "s"}`;
    }
  }
}
