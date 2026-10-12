"""logosctl client: run the Logos Core CLI and parse its JSON output.

Storage and delivery nodes run as modules inside one Logos Core daemon, and
logosctl is the only supported way to query them. Only read-only commands are
issued from here; arguments never come from the browser.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from .config import LogosctlConfig

logger = logging.getLogger(__name__)


class LogosctlError(Exception):
    """A logosctl command failed. `code` is logosctl's own error code when it gave one."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Logosctl:
    def __init__(self, cfg: LogosctlConfig):
        self._command = list(cfg.command)
        self._timeout = cfg.timeout_seconds

    def _run(self, *args: str) -> Any:
        argv = [*self._command, "--json", *args]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self._timeout)
        except FileNotFoundError as e:
            raise LogosctlError("NOT_INSTALLED", f"{self._command[0]} not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise LogosctlError("TIMEOUT", f"no answer within {self._timeout}s") from e

        # Errors are reported as JSON on stdout with a non-zero exit code.
        text = proc.stdout.strip() or proc.stderr.strip()
        try:
            data = json.loads(text)
        except ValueError:
            raise LogosctlError(
                "BAD_OUTPUT", f"exit {proc.returncode}, non-JSON output: {text[:200]!r}"
            ) from None
        if isinstance(data, dict) and data.get("status") == "error":
            raise LogosctlError(data.get("code") or "ERROR", data.get("message") or "")
        if proc.returncode != 0:
            # `status` reports an unreachable daemon in its normal shape, with a reason.
            daemon = data.get("daemon") if isinstance(data, dict) else None
            if isinstance(daemon, dict) and daemon.get("reason"):
                raise LogosctlError("DAEMON_UNREACHABLE", str(daemon["reason"]))
            raise LogosctlError("ERROR", f"exit {proc.returncode}: {text[:200]}")
        return data

    def status(self) -> dict:
        """Daemon health and the status of every module."""
        return self._run("status")

    def stats(self) -> list:
        """Per-module CPU and memory usage."""
        return self._run("stats")

    def call(self, module: str, method: str, *args: str) -> Any:
        """Call a module method and return its result.

        Methods returning a `{success, value, error}` envelope are unwrapped to
        `value`; a false `success` raises.
        """
        result = self._run("call", module, method, *args).get("result")
        if isinstance(result, dict) and "success" in result and "value" in result:
            if not result["success"]:
                raise LogosctlError("METHOD_FAILED", str(result.get("error")))
            return result["value"]
        return result
