import { registerComponentType } from "./registry.js?v=field-live-3";

registerComponentType("valve", {
  label: "Válvula",
  deviceTypes: ["plc-s7"],
  icon: "V",
  create(index = 1) {
    return {
      type: "valve",
      name: `valvula_${index}`,
      db: 1,
      initial_state: false,
    };
  },
  fields: [
    { path: "name", label: "Nombre", type: "text", full: true },
    { path: "db", label: "DB", type: "number", min: 1 },
    { path: "initial_state", label: "Inicial abierta", type: "checkbox" },
  ],
});
