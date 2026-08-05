"""Opt-in Reddit fallback via upstream rdt-cli.

Disabled by default. Never auto-installs the tool, never auto-reads browser
cookies, and never prints credentials. Enable only with ``RDT_CLI_FALLBACK=1``.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from .backend import CommandResult, actionable_missing_tool, parse_json_payload, redact_secrets, run_command, trim_text, which
from .domains import detect_platform, is_safe_public_http_url

CommandRunner = Callable[..., CommandResult]

RDT_INSTALL_HINT = (
    "install upstream rdt-cli yourself and put `rdt` on PATH "
    "(never auto-installed; no cookies auto-read)"
)


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def rdt_cli_available() -> bool:
    return which("rdt") is not None


def rdt_cli_fallback_enabled(
    explicit: bool | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    env = environ if environ is not None else os.environ
    return _truthy(env.get("RDT_CLI_FALLBACK"), False)


def extract_reddit_rdt(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 30,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    if detect_platform(url) != "reddit" or not is_safe_public_http_url(url):
        return None
    if not rdt_cli_fallback_enabled(enabled, environ=environ):
        return None

    max_chars = max(50, min(int(length or 900), 10_000))
    if runner is None and not rdt_cli_available():
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "rdt-cli",
            "status": "error",
            "warnings": [actionable_missing_tool("rdt-cli", RDT_INSTALL_HINT)],
            "platform": "reddit",
        }

    # Child env strips browser cookie helper hints if any ever appear.
    env = dict(environ if environ is not None else os.environ)
    for key in ("RDT_BROWSER", "RDT_COOKIE_FILE", "REDDIT_COOKIE_FILE"):
        env.pop(key, None)

    try:
        result = run_command(
            ["rdt", "read", url, "--json"],
            timeout=timeout,
            env=env,
            runner=runner,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "rdt-cli",
            "status": "error",
            "warnings": [f"rdt-cli execution failed: {redact_secrets(str(exc))}"],
            "platform": "reddit",
        }

    if result.returncode != 0:
        err = redact_secrets((result.stderr or result.stdout or "rdt-cli failed").strip().splitlines()[0][:300])
        warnings = [err]
        lower = err.lower()
        if any(token in lower for token in ("auth", "login", "captcha", "cookie")):
            warnings.append(
                "rdt-cli could not read the post without interactive auth; "
                "do not auto-supply cookies — log in manually in the upstream tool if required."
            )
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "rdt-cli",
            "status": "error",
            "warnings": warnings,
            "platform": "reddit",
        }

    try:
        payload = parse_json_payload(result.stdout)
    except Exception:
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "rdt-cli",
            "status": "error",
            "warnings": ["rdt-cli returned non-JSON output"],
            "platform": "reddit",
        }

    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    title = str(data.get("title") or data.get("name") or "").strip() or None
    body = str(data.get("selftext") or data.get("text") or data.get("body") or data.get("content") or "").strip()
    author = str(data.get("author") or data.get("username") or "").strip() or None
    published_at = str(data.get("created_at") or data.get("created_utc") or data.get("published_at") or "").strip() or None
    summary = trim_text(body or title or "", max_chars)
    out: dict[str, Any] = {
        "url": url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": "rdt-cli",
        "status": "ok" if summary else "partial",
        "warnings": [] if summary else ["rdt-cli returned no readable text"],
        "platform": "reddit",
        "author": author,
        "published_at": published_at,
    }
    if include_content:
        out["content"] = body or ""
    # Ensure secrets never leak even if upstream echoes them.
    serialized = str(out)
    if any(token in serialized.lower() for token in ("auth_token", "cookie=", "bearer ")):
        out["warnings"] = list(out.get("warnings") or []) + ["rdt-cli payload redacted untrusted secret-like fields"]
        out["summary"] = redact_secrets(out.get("summary") or "")
        if "content" in out:
            out["content"] = redact_secrets(str(out.get("content") or ""))
    return out
