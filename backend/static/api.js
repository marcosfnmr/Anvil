const API_ROOT = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join(" · ")
      : detail || payload || `Error HTTP ${response.status}`;
    throw new Error(message);
  }

  return payload;
}

export const scenarioApi = {
  health: () => request("/health"),
  list: () => request("/scenarios"),
  get: (scenarioId) => request(`/scenarios/${scenarioId}`),
  create: (payload) =>
    request("/scenarios", { method: "POST", body: JSON.stringify(payload) }),
  update: (scenarioId, payload) =>
    request(`/scenarios/${scenarioId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  remove: (scenarioId) => request(`/scenarios/${scenarioId}`, { method: "DELETE" }),
  deploy: (scenarioId) => request(`/scenarios/${scenarioId}/deploy`, { method: "POST" }),
  stop: (scenarioId) => request(`/scenarios/${scenarioId}/stop`, { method: "POST" }),
  status: (scenarioId) => request(`/scenarios/${scenarioId}/status`),
};
