/**
 * Turning snapshot rows into chart series for the Logos Node Observer dashboard.
 *
 * A node that could not be read leaves no snapshot row, so its history has
 * holes. Charts must show those as gaps, never as a line drawn across them.
 *
 * Exported for unit testing; imported by index.html.
 */

/**
 * Build chart points from snapshot rows (oldest first).
 *
 * @param {Array<Object>} rows        - snapshot rows, each with a `timestamp` (epoch seconds)
 * @param {Array<Function>} valueFns  - one per series: (row, prevRow) => number | null.
 *                                      prevRow is null at the start and after a gap.
 * @param {number} maxGapSecs         - rows further apart than this have a gap between them
 * @returns {{timestamps: Array<number|null>, series: Array<Array<number|null>>}}
 *          A gap is one extra point whose timestamp and values are all null.
 */
export function buildSeries(rows, valueFns, maxGapSecs) {
  const timestamps = [];
  const series = valueFns.map(() => []);
  let prev = null;
  for (const row of rows) {
    if (prev && row.timestamp - prev.timestamp > maxGapSecs) {
      timestamps.push(null);
      for (const s of series) s.push(null);
      prev = null;
    }
    timestamps.push(row.timestamp);
    valueFns.forEach((fn, i) => {
      const v = fn(row, prev);
      series[i].push(v === undefined || Number.isNaN(v) ? null : v);
    });
    prev = row;
  }
  return { timestamps, series };
}

/** Series value: a plain column. */
export function field(name) {
  return (row) => row[name] ?? null;
}

/**
 * Series value: how much a counter grew since the previous row.
 *
 * Null when there is no previous row (start, or after a gap) and when the
 * counter went down, which means the node restarted and counts from zero again.
 */
export function delta(name) {
  return (row, prev) => {
    if (!prev || row[name] == null || prev[name] == null) return null;
    const d = row[name] - prev[name];
    return d < 0 ? null : d;
  };
}

/** 1536 -> "1.5 KiB". */
export function fmtBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return null;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let v = Number(n);
  let u = 0;
  while (Math.abs(v) >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${u === 0 ? v : v.toFixed(v < 10 ? 2 : 1)} ${units[u]}`;
}

/** 93784 -> "1d 2h"; 754 -> "12m"; 42 -> "42s". */
export function fmtDuration(secs) {
  if (secs == null || Number.isNaN(Number(secs))) return null;
  const s = Math.max(0, Math.floor(Number(secs)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}
