import { registerComponentType } from "./registry.js?v=field-live-3";

registerComponentType("pump", {
  label: "Bomba con caudal",
  icon: "P",
  deviceTypes: ["plc-s7"],
  create(index = 1) {
    return { type: "pump", name: `bomba_${index}`, db: 1, initial_state: false, flow_min: 0, flow_max: 100, unit: "L/min" };
  },
  fields: [
    { path: "name", label: "Nombre", type: "text", full: true },
    { path: "db", label: "DB", type: "number", min: 1 },
    { path: "initial_state", label: "En marcha al iniciar", type: "checkbox" },
    { path: "flow_min", label: "Caudal mínimo", type: "number", min: 0, step: "any" },
    { path: "flow_max", label: "Caudal máximo", type: "number", min: 0, step: "any" },
    { path: "unit", label: "Unidad de caudal", type: "text" },
  ],
});
