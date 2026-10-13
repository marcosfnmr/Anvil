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
  if (globalThis.crypto?.randomUUID) return `modbus-${crypto.randomUUID().slice(0, 8)}`;
  return `modbus-${Date.now().toString(36)}-${index}`;
}

registerNodeType("modbus-tcp", {
  label: "Dispositivo Modbus TCP/RTU",
  shortLabel: "Modbus TCP/RTU",
  description: "Servidor Modbus TCP o RTU en serie virtual",
  icon: "MB",
  className: "node-modbus-tcp",
  create(index = 1, suggestedIp = `172.30.100.${index + 9}`) {
    return {
      node_id: uniqueNodeId(index),
      device_type: "modbus-tcp",
      name: `Modbus TCP ${index}`,
      ip: suggestedIp,
      external_access: false,
      external_port: null,
      config: {
        unit_id: 1,
        port: 502,
        transport: "tcp",
        baudrate: 19200,
        bytesize: 8,
        parity: "N",
        stopbits: 1,
        response_delay_ms: 0,
        response_jitter_ms: 0,
        response_jitter_distribution: "deterministic",
        response_jitter_seed: 0,
        fault_mode: "none",
        points: [
          { type: "coil", name: "coil_1", address: null, initial_value: false },
          { type: "holding_register", name: "holding_register_1", address: null, initial_value: 0, unit: "" },
          { type: "input_register", name: "input_register_1", address: null, initial_value: 0, unit: "" },
        ],
      },
    };
  },
  render(data) {
    const count = data.config?.points?.length || 0;
    return `
      <article class="device-node">
        <header class="device-node-header">
          <span class="device-node-icon">MB</span>
          <span class="device-node-title"><strong>${escapeHtml(data.name)}</strong><small>${escapeHtml(data.node_id)}</small></span>
          <span class="node-state" title="Estado del contenedor"></span>
        </header>
        <div class="device-node-body"><span>${escapeHtml(data.config?.transport === "rtu" ? `RTU · túnel ${data.ip}:${data.config?.port}` : `${data.ip}:${data.config?.port}`)}</span><span>${count} puntos</span></div>
      </article>`;
  },
});
