export const NODE_REGISTRY = new Map();

export function registerNodeType(type, definition) {
  if (NODE_REGISTRY.has(type)) {
    throw new Error(`El tipo de nodo '${type}' ya está registrado`);
  }
  NODE_REGISTRY.set(type, { type, ...definition });
}

export function getNodeType(type) {
  const definition = NODE_REGISTRY.get(type);
  if (!definition) {
    throw new Error(`Tipo de nodo no soportado: ${type}`);
  }
  return definition;
}

export function listNodeTypes() {
  return [...NODE_REGISTRY.values()];
}
