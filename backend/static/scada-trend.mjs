const finite = (value) => typeof value === "number" && Number.isFinite(value);

/** Build an SVG trend without hiding bad quality or missing time intervals. */
export function scadaTrend(series, samples, point, range = {}) {
  const parsedStart = Date.parse(range.start || "");
  const parsedEnd = Date.parse(range.end || "");
  const end = Number.isFinite(parsedEnd) ? parsedEnd : Date.now();
  const start = Number.isFinite(parsedStart) ? parsedStart : end - 10 * 60 * 1000;
  const intervalMs = Math.max(1, Number(range.bucket_seconds) || 5) * 1000;
  const buckets = series?.buckets;
  const observations = Array.isArray(buckets)
    ? buckets.map((bucket) => ({
      timestamp: Date.parse(bucket.timestamp),
      value: bucket.average,
      good: Number(bucket.good_samples) > 0 && finite(bucket.average),
    }))
    : (samples || []).flatMap((sample) => {
      const item = sample.values?.find((value) => value.node_id === point.node_id && value.address === point.address);
      const timestamp = Date.parse(sample.timestamp);
      return item && finite(item.value) ? [{ timestamp, value: item.value, good: item.quality === "good" }] : [];
    });

  const inWindow = observations.filter((item) => Number.isFinite(item.timestamp) && item.timestamp >= start && item.timestamp <= end).sort((a, b) => a.timestamp - b.timestamp);
  const valid = inWindow.filter((item) => item.good);
  if (!valid.length) return "<small>Sin tendencia: no hay lecturas numéricas válidas de calidad buena en los últimos 10 min.</small>";

  const min = Math.min(...valid.map((item) => item.value));
  const max = Math.max(...valid.map((item) => item.value));
  const span = Math.max(1e-9, max - min);
  const segments = [];
  let segment = [];
  let previous;
  for (const item of inWindow) {
    const continuous = previous && item.timestamp - previous.timestamp <= intervalMs * 1.5;
    if (!item.good || (previous && !continuous)) {
      if (segment.length) segments.push(segment);
      segment = [];
    }
    if (item.good) segment.push(item);
    previous = item;
  }
  if (segment.length) segments.push(segment);

  const polylines = segments.filter((part) => part.length >= 2).map((part) => {
    const coords = part.map((item) => {
      const x = ((item.timestamp - start) / (end - start)) * 180;
      const y = 34 - ((item.value - min) / span) * 28;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return `<polyline points="${coords}" />`;
  }).join("");
  const gaps = Math.max(0, inWindow.length - valid.length) + Math.max(0, segments.length - 1);
  const shape = polylines || valid.map((item) => {
    const x = ((item.timestamp - start) / (end - start)) * 180;
    const y = 34 - ((item.value - min) / span) * 28;
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.8" />`;
  }).join("");
  return `<small>Tendencia · media por intervalos de ${Math.round(intervalMs / 1000)} s · ${Math.round((end - start) / 60000)} min · ${valid.length} intervalos buenos${gaps ? ` · ${gaps} huecos o intervalos descartados` : ""}</small><svg class="scada-trend" viewBox="0 0 180 36" role="img" aria-label="Tendencia de ${valid.length} intervalos buenos; los huecos no se interpolan">${shape}</svg>`;
}
