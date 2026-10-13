import { registerNodeType } from "./registry.js?v=field-live-3";

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
      external_access: false,
      external_port: null,
      config: {
        rack: 0,
        slot: 1,
        db_size: 32,
        scan_cycle_ms: 100,
        retain_logic_state: false,
        response_delay_ms: 0,
        response_jitter_ms: 0,
        response_jitter_distribution: "deterministic",
        response_jitter_seed: 0,
        fault_mode: "none",
        components: [],
        control_rules: [],
        boolean_rules: [],
        latches: [],
        counters: [],
        sequences: [],
      },
    };
  },

  render(data) {
    const componentCount = data.config?.components?.length || 0;
    const logicCount = (data.config?.control_rules?.length || 0) + (data.config?.boolean_rules?.length || 0) + (data.config?.timers?.length || 0) + (data.config?.latches?.length || 0) + (data.config?.counters?.length || 0) + (data.config?.sequences?.length || 0);
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
          <span>${componentCount} señales · ${logicCount} bloques</span>
        </div>
      </article>
    `;
  },
});
