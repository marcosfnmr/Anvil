import assert from "node:assert/strict";
import { scadaTrend } from "../backend/static/scada-trend.mjs";

const start = "2026-10-09T10:00:00.000Z";
const buckets = [
  { timestamp: start, average: 10, good_samples: 1, bad_samples: 0 },
  { timestamp: "2026-10-09T10:00:05.000Z", average: 20, good_samples: 1, bad_samples: 0 },
  { timestamp: "2026-10-09T10:00:10.000Z", average: null, good_samples: 0, bad_samples: 1 },
  { timestamp: "2026-10-09T10:00:15.000Z", average: 30, good_samples: 1, bad_samples: 0 },
  { timestamp: "2026-10-09T10:00:20.000Z", average: 35, good_samples: 1, bad_samples: 0 },
  { timestamp: "2026-10-09T10:00:50.000Z", average: 40, good_samples: 1, bad_samples: 0 },
  { timestamp: "2026-10-09T10:00:55.000Z", average: 45, good_samples: 1, bad_samples: 0 },
];
const html = scadaTrend({ buckets }, [], { node_id: "plc", address: "DB1.DBD0" }, {
  start, end: "2026-10-09T10:01:00.000Z", bucket_seconds: 5,
});

assert.match(html, /6 intervalos buenos/);
assert.match(html, /huecos o intervalos descartados/);
assert.match(html, /aria-label="Tendencia de 6 intervalos buenos; los huecos no se interpolan"/);
assert.equal((html.match(/<polyline /g) || []).length, 3, "bad-quality and missing buckets split the trend into separate segments");
assert.doesNotMatch(html, /NaN|Infinity|null/);

const fallback = scadaTrend(null, [
  { timestamp: start, values: [{ node_id: "plc", address: "DB1.DBD0", value: 12, quality: "bad" }] },
], { node_id: "plc", address: "DB1.DBD0" }, { start, end: "2026-10-09T10:01:00.000Z", bucket_seconds: 5 });
assert.match(fallback, /no hay lecturas numéricas válidas de calidad buena/);

console.log("SCADA trend quality and gaps: OK");
