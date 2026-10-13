import { registerNodeType } from "./registry.js?v=field-live-3";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const roles = [
  ["hmi", "HMI", "HMI", "Lee valores de PLC S7 y Modbus configurados"],
  ["scada", "SCADA", "SCADA", "Adquiere datos S7/Modbus, guarda tendencias y genera alarmas"],
  ["gateway", "Gateway", "GW", "Nodo representativo; no enruta tráfico"],
  ["firewall", "Política de consola", "FW", "Controla puertos de la API; no filtra la red"],
  ["attacker-station", "Estación de participante", "PART", "Acceso de red opcional y limitado a su zona Purdue"],
  ["engineering-station", "Estación de ingeniería", "ENG", "Host de ingeniería con acceso opcional a su zona Purdue"],
  ["ethernet-switch", "Switch Ethernet", "SW", "Conmuta tráfico L2 en el bridge Docker interno de su zona"],
];

for (const [type, label, icon, summary] of roles) {
  registerNodeType(type, {
    label, shortLabel: label, icon, description: summary,
    className: `node-${type}`,
    create(index = 1, suggestedIp = `172.30.100.${index + 9}`) {
      const id = `${type}-${globalThis.crypto?.randomUUID?.().slice(0, 8) || `${Date.now().toString(36)}-${index}`}`;
      return {
        node_id: id, device_type: type, name: `${label} ${index}`, ip: suggestedIp,
        external_access: false, external_port: null,
        config: type === "ethernet-switch"
          ? { role: "switch", summary, target_node_ids: "", alarm_source_node_ids: "", firewall_allow_tcp_ports: [], participant_access_enabled: false, engineering_access_enabled: false, switch_port_capacity: 24 }
          : type === "scada"
          ? { target_node_ids: "", poll_interval_ms: 1000, history_capacity: 600, alarms: [] }
          : { role: type === "attacker-station" ? "attacker" : type === "engineering-station" ? "engineering" : type, summary, target_node_ids: "", alarm_source_node_ids: "", firewall_allow_tcp_ports: [], participant_access_enabled: false, engineering_access_enabled: false },
      };
    },
    render(data) {
      const detail = data.device_type === "scada"
        ? `Sondeo ${Number(data.config?.poll_interval_ms || 1000)} ms · ${(data.config?.target_node_ids || "").split(",").filter(Boolean).length} objetivos`
        : data.device_type === "ethernet-switch" ? `Bridge L2 de zona · ${Number(data.config?.switch_port_capacity || 24)} puertos`
        : data.config?.summary;
      return `<article class="device-node"><header class="device-node-header"><span class="device-node-icon">${icon}</span><span class="device-node-title"><strong>${escapeHtml(data.name)}</strong><small>${escapeHtml(data.node_id)}</small></span><span class="node-state"></span></header><div class="device-node-body"><span>${escapeHtml(data.ip)}</span><span>${escapeHtml(detail)}</span></div></article>`;
    },
  });
}
