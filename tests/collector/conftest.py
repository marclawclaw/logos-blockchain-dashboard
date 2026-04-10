"""conftest: shared fixtures for collector tests."""

from __future__ import annotations

import pytest

# No autouse patches — each test file manages its own mocking.
# The daemon tests use run_collector_for_test.py for subprocess-level mocking.
