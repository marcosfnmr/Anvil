import { registerNodeType } from "./registry.js?v=field-live-3";
import { renderDeviceCard } from "./card.js?v=canvas-node-cards-20261013";

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
      const kind = data.device_type === "hmi" ? "Operación · HMI"
        : data.device_type === "scada" ? "Supervisión · SCADA"
          : data.device_type === "ethernet-switch" ? "Conmutación · Ethernet"
            : data.device_type === "engineering-station" ? "Puesto · Ingeniería"
              : data.device_type === "attacker-station" ? "Puesto · Participante"
                : `${label} · elemento lógico`;
      const targets = (data.config?.target_node_ids || "").split(",").filter(Boolean).length;
      const metrics = data.device_type === "scada"
        ? [
            { label: "Sondeo", value: `${Number(data.config?.poll_interval_ms || 1000)} ms` },
            { label: "Fuentes", value: targets },
          ]
        : data.device_type === "ethernet-switch"
          ? [
              { label: "Puertos", value: Number(data.config?.switch_port_capacity || 24) },
              { label: "Red", value: "Bridge L2" },
            ]
          : data.device_type === "hmi"
            ? [
                { label: "Fuentes", value: targets },
                { label: "Vista", value: "Operación" },
              ]
            : [
                { label: "Perfil", value: label },
                { label: "Acceso", value: data.external_access ? "Publicado" : "Interno" },
              ];
      return renderDeviceCard({
        type: data.device_type,
        name: data.name,
        icon,
        kind,
        ip: data.ip,
        metrics,
      });
    },
  });
}
