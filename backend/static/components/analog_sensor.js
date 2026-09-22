import { registerComponentType } from "./registry.js";

registerComponentType("analog_sensor", {
  label: "Sensor analógico",
  icon: "AI",
  create(index = 1) {
    return {
      type: "analog_sensor",
      name: `sensor_${index}`,
      db: 1,
      unit: "bar",
      min_value: 0,
      max_value: 10,
      simulation: {
        mode: "sine_wave",
        period_seconds: 30,
        noise: 0,
      },
    };
  },
  fields: [
    { path: "name", label: "Nombre", type: "text", full: true },
    { path: "db", label: "DB", type: "number", min: 1 },
    { path: "unit", label: "Unidad", type: "text" },
    { path: "min_value", label: "Mínimo", type: "number", step: "any" },
    { path: "max_value", label: "Máximo", type: "number", step: "any" },
    {
      path: "simulation.mode",
      label: "Simulación",
      type: "select",
      options: [
        { value: "constant", label: "Constante" },
        { value: "sine_wave", label: "Onda senoidal" },
      ],
      full: true,
    },
    {
      path: "simulation.period_seconds",
      label: "Periodo (s)",
      type: "number",
      min: 0.1,
      step: "any",
    },
    {
      path: "simulation.noise",
      label: "Ruido (0–1)",
      type: "number",
      min: 0,
      max: 1,
      step: 0.01,
    },
  ],
});
