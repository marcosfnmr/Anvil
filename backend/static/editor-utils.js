// Pure editor helpers shared with browser regression tests.
export function nextAvailableIp(subnet, usedAddresses = []) {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(subnet.trim());
  if (!match) throw new Error("Indica una subred IPv4, por ejemplo 172.30.100.0/24.");
  const parts = match.slice(1, 5).map(Number);
  const prefix = Number(match[5]);
  if (parts.some((part) => part > 255) || prefix > 30) throw new Error("La subred debe tener direcciones disponibles para los dispositivos.");
  const raw = parts.reduce((sum, part) => sum * 256 + part, 0);
  const size = 2 ** (32 - prefix);
  const network = Math.floor(raw / size) * size;
  if (network !== raw) throw new Error("La dirección debe ser el inicio de la subred.");
  const used = new Set(usedAddresses);
  const format = (value) => [24, 16, 8, 0].map((shift) => Math.floor(value / 2 ** shift) % 256).join(".");
  // Reserve network, broadcast and the Docker gateway (.1); prefer .10 onward.
  const start = Math.min(10, size - 2);
  for (const [first, last] of [[start, size - 2], [2, start - 1]]) {
    for (let offset = first; offset <= last; offset += 1) {
      const candidate = format(network + offset);
      if (!used.has(candidate)) return candidate;
    }
  }
  throw new Error("No quedan direcciones libres en esta subred.");
}
