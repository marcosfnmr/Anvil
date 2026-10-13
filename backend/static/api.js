const API_ROOT = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => {
        const location = Array.isArray(item?.loc)
          ? item.loc.filter((part, index) => !(index === 0 && ["body", "query", "path", "header", "cookie"].includes(part)))
            .reduce((path, part) => typeof part === "number"
              ? `${path}[${part}]`
              : path ? `${path}.${part}` : String(part), "")
          : "";
        const reason = item?.msg || "Valor no válido";
        return location ? `${location}: ${reason}` : reason;
      }).join(" · ")
      : detail || payload || `Error HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

export const scenarioApi = {
  health: () => request("/health"),
  catalog: () => request("/scenarios/catalog"),
  templates: () => request("/templates"),
  publishTemplate: (payload) => request("/templates", { method: "POST", body: JSON.stringify(payload) }),
  updateTemplate: (templateId, payload) => request(`/templates/${encodeURIComponent(templateId)}`, { method: "PUT", body: JSON.stringify(payload) }),
  templateRevisions: (templateId) => request(`/templates/${encodeURIComponent(templateId)}/revisions`),
  restoreTemplateRevision: (templateId, version) => request(`/templates/${encodeURIComponent(templateId)}/revisions/${version}/restore`, { method: "POST" }),
  deleteTemplate: (templateId) => request(`/templates/${encodeURIComponent(templateId)}`, { method: "DELETE" }),
  componentTemplates: () => request("/components/templates"),
  publishComponentTemplate: (payload) => request("/components/templates", { method: "POST", body: JSON.stringify(payload) }),
  updateComponentTemplate: (presetId, payload) => request(`/components/templates/${encodeURIComponent(presetId)}`, { method: "PUT", body: JSON.stringify(payload) }),
  componentTemplateRevisions: (presetId) => request(`/components/templates/${encodeURIComponent(presetId)}/revisions`),
  restoreComponentTemplateRevision: (presetId, version) => request(`/components/templates/${encodeURIComponent(presetId)}/revisions/${version}/restore`, { method: "POST" }),
  deleteComponentTemplate: (presetId) => request(`/components/templates/${encodeURIComponent(presetId)}`, { method: "DELETE" }),
  instantiateTemplate: (templateId) => request(`/templates/${encodeURIComponent(templateId)}/instantiate`, { method: "POST" }),
  list: () => request("/scenarios"),
  get: (scenarioId) => request(`/scenarios/${scenarioId}`),
  revisions: (scenarioId) => request(`/scenarios/${scenarioId}/revisions`),
  revision: (scenarioId, revisionId) => request(`/scenarios/${scenarioId}/revisions/${revisionId}`),
  restoreRevision: (scenarioId, revisionId) => request(`/scenarios/${scenarioId}/revisions/${revisionId}/restore`, { method: "POST" }),
  create: (payload) =>
    request("/scenarios", { method: "POST", body: JSON.stringify(payload) }),
  importScenario: (payload) =>
    request("/scenarios/import", { method: "POST", body: JSON.stringify(payload) }),
  clone: (scenarioId, name) =>
    request(`/scenarios/${scenarioId}/clone`, { method: "POST", body: JSON.stringify({ name }) }),
  migrateLegacyFieldCopy: (scenarioId, name) =>
    request(`/scenarios/${scenarioId}/field-migration-copy`, { method: "POST", body: JSON.stringify({ name }) }),
  exportScenario: (scenarioId) => request(`/scenarios/${scenarioId}/export`),
  exportDeviceProfile: (scenarioId, nodeId) => request(`/scenarios/${scenarioId}/devices/${encodeURIComponent(nodeId)}/profile`),
  importDeviceProfile: (scenarioId, payload) => request(`/scenarios/${scenarioId}/devices/import`, { method: "POST", body: JSON.stringify(payload) }),
  update: (scenarioId, payload) =>
    request(`/scenarios/${scenarioId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  remove: (scenarioId) => request(`/scenarios/${scenarioId}`, { method: "DELETE" }),
  deploy: (scenarioId) => request(`/scenarios/${scenarioId}/deploy`, { method: "POST" }),
  pause: (scenarioId) => request(`/scenarios/${scenarioId}/pause`, { method: "POST" }),
  resume: (scenarioId) => request(`/scenarios/${scenarioId}/resume`, { method: "POST" }),
  stop: (scenarioId) => request(`/scenarios/${scenarioId}/stop`, { method: "POST" }),
  restart: (scenarioId) => request(`/scenarios/${scenarioId}/restart`, { method: "POST" }),
  nodeLifecycle: (scenarioId, nodeId, action) => request(`/scenarios/${scenarioId}/nodes/${encodeURIComponent(nodeId)}/lifecycle`, { method: "POST", body: JSON.stringify({ action }) }),
  status: (scenarioId) => request(`/scenarios/${scenarioId}/status`),
  ioMap: (scenarioId) => request(`/scenarios/${scenarioId}/io-map`),
  diagnostics: (scenarioId) => request(`/scenarios/${scenarioId}/diagnostics`),
  networkRoutes: (scenarioId) => request(`/scenarios/${scenarioId}/network-routes`),
  services: (scenarioId) => request(`/scenarios/${scenarioId}/services`),
  logs: (scenarioId) => request(`/scenarios/${scenarioId}/logs`),
  members: (scenarioId) => request(`/scenarios/${scenarioId}/members`),
  grantMember: (scenarioId, email, role) => request(`/scenarios/${scenarioId}/members`, { method: "POST", body: JSON.stringify({ email, role }) }),
  removeMember: (scenarioId, userId) => request(`/scenarios/${scenarioId}/members/${encodeURIComponent(userId)}`, { method: "DELETE" }),
};
