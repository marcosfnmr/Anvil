const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const labels = {
  valve: "Válvula", proportional_valve: "Válvula proporcional", pump: "Bomba",
  tank: "Depósito", pipe: "Tubería", flow_splitter: "Distribuidor", flow_mixer: "Colector", motor: "Motor", analog_sensor: "Sensor",
  digital_input: "Entrada digital", alarm: "Alarma",
};

function fieldSummary(field, readings, byId, processConnections, readingsByField) {
  if (readings.length) {
    return readings.map((reading) => {
      const value = reading.data_type === "BOOL"
        ? (reading.value ? "Activo / abierto" : "Inactivo / cerrado")
        : Number.isFinite(Number(reading.value)) ? Number(reading.value).toLocaleString("es-ES", { maximumFractionDigits: 2 }) : "Sin lectura";
      const label = reading.kind === "command" ? "Orden PLC" : "Señal PLC";
      return `<span class="hmi-synoptic-reading">${label}: ${escapeHtml(value)}${reading.unit ? ` ${escapeHtml(reading.unit)}` : ""}<small>${escapeHtml(reading.address)}</small></span>`;
    }).join("");
  }
  if (field.type === "tank") {
    const linkedSensors = processConnections.flatMap((link) => {
      if (link.source?.object_id === field.object_id && ["level", "pressure"].includes(link.source.port)) {
        return [{ id: link.target?.object_id, measure: link.source.port }];
      }
      if (link.target?.object_id === field.object_id && ["level", "pressure"].includes(link.target.port)) {
        return [{ id: link.source?.object_id, measure: link.target.port }];
      }
      return [];
    });
    const measured = linkedSensors.flatMap(({ id, measure }) => (readingsByField.get(id) || []).map((reading) => {
      const value = reading.data_type === "BOOL" ? String(Boolean(reading.value)) : Number.isFinite(Number(reading.value))
        ? Number(reading.value).toLocaleString("es-ES", { maximumFractionDigits: 2 }) : "Sin lectura";
      const label = measure === "level" ? "Nivel medido por PLC" : "Presión medida por PLC";
      return `<span class="hmi-synoptic-reading">${label}: ${escapeHtml(value)}${reading.unit ? ` ${escapeHtml(reading.unit)}` : ""}<small>${escapeHtml(byId.get(id)?.name || id)} · ${escapeHtml(reading.address)}</small></span>`;
    }));
    if (measured.length) return measured.join("");
  }
  return "Sin lectura PLC asociada";
}

