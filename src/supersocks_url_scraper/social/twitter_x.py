"""X/Twitter reads via upstream twitter-cli when explicitly authenticated.

Policy:
- Use twitter-cli only when the binary is on PATH.
- Require explicit TWITTER_AUTH_TOKEN + TWITTER_CT0 in the process environment.
- Never auto-read browser cookies, never print/store tokens, never invent credentials.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable
from urllib.parse import urlparse

from .backend import (
    actionable_missing_tool,
    child_env_without_browser_cookie_hints,
    parse_json_payload,
    redact_secrets,
    run_command,
    trim_text,
    which,
)
from .domains import detect_platform, is_safe_public_http_url

TWITTER_CLI_INSTALL = (
    "install from GitHub/PyPI via `pipx install twitter-cli` or "
    "`uv tool install twitter-cli` (never auto-installed by this package)"
)

CommandRunner = Callable[..., Any]

_STATUS_RE = re.compile(r"/(?:i/)?status/(\d+)", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"/(?:i/)?article/(\d+)", re.IGNORECASE)
_RESERVED_USER_PATHS = frozenset(
    {
        "home",
        "explore",
        "search",
        "notifications",
        "messages",
        "settings",
        "i",
        "intent",
        "share",
        "hashtag",
        "compose",
        "login",
        "signup",
        "tos",
        "privacy",
    }
)


def twitter_cli_available() -> bool:
    return which("twitter") is not None


def explicit_twitter_credentials_present(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    auth = str(env.get("TWITTER_AUTH_TOKEN") or "").strip()
    ct0 = str(env.get("TWITTER_CT0") or "").strip()
    return bool(auth and ct0)


def twitter_missing_backend_warning() -> str:
    return actionable_missing_tool("twitter-cli (`twitter`)", TWITTER_CLI_INSTALL)


def twitter_missing_credentials_warning() -> str:
    return (
        "twitter-cli is installed but TWITTER_AUTH_TOKEN and TWITTER_CT0 are not both set; "
        "export them from a manual Cookie-Editor export. This package never auto-reads browser cookies."
    )


def classify_x_url(url: str) -> tuple[str, str | None]:
    """Return (kind, identifier) for status|article|user|unknown."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    article = _ARTICLE_RE.search(path)
    if article:
        return "article", article.group(1)
    status = _STATUS_RE.search(path)
    if status:
        return "status", status.group(1)
    parts = [p for p in path.split("/") if p]
    if parts and parts[0].lower() not in _RESERVED_USER_PATHS and not parts[0].isdigit():
        handle = parts[0].lstrip("@")
        if re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            return "user", handle
    return "unknown", None


def _author_name(data: dict[str, Any]) -> str | None:
    author = data.get("author")
    if isinstance(author, dict):
        for key in ("name", "screen_name", "screenName", "username"):
            value = str(author.get(key) or "").strip()
            if value:
                return value
    for key in ("name", "screen_name", "screenName", "username"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return None


def _tweet_text(data: dict[str, Any]) -> str:
    for key in ("articleText", "article_text", "full_text", "fullText", "text", "bio", "description"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _tweet_title(data: dict[str, Any], *, fallback_author: str | None = None) -> str | None:
    for key in ("articleTitle", "article_title", "title", "name"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    text = _tweet_text(data)
    if text:
        return trim_text(text, 80)
    if fallback_author:
        return f"@{fallback_author}" if not fallback_author.startswith("@") else fallback_author
    return None


def _unwrap_data(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is False:
        return None
    data = payload.get("data", payload)
    if isinstance(data, list):
        if not data:
            return None
        first = data[0]
        return first if isinstance(first, dict) else None
    return data if isinstance(data, dict) else None


def _error_message(payload: Any, stderr: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "").strip()
            message = str(err.get("message") or "").strip()
            joined = ": ".join(p for p in (code, message) if p)
            if joined:
                return redact_secrets(joined)
        if payload.get("ok") is False:
            return redact_secrets(str(payload.get("message") or "twitter-cli returned ok=false"))
    if stderr.strip():
        return redact_secrets(stderr.strip().splitlines()[0][:300])
    return "twitter-cli returned no usable data"


def extract_x(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 30,
    runner: CommandRunner | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Extract an X/Twitter URL via twitter-cli, or return an actionable failure payload."""
    if detect_platform(url) != "x" or not is_safe_public_http_url(url):
        return None

    max_chars = max(50, min(int(length or 900), 10_000))
    base = {
        "url": url,
        "content_type": "article",
        "title": None,
        "summary": "",
        "length": max_chars,
        "fetch_method": "twitter-cli",
        "platform": "x",
        "warnings": [],
    }

    if not twitter_cli_available() and runner is None:
        payload = dict(base)
        payload["status"] = "error"
        payload["warnings"] = [twitter_missing_backend_warning()]
        return payload

    env = child_env_without_browser_cookie_hints(environ)
    if not explicit_twitter_credentials_present(env):
        payload = dict(base)
        payload["status"] = "error"
        payload["warnings"] = [twitter_missing_credentials_warning()]
        return payload

    kind, ident = classify_x_url(url)
    if kind == "article":
        argv = ["twitter", "article", ident or url, "--json"]
    elif kind == "status":
        argv = ["twitter", "tweet", ident or url, "--json"]
    elif kind == "user" and ident:
        argv = ["twitter", "user", ident, "--json"]
    else:
        payload = dict(base)
        payload["status"] = "partial"
        payload["warnings"] = [
            "unsupported X/Twitter URL shape for twitter-cli; expected status, article, or profile URL"
        ]
        return payload

    try:
        result = run_command(argv, timeout=timeout, env=env, runner=runner)
    except Exception as exc:  # noqa: BLE001 - surface as warning, never raise secrets
        payload = dict(base)
        payload["status"] = "error"
        payload["warnings"] = [f"twitter-cli execution failed: {redact_secrets(str(exc))}"]
        return payload

    parsed: Any = None
    try:
        parsed = parse_json_payload(result.stdout)
    except Exception:
        parsed = None

    data = _unwrap_data(parsed)
    if result.returncode != 0 or data is None:
        payload = dict(base)
        payload["status"] = "error"
        payload["warnings"] = [_error_message(parsed, result.stderr)]
        return payload

    author = _author_name(data)
    body = _tweet_text(data)
    title = _tweet_title(data, fallback_author=author or ident)
    summary = trim_text(body or title or "", max_chars)
    published = str(data.get("created_at") or data.get("createdAt") or "").strip() or None

    out: dict[str, Any] = {
        "url": url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": "twitter-cli",
        "status": "ok" if summary else "partial",
        "warnings": [] if summary else ["twitter-cli returned metadata without readable text"],
        "platform": "x",
        "author": author,
        "published_at": published,
    }
    if include_content:
        out["content"] = body or ""
    return out
