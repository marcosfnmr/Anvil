import { registerNodeType } from "./registry.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function uniqueNodeId(index) {
  if (globalThis.crypto?.randomUUID) {
    return `plc-${crypto.randomUUID().slice(0, 8)}`;
  }
  return `plc-${Date.now().toString(36)}-${index}`;
}

registerNodeType("plc-s7", {
  label: "PLC Siemens S7",
  shortLabel: "PLC S7",
  description: "Servidor S7 parametrizable",
  icon: "S7",
  className: "node-plc-s7",

  create(index = 1, suggestedIp = `172.30.100.${index + 9}`) {
    return {
      node_id: uniqueNodeId(index),
      device_type: "plc-s7",
      name: `PLC S7 ${index}`,
      ip: suggestedIp,
      config: {
        rack: 0,
        slot: 1,
        db_size: 32,
        components: [
          {
            type: "valve",
            name: "valvula_1",
            db: 1,
            initial_state: false,
          },
        ],
      },
    };
  },

  render(data) {
    const componentCount = data.config?.components?.length || 0;
    return `
      <article class="device-node">
        <header class="device-node-header">
          <span class="device-node-icon">S7</span>
          <span class="device-node-title">
            <strong>${escapeHtml(data.name)}</strong>
            <small>${escapeHtml(data.node_id)}</small>
          </span>
          <span class="node-state" title="Estado del contenedor"></span>
        </header>
        <div class="device-node-body">
          <span>${escapeHtml(data.ip)}</span>
          <span>${componentCount} comp.</span>
        </div>
      </article>
    `;
  },
});
