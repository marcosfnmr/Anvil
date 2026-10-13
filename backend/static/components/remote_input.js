import { registerComponentType } from "./registry.js?v=field-live-3";

registerComponentType("remote_input", {
  label: "Entrada remota S7", icon: "GET", deviceTypes: ["plc-s7"],
  create(index = 1) {
    return { type: "remote_input", name: `remota_${index}`, db: 1, data_type: "BOOL",
      source_node_id: "", source_address: "", source_name: null,
      period_ms: 200, timeout_ms: 1000, stale_after_ms: 1000, stale_behavior: "hold", unit: "" };
  },
  fields: [
    { path: "name", label: "Nombre de la entrada", type: "text", full: true },
    { path: "db", label: "DB local", type: "number", min: 1 },
    { path: "data_type", label: "Tipo", type: "select", options: [{ value: "BOOL", label: "BOOL" }, { value: "REAL", label: "REAL" }] },
    { path: "source_node_id", label: "PLC emisor", type: "select", options: [], full: true },
    { path: "source_address", label: "Señal que lee este PLC", type: "select", options: [], full: true },
    { path: "period_ms", label: "Consulta cada (ms)", type: "number", min: 100, max: 60000 },
    { path: "timeout_ms", label: "Timeout de red (ms)", type: "number", min: 100, max: 10000 },
    { path: "stale_after_ms", label: "Dato obsoleto tras (ms)", type: "number", min: 100, max: 120000, full: true },
    { path: "stale_behavior", label: "Al perder calidad", type: "select", options: [{ value: "hold", label: "Conservar control actual" }, { value: "force_off", label: "Forzar salidas controladas a OFF" }], full: true },
  ],
});

// Matches automatic S7 allocation. Used only for choosing a source; the backend
// independently resolves and validates the final map before saving/deploying.
export function s7Signals(device) {
  const cursors = new Map();
  const points = [];
  for (const component of device?.config?.components || []) {
    const area=component.type==='io_channel'?(component.area||'DB'):'DB', key=`${area}:${area==='DB'?component.db:0}`;
    let [byte, bit] = cursors.get(key) || [0, 0];
    if (component.type === 'io_channel') {
      if (bit) byte++;
      if (component.data_type === 'REAL' && byte % 2) byte++;
      points.push({ address: component.data_type === 'BOOL' ? (area==='DB'?`DB${component.db}.DBX${byte}.0`:`${area}${byte}.0`) : (area==='DB'?`DB${component.db}.DBD${byte}`:`${area}D${byte}`), name: component.name, label: component.name, data_type: component.data_type, unit: component.unit, channel_id: component.channel_id });
      cursors.set(key, [byte + (component.data_type === 'REAL' ? 4 : 1), 0]);
      continue;
    }
    const real = component.type === "analog_sensor" || component.type === "remote_input" && component.data_type === "REAL";
    if (real || component.type === "pump") {
      if (bit) byte += 1;
      if (byte % 2) byte += 1;
      bit = 0;
      if (component.type === "pump") {
        points.push({ address: `DB${component.db}.DBX${byte}.0`, name: component.name, label: `${component.name} · orden`, data_type: "BOOL" });
        points.push({ address: `DB${component.db}.DBD${byte + 2}`, name: component.name, label: `${component.name} · caudal`, data_type: "REAL", unit: component.unit });
        byte += 6;
      } else {
        if (component.type !== "remote_input") points.push({ address: `DB${component.db}.DBD${byte}`, name: component.name, label: component.name, data_type: "REAL", unit: component.unit });
        byte += 4;
      }
    } else {
      if (component.type !== "remote_input") points.push({ address: `DB${component.db}.DBX${byte}.${bit}`, name: component.name, label: component.name, data_type: "BOOL" });
      bit += 1;
      if (bit === 8) { byte += 1; bit = 0; }
    }
      cursors.set(key, [byte, bit]);
  }
  return points;
}