/** Render a process view limited to field objects wired to this HMI's selected controllers. */
export function buildHmiSynoptic({ hmi, fieldObjects = [], processConnections = [], signalConnections = [], snapshot = null } = {}) {
  if (!hmi) return "";
  const selectedTargets = new Set(String(hmi.config?.target_node_ids || "").split(",").map((id) => id.trim()).filter(Boolean));
  const byId = new Map(fieldObjects.map((field) => [field.object_id, field]));
  const visible = new Set();
  const values = snapshot?.values || [];
  const readingsByField = new Map();
  for (const link of signalConnections) {
    const fieldEndpoint = link.source?.kind === "field" ? link.source : link.target?.kind === "field" ? link.target : null;
    const deviceEndpoint = link.source?.kind === "device" ? link.source : link.target?.kind === "device" ? link.target : null;
    if (fieldEndpoint && deviceEndpoint && selectedTargets.has(deviceEndpoint.node_id) && byId.has(fieldEndpoint.object_id)) {
      const value = values.find((item) => item.node_id === deviceEndpoint.node_id && item.address === deviceEndpoint.address);
      const readings = readingsByField.get(fieldEndpoint.object_id) || [];
      if (value) readings.push({ ...value, kind: link.source?.kind === "device" ? "command" : "feedback" });
      readingsByField.set(fieldEndpoint.object_id, readings);
      if (value) visible.add(fieldEndpoint.object_id);
    }
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const link of processConnections) {
      const source = link.source?.object_id;
      const target = link.target?.object_id;
      if (visible.has(source) && byId.has(target) && !visible.has(target)) { visible.add(target); changed = true; }
      if (visible.has(target) && byId.has(source) && !visible.has(source)) { visible.add(source); changed = true; }
    }
  }
  const objects = fieldObjects.filter((field) => visible.has(field.object_id));
  if (!objects.length) {
    return `<section class="hmi-synoptic" aria-labelledby="hmi-synoptic-title"><header><h3 id="hmi-synoptic-title">Sinóptico del proceso</h3></header><p>Conecta objetos de campo a un PLC o dispositivo Modbus seleccionado en esta HMI para mostrarlos aquí.</p></section>`;
  }

  const positioned = objects.map((field, index) => ({
    field,
    x: Number.isFinite(field.position?.x) ? field.position.x : index * 300,
    y: Number.isFinite(field.position?.y) ? field.position.y : 120,
  }));
  const minX = Math.min(...positioned.map((item) => item.x));
  const minY = Math.min(...positioned.map((item) => item.y));
  const maxX = Math.max(...positioned.map((item) => item.x));
  const maxY = Math.max(...positioned.map((item) => item.y));
  const width = Math.max(620, Math.ceil(maxX - minX + 270));
  const height = Math.max(300, Math.ceil(maxY - minY + 170));
  const coords = new Map(positioned.map(({ field, x, y }) => [field.object_id, { x: x - minX + 10, y: y - minY + 10 }]));
  const activeProcessLinks = processConnections.filter((link) => visible.has(link.source?.object_id) && visible.has(link.target?.object_id));
  const paths = activeProcessLinks.map((link) => {
    const source = coords.get(link.source.object_id);
    const target = coords.get(link.target.object_id);
    const sx = source.x + 220;
    const sy = source.y + 58;
    const tx = target.x;
    const ty = target.y + 58;
    const bend = Math.max(45, Math.abs(tx - sx) * 0.42);
    const sourceName = byId.get(link.source.object_id)?.name || link.source.object_id;
    const targetName = byId.get(link.target.object_id)?.name || link.target.object_id;
    return `<g class="hmi-process-link"><path d="M${sx},${sy} C${sx + bend},${sy} ${tx - bend},${ty} ${tx},${ty}" marker-end="url(#hmi-process-arrow)"/><text x="${(sx + tx) / 2}" y="${(sy + ty) / 2 - 8}">${escapeHtml(link.source.port)} → ${escapeHtml(link.target.port)}</text><title>${escapeHtml(sourceName)} · ${escapeHtml(source.port)} a ${escapeHtml(targetName)} · ${escapeHtml(link.target.port)}</title></g>`;
  }).join("");
  const cards = positioned.map(({ field }) => {
    const position = coords.get(field.object_id);
    const readings = readingsByField.get(field.object_id) || [];
    const value = fieldSummary(field, readings, byId, processConnections, readingsByField);
    return `<article class="hmi-synoptic-node" data-hmi-object="${escapeHtml(field.object_id)}" style="left:${position.x}px;top:${position.y}px" aria-label="${escapeHtml(labels[field.type] || field.type)} ${escapeHtml(field.name)}"><header><span aria-hidden="true">${escapeHtml(({ tank: "▤", pump: "P", valve: "V", proportional_valve: "%", pipe: "━", flow_splitter: "Y", flow_mixer: "∪", analog_sensor: "◉", motor: "M", digital_input: "DI", alarm: "!" })[field.type] || "OT")}</span><div><strong>${escapeHtml(field.name)}</strong><small>${escapeHtml(labels[field.type] || field.type)}</small></div></header><p>${value}</p><small class="hmi-synoptic-quality">${readings.length ? "Leído en vivo desde PLC/Modbus" : "Sin lectura PLC asociada"}</small></article>`;
  }).join("");
  const relations = activeProcessLinks.map((link) => `<li>${escapeHtml(byId.get(link.source.object_id)?.name)} · ${escapeHtml(link.source.port)} → ${escapeHtml(byId.get(link.target.object_id)?.name)} · ${escapeHtml(link.target.port)}</li>`).join("");
  return `<section class="hmi-synoptic" aria-labelledby="hmi-synoptic-title"><header><div><h3 id="hmi-synoptic-title">Sinóptico del proceso</h3><p>Topología física del modelo con señales leídas por esta HMI a través de los PLC/Modbus configurados. Sin lectura del controlador, no se muestra un valor del objeto.</p></div><span class="hmi-synoptic-legend"><i></i> Conexión física</span></header><div class="hmi-synoptic-viewport" role="group" aria-label="Diagrama del proceso; desplázate horizontal o verticalmente para recorrerlo"><div class="hmi-synoptic-stage" style="--hmi-stage-width:${width}px;--hmi-stage-height:${height}px"><svg class="hmi-synoptic-pipes" viewBox="0 0 ${width} ${height}" aria-hidden="true"><defs><marker id="hmi-process-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>${paths}</svg>${cards}</div></div>${relations ? `<details class="hmi-synoptic-relations"><summary>Conexiones físicas (${activeProcessLinks.length})</summary><ul>${relations}</ul></details>` : "<p>Los elementos aparecen sin conexiones físicas de proceso.</p>"}</section>`;
}
