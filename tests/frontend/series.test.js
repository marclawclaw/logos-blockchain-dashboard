/**
 * Tests for chart series building (series.js).
 *
 * Requirements verified:
 *   1. A missed interval is a gap in the chart, never a line across it
 *   2. Counters are charted as growth per interval
 *   3. A counter that restarts from zero leaves a gap, not a negative spike
 */

import { describe, it, expect } from 'vitest';
import { buildSeries, field, delta, fmtBytes, fmtDuration } from '../../dashboard/static/series.js';

const GAP = 1500; // 2.5 x the 10-minute interval

describe('buildSeries', () => {
  it('maps rows to one array per series', () => {
    const rows = [
      { timestamp: 600, peers: 3, blocks: 10 },
      { timestamp: 1200, peers: 4, blocks: 12 },
    ];
    const out = buildSeries(rows, [field('peers'), field('blocks')], GAP);
    expect(out.timestamps).toEqual([600, 1200]);
    expect(out.series).toEqual([[3, 4], [10, 12]]);
  });

  it('inserts a null point where snapshots are missing', () => {
    const rows = [
      { timestamp: 600, peers: 3 },
      { timestamp: 1200, peers: 4 },
      { timestamp: 7200, peers: 9 }, // node was down for over an hour
    ];
    const out = buildSeries(rows, [field('peers')], GAP);
    expect(out.timestamps).toEqual([600, 1200, null, 7200]);
    expect(out.series[0]).toEqual([3, 4, null, 9]);
  });

  it('does not treat one late snapshot as a gap', () => {
    const rows = [{ timestamp: 600, peers: 3 }, { timestamp: 1800, peers: 4 }];
    expect(buildSeries(rows, [field('peers')], GAP).timestamps).toEqual([600, 1800]);
  });

  it('keeps a value the node did not report as null', () => {
    const rows = [{ timestamp: 600, peers: null }, { timestamp: 1200 }];
    expect(buildSeries(rows, [field('peers')], GAP).series[0]).toEqual([null, null]);
  });

  it('handles no rows', () => {
    expect(buildSeries([], [field('peers')], GAP)).toEqual({ timestamps: [], series: [[]] });
  });
});

describe('delta', () => {
  const run = (rows) => buildSeries(rows, [delta('sent_total')], GAP).series[0];

  it('charts growth since the previous snapshot', () => {
    expect(run([
      { timestamp: 600, sent_total: 100 },
      { timestamp: 1200, sent_total: 130 },
      { timestamp: 1800, sent_total: 130 },
    ])).toEqual([null, 30, 0]);
  });

  it('leaves a gap when the counter restarts from zero', () => {
    expect(run([
      { timestamp: 600, sent_total: 500 },
      { timestamp: 1200, sent_total: 20 }, // node restarted
      { timestamp: 1800, sent_total: 50 },
    ])).toEqual([null, null, 30]);
  });

  it('does not spread growth across missing snapshots', () => {
    expect(run([
      { timestamp: 600, sent_total: 100 },
      { timestamp: 7200, sent_total: 900 },
      { timestamp: 7800, sent_total: 950 },
    ])).toEqual([null, null, null, 50]);
  });

  it('is null when either side is unknown', () => {
    expect(run([
      { timestamp: 600, sent_total: null },
      { timestamp: 1200, sent_total: 5 },
      { timestamp: 1800 },
    ])).toEqual([null, null, null]);
  });
});

describe('formatting', () => {
  it('formats bytes', () => {
    expect(fmtBytes(0)).toBe('0 B');
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(1536)).toBe('1.50 KiB');
    expect(fmtBytes(21474836480)).toBe('20.0 GiB');
    expect(fmtBytes(null)).toBe(null);
  });

  it('formats durations', () => {
    expect(fmtDuration(42)).toBe('42s');
    expect(fmtDuration(754)).toBe('12m');
    expect(fmtDuration(4056)).toBe('1h 7m');
    expect(fmtDuration(93784)).toBe('1d 2h');
    expect(fmtDuration(undefined)).toBe(null);
  });
});
