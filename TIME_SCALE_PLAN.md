# Time Scale Selector — Implementation Plan

## Overview

Add a time scale selector button group to the dashboard header. When the user selects a time scale (1d / 1w / 1m / Max), the historical charts clear and re-fetch from `GET /api/snapshots?hours=N`. The live panel (5s refresh) is unaffected.

## What Exists

- `GET /api/snapshots?hours=N` already implemented in `dashboard/api.py` — no backend changes needed
- Frontend (`index.html`) has hardcoded `const HOURS = 72;` and calls `fetchHistory()` once at init

## Files to Change

| File | Change |
|------|--------|
| `dashboard/templates/index.html` | Add button group in header; replace hardcoded `HOURS` with reactive state; wire up `onChange` to clear charts + re-fetch |
| `tests/dashboard/test_api.py` | Add tests for `GET /api/snapshots?hours=N` with various hour values (1, 24, 168, 720, 0) |

## Frontend Changes (index.html)

### CSS additions
- `.timescale-group` container in header
- `.timescale-btn` button styles (inactive / active states)
- Active button: accent background or border

### State additions
```js
const TIME_SCALES = { '1d': 24, '1w': 168, '1m': 720, 'Max': 0 };
let selectedScale = '1d';  // default
```

### Button group HTML (in header)
```html
<div class="timescale-group">
  <button class="timescale-btn active" 
  <button class="timescale-btn active" data-scale="1d">1d</button>
  <button class="timescale-btn" data-scale="1w">1w</button>
  <button class="timescale-btn" data-scale="1m">1m</button>
  <button class="timescale-btn" data-scale="Max">Max</button>
</div>
```

### JS changes
1. `clearCharts()` — reset all chart datasets to empty arrays + `chart.update('none')`
2. `loadHistory(scale)` — clearCharts() → fetch `${API_BASE}/snapshots?hours=${TIME_SCALES[scale]}` → `updateHistory(data)`
3. Click handlers on `.timescale-btn` — update `selectedScale`, toggle `.active` class, call `loadHistory(scale)`
5. On init: call `loadHistory('1d')` instead of `fetchHistory()`

### updateHistory() changes
- `updateHistory` is called by `loadHistory`; no structural changes needed — it already clears and repopulates charts from the full snapshot list passed to it

## API Tests (tests/dashboard/test_api.py)

Add:
- `test_snapshots_filter_by_1_hour` — `hours=1`
- `test_snapshots_filter_by_24_hours` — `hours=24`
- `test_snapshots_filter_by_1_week` — `hours=168`
- `test_snapshots_filter_by_1_month` — `hours=720`
- `test_snapshots_max_returns_all` — `hours=0` returns all records
- `test_snapshots_default_is_24_hours` — no `hours` param defaults to 24

## Commit Sequence

1. `[spec]` Tighten SPEC.md time scale description → `b0f8409`
2. `[plan]` Add TIME_SCALE_PLAN.md
3. `[ui]` Add time scale selector button group and reactive state to index.html
4. `[test]` Add API tests for `?hours=` parameter
5. `[test]` Run all tests, verify green
6. Push all commits, notify via `openclaw system event`
