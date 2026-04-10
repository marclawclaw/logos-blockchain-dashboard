/**
 * Tests for tab visibility polling behaviour (refresh.js).
 *
 * Spec requirements verified:
 *   1. Polls every 5 s when visible
 *   2. Cancels on hidden
 *   3. Immediate poll on visible return
 *   4. Starts immediately on load if visible
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { initVisibilityPolling } from '../../dashboard/static/refresh.js';

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Set document.hidden and dispatch visibilitychange. */
function setHidden(hidden) {
  Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });
  document.dispatchEvent(new Event('visibilitychange'));
}

// ─── Lifecycle ────────────────────────────────────────────────────────────────

/** cleanup handle returned by initVisibilityPolling */
let cleanup;

beforeEach(() => {
  vi.useFakeTimers();
  // Default: tab visible
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});

afterEach(() => {
  if (cleanup) cleanup();
  cleanup = undefined;
  vi.useRealTimers();
});

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('initVisibilityPolling', () => {

  it('calls refreshFn immediately on init when tab is visible', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('does NOT call refreshFn on init when tab is hidden', () => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).not.toHaveBeenCalled();
  });

  it('polls every 5 s while tab stays visible', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    // 1 call from immediate poll on init
    expect(fn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('cancels polling when tab becomes hidden', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).toHaveBeenCalledTimes(1); // init

    // Hide the tab
    setHidden(true);

    // Advance well past next interval — should NOT fire
    vi.advanceTimersByTime(15000);
    expect(fn).toHaveBeenCalledTimes(1); // still 1
  });

  it('fires an immediate poll when tab returns to visible', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).toHaveBeenCalledTimes(1); // init

    setHidden(true);
    vi.advanceTimersByTime(10000); // no calls while hidden
    expect(fn).toHaveBeenCalledTimes(1);

    setHidden(false); // return to visible
    expect(fn).toHaveBeenCalledTimes(2); // immediate poll
  });

  it('resumes 5 s interval after returning to visible', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).toHaveBeenCalledTimes(1); // init

    setHidden(true);
    setHidden(false); // immediate poll → 2
    expect(fn).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(3);

    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(4);
  });

  it('does not accumulate intervals on rapid hide/show toggles', () => {
    const fn = vi.fn();
    cleanup = initVisibilityPolling(fn, 5000);
    expect(fn).toHaveBeenCalledTimes(1); // init

    // Rapid toggle 5 times
    for (let i = 0; i < 5; i++) {
      setHidden(true);
      setHidden(false);
    }
    // 1 init + 5 immediate polls on each show
    expect(fn).toHaveBeenCalledTimes(6);

    fn.mockClear();

    // After 5 s only ONE interval tick should fire (not 5 stacked)
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
