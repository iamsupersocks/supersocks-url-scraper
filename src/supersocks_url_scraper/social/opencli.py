"""OpenCLI backend probing and command runner.

OpenCLI reuses a user-controlled Chrome session via its Browser Bridge extension.
This module never runs `opencli doctor` (side effects), never reads cookies, and
never stores browser profiles.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .backend import CommandResult, redact_secrets, run_command, which

OPENCLI_PACKAGE = "@jackwener/opencli"
OPENCLI_EXTENSION_URL = "https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk"
OPENCLI_INSTALL = (
    f"install from GitHub/npm: `npm install -g {OPENCLI_PACKAGE}` "
    "(Node >= 20; never auto-installed by this package; no ZIP/PyPI extra)"
)
_DAEMON_STATUS_URL = "http://127.0.0.1:19825/status"
_MAX_DAEMON_STATUS_BYTES = 64 * 1024
_UNSUPPORTED_APP_ENV = ("OPENCLI_DAEMON_PORT",)

CommandRunner = Callable[..., CommandResult]


@dataclass(frozen=True)
class OpenCLIStatus:
    installed: bool = False
    broken: bool = False
    daemon_running: bool = False
    extension_connected: bool = False
    version: str = ""
    hint: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and not self.broken and self.extension_connected


def opencli_available() -> bool:
    return which("opencli") is not None


def fetch_daemon_status(*, timeout: int = 2, opener: Any | None = None) -> dict[str, Any] | None:
    """Read OpenCLI loopback /status without starting the CLI."""
    request = urllib.request.Request(
        _DAEMON_STATUS_URL,
        headers={"X-OpenCLI": "1"},
        method="GET",
    )
    built = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with built.open(request, timeout=min(timeout, 2)) as response:
            raw = response.read(_MAX_DAEMON_STATUS_BYTES + 1)
    except Exception:
        return None
    if len(raw) > _MAX_DAEMON_STATUS_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    return payload


def opencli_child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    import os

    env = dict(base if base is not None else os.environ)
    for key in _UNSUPPORTED_APP_ENV:
        env.pop(key, None)
    return env


def probe_opencli(
    *,
    timeout: int = 8,
    runner: CommandRunner | None = None,
    daemon_fetcher: Callable[..., dict[str, Any] | None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> OpenCLIStatus:
    """Probe install + extension connection without `opencli doctor` side effects."""
    if runner is None and not opencli_available():
        return OpenCLIStatus(
            installed=False,
            hint=f"OpenCLI not available on PATH; {OPENCLI_INSTALL}. Then install/enable the Chrome extension: {OPENCLI_EXTENSION_URL}",
        )

    env = opencli_child_env(environ)
    try:
        version_result = run_command(
            ["opencli", "--version"],
            timeout=timeout,
            env=env,
            runner=runner,
        )
    except Exception as exc:  # noqa: BLE001
        return OpenCLIStatus(
            installed=True,
            broken=True,
            hint=f"opencli is on PATH but failed to execute: {redact_secrets(str(exc))}",
        )

    if version_result.returncode != 0:
        return OpenCLIStatus(
            installed=True,
            broken=True,
            hint=(
                "opencli exists but `--version` failed; repair with "
                f"`npm install -g {OPENCLI_PACKAGE}` (Node >= 20)"
            ),
        )

    version = (version_result.stdout or version_result.stderr or "").strip().splitlines()
    version_text = version[0].strip() if version else ""
    status = OpenCLIStatus(installed=True, version=version_text)

    daemon = (daemon_fetcher or fetch_daemon_status)(timeout=2)
    if daemon is None:
        return OpenCLIStatus(
            installed=True,
            version=version_text,
            daemon_running=False,
            extension_connected=False,
            hint=(
                "OpenCLI is installed but the Browser Bridge daemon is not reachable on "
                "127.0.0.1:19825. Keep Chrome open with the OpenCLI extension enabled, "
                f"then retry. Extension: {OPENCLI_EXTENSION_URL}"
            ),
        )

    connected = bool(daemon.get("extensionConnected"))
    if connected:
        return OpenCLIStatus(
            installed=True,
            version=version_text,
            daemon_running=True,
            extension_connected=True,
            hint="OpenCLI bridge connected (uses your existing Chrome login session only)",
        )

    return OpenCLIStatus(
        installed=True,
        version=version_text,
        daemon_running=True,
        extension_connected=False,
        hint=(
            "OpenCLI daemon is running but the Chrome extension is not connected. "
            f"Install/enable OpenCLI in Chrome ({OPENCLI_EXTENSION_URL}), keep the browser "
            "open, and ensure you are logged into the target site."
        ),
    )


def run_opencli(
    argv: Sequence[str],
    *,
    timeout: int = 45,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
) -> CommandResult:
    env = opencli_child_env(environ)
    return run_command(["opencli", *list(argv)], timeout=timeout, env=env, runner=runner)
