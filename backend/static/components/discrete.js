import { registerComponentType } from "./registry.js?v=field-live-3";

for (const [type, label, icon, writable] of [
  ["motor", "Motor", "M", true],
  ["digital_input", "Entrada digital", "DI", false],
  ["alarm", "Alarma", "AL", false],
]) {
  registerComponentType(type, {
    label,
    icon,
    deviceTypes: ["plc-s7"],
    create(index = 1) {
      return { type, name: `${type}_${index}`, db: 1, initial_state: false };
    },
    fields: [
      { path: "name", label: "Nombre", type: "text", full: true },
      { path: "db", label: "DB", type: "number", min: 1 },
      { path: "initial_state", label: "Estado inicial", type: "checkbox" },
    ],
    writable,
  });
}
