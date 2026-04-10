/**
 * Tab visibility polling for the Logos Node Observer dashboard.
 *
 * - Starts immediately on load if the tab is already visible.
 * - When the tab becomes visible: immediate poll + start 5s interval.
 * - When the tab becomes hidden: clear the interval.
 *
 * Exported for unit testing; imported by index.html.
 */

let _refreshInterval = null;
let _handler = null;

/**
 * Start (or restart) visibility-change polling.
 *
 * @param {Function} refreshFn  - async function to call to fetch latest data
 * @param {number}   intervalMs - polling interval in ms (default 5000)
 * @returns {Function} cleanup  - call to remove listener and stop interval
 */
export function initVisibilityPolling(refreshFn, intervalMs = 5000) {
  // Clean up any prior listener (defensive)
  if (_handler) {
    document.removeEventListener('visibilitychange', _handler);
    _handler = null;
  }
  if (_refreshInterval !== null) {
    clearInterval(_refreshInterval);
    _refreshInterval = null;
  }

  function startPolling() {
    refreshFn(); // immediate poll
    _refreshInterval = setInterval(refreshFn, intervalMs);
  }

  function stopPolling() {
    if (_refreshInterval !== null) {
      clearInterval(_refreshInterval);
      _refreshInterval = null;
    }
  }

  _handler = () => {
    if (document.hidden) {
      stopPolling();
    } else {
      startPolling();
    }
  };

  document.addEventListener('visibilitychange', _handler);

  // Start immediately on load if tab is already visible
  if (!document.hidden) {
    startPolling();
  }

  return function cleanup() {
    document.removeEventListener('visibilitychange', _handler);
    _handler = null;
    stopPolling();
  };
}
