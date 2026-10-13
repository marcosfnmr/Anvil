import { registerComponentType } from "./registry.js?v=field-live-3";

registerComponentType("coil", {
  deviceTypes: ["modbus-tcp"],
  label: "Coil / salida digital", icon: "CO",
  create(index = 1) { return { type: "coil", name: `coil_${index}`, address: null, initial_value: false }; },
  fields: [
    { path: "name", label: "Nombre", type: "text", full: true },
    { path: "address", label: "Dirección (auto si vacío)", type: "number", min: 0, max: 65535 },
    { path: "initial_value", label: "Estado inicial", type: "checkbox" },
  ],
});

registerComponentType("discrete_input", {
  deviceTypes: ["modbus-tcp"],
  label: "Discrete input (solo lectura)", icon: "DI",
  create(index = 1) { return { type: "discrete_input", name: `discrete_input_${index}`, address: null, initial_value: false }; },
  fields: [
    { path: "name", label: "Nombre", type: "text", full: true },
    { path: "address", label: "Dirección (auto si vacío)", type: "number", min: 0, max: 65535 },
    { path: "initial_value", label: "Estado inicial", type: "checkbox" },
  ],
});

for (const [type, label, icon] of [
  ["holding_register", "Holding register (R/W)", "HR"],
  ["input_register", "Input register (solo lectura)", "IR"],
]) {
  registerComponentType(type, {
    deviceTypes: ["modbus-tcp"],
    label, icon,
    create(index = 1) { return { type, name: `${type}_${index}`, address: null, initial_value: 0, unit: "" }; },
    fields: [
      { path: "name", label: "Nombre", type: "text", full: true },
      { path: "address", label: "Dirección (auto si vacío)", type: "number", min: 0, max: 65535 },
      { path: "initial_value", label: "Valor inicial (0–65535)", type: "number", min: 0, max: 65535 },
      { path: "unit", label: "Unidad", type: "text" },
      { path: "min_value", label: "Límite inferior (opcional)", type: "number", min: 0, max: 65535 },
      { path: "max_value", label: "Límite superior (opcional)", type: "number", min: 0, max: 65535 },
    ],
  });
}
