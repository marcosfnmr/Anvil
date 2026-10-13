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

export function configureNodeCatalog(catalog) {
  for (const metadata of catalog) {
    const definition = NODE_REGISTRY.get(metadata.type);
    if (definition) Object.assign(definition, metadata);
  }
}

export function getNodeCatalog() {
  return [...NODE_REGISTRY.values()].map(({ create, render, className, ...metadata }) => metadata);
}
