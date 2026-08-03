"""Shared helpers for optional upstream social CLIs.

Security rules:
- Never collect, print, or persist cookies/tokens/profiles.
- Never auto-read browser cookie stores.
- Warnings and errors must be actionable without leaking secrets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_SECRET_PATTERNS = (
    re.compile(r"(?i)(auth[_-]?token|ct0|cookie|authorization|bearer)\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"(?i)(TWITTER_AUTH_TOKEN|TWITTER_CT0)\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"(?i)(auth_token|ct0)=([^\s;]+)"),
)


def trim_text(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    space = cut.rfind(" ", 0, max_chars)
    return ((cut[:space] if space >= int(max_chars * 0.5) else cut[: max_chars - 1]) + "…").strip()


def redact_secrets(text: str) -> str:
    """Redact credential-looking substrings from tool output used in warnings."""
    value = text or ""
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    return value


def which(command: str) -> str | None:
    return shutil.which(command)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(
    argv: Sequence[str],
    *,
    timeout: int = 30,
    env: Mapping[str, str] | None = None,
    runner: Any | None = None,
) -> CommandResult:
    """Run an upstream CLI without mutating the parent environment."""
    if runner is not None:
        return runner(list(argv), timeout=timeout, env=dict(env) if env is not None else None)

    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout)),
        env=dict(env) if env is not None else None,
        check=False,
    )
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def parse_json_payload(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty JSON payload")
    return json.loads(text)


def child_env_without_browser_cookie_hints(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a child env that prefers explicit credentials and avoids browser hints.

    Does not invent credentials. Strips twitter-cli browser-selection hints so the
    upstream tool is less likely to fall back to automatic browser cookie reads
    when explicit env credentials are incomplete.
    """
    env = dict(base if base is not None else os.environ)
    for key in ("TWITTER_BROWSER", "TWITTER_CHROME_PROFILE"):
        env.pop(key, None)
    return env


def actionable_missing_tool(tool: str, install_hint: str) -> str:
    return f"{tool} not available on PATH; {install_hint}"
