export function escapeNodeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function renderDeviceCard({ type, name, icon, kind, ip, endpointLabel = "IP", metrics = [] }) {
  const items = metrics.map(({ label, value }) => `
    <div class="device-node-metric">
      <span>${escapeNodeText(label)}</span>
      <strong title="${escapeNodeText(value)}">${escapeNodeText(value)}</strong>
    </div>`).join("");

  return `
    <article class="device-node device-node--${escapeNodeText(type)}">
      <header class="device-node-header">
        <span class="device-node-icon" aria-hidden="true">${escapeNodeText(icon)}</span>
        <div class="device-node-title">
          <small>${escapeNodeText(kind)}</small>
          <strong title="${escapeNodeText(name)}">${escapeNodeText(name)}</strong>
        </div>
      </header>
      <div class="device-node-status-row">
        <span class="node-state" title="Estado del contenedor">Sin desplegar</span>
        <span class="device-node-endpoint"><small>${escapeNodeText(endpointLabel)}</small><strong title="${escapeNodeText(ip)}">${escapeNodeText(ip)}</strong></span>
      </div>
      <div class="device-node-body">${items}</div>
    </article>`;
}
