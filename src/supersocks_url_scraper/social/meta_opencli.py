"""Instagram and Facebook URL reads via upstream OpenCLI.

Uses only the user-controlled Chrome session exposed by OpenCLI's Browser Bridge.
Never collects, prints, or stores cookies/tokens/profiles.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

from .backend import parse_json_payload, redact_secrets, trim_text
from .domains import detect_platform, is_safe_public_http_url
from .opencli import OpenCLIStatus, probe_opencli, run_opencli

CommandRunner = Callable[..., Any]
DaemonFetcher = Callable[..., dict[str, Any] | None]

_IG_RESERVED = frozenset(
    {
        "p",
        "reel",
        "reels",
        "tv",
        "stories",
        "explore",
        "accounts",
        "direct",
        "about",
        "legal",
        "developer",
        "directory",
    }
)
_FB_RESERVED = frozenset(
    {
        "watch",
        "groups",
        "events",
        "marketplace",
        "gaming",
        "permalink.php",
        "story.php",
        "photo.php",
        "login",
        "dialog",
        "share",
        "sharer",
        "people",
        "pages",
        "public",
    }
)


def _profile_handle(url: str, *, platform: str) -> str | None:
    parsed = urlparse(url)
    parts = [unquote(p) for p in (parsed.path or "/").split("/") if p]
    if not parts:
        return None
    first = parts[0]
    reserved = _IG_RESERVED if platform == "instagram" else _FB_RESERVED
    if first.lower() in reserved:
        return None
    if platform == "facebook" and first.lower() == "profile.php":
        return None
    if re.fullmatch(r"[A-Za-z0-9._-]{2,64}", first):
        return first
    return None


def _json_to_text(payload: Any) -> tuple[str | None, str, str | None]:
    """Return (title, body, author) from common OpenCLI JSON shapes."""
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], (dict, list)):
            return _json_to_text(payload["data"])
        if "markdown" in payload or "content" in payload or "text" in payload:
            title = str(payload.get("title") or payload.get("name") or "").strip() or None
            body = str(
                payload.get("markdown")
                or payload.get("content")
                or payload.get("text")
                or payload.get("bio")
                or ""
            ).strip()
            author = str(payload.get("author") or payload.get("username") or payload.get("name") or "").strip() or None
            return title, body, author
        # Profile-like object
        title = str(payload.get("name") or payload.get("full_name") or payload.get("username") or "").strip() or None
        parts: list[str] = []
        for key in ("bio", "description", "about", "text", "caption", "message"):
            value = str(payload.get(key) or "").strip()
            if value:
                parts.append(value)
        # Flatten shallow string fields for sparse objects.
        if not parts:
            for key, value in payload.items():
                if key.lower() in {"id", "pk", "url", "link", "avatar", "profile_pic_url"}:
                    continue
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
        author = str(payload.get("username") or payload.get("name") or "").strip() or None
        return title, "\n".join(parts).strip(), author
    if isinstance(payload, list):
        chunks: list[str] = []
        title = None
        author = None
        for item in payload[:10]:
            item_title, item_body, item_author = _json_to_text(item)
            if item_title and not title:
                title = item_title
            if item_author and not author:
                author = item_author
            if item_body:
                chunks.append(item_body)
            elif item_title:
                chunks.append(item_title)
        return title, "\n\n".join(chunks).strip(), author
    if isinstance(payload, str):
        return None, payload.strip(), None
    return None, "", None


def _failure(url: str, *, platform: str, length: int, warnings: list[str], status: str = "error") -> dict[str, Any]:
    max_chars = max(50, min(int(length or 900), 10_000))
    return {
        "url": url,
        "content_type": "article",
        "title": None,
        "summary": "",
        "length": max_chars,
        "fetch_method": "opencli",
        "status": status,
        "warnings": warnings,
        "platform": platform,
    }


def _build_command(url: str, *, platform: str) -> list[str]:
    handle = _profile_handle(url, platform=platform)
    if handle and platform == "instagram":
        return ["instagram", "profile", handle, "-f", "json"]
    if handle and platform == "facebook":
        return ["facebook", "profile", handle, "-f", "json"]
    # Posts and other shapes: read through the logged-in Chrome session.
    return ["web", "read", "--url", url, "--download-images", "false", "-f", "json"]


def extract_meta_opencli(
    url: str,
    *,
    platform: str,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 45,
    runner: CommandRunner | None = None,
    daemon_fetcher: DaemonFetcher | None = None,
    environ: Mapping[str, str] | None = None,
    status_override: OpenCLIStatus | None = None,
) -> dict[str, Any] | None:
    if platform not in {"instagram", "facebook"}:
        return None
    if detect_platform(url) != platform or not is_safe_public_http_url(url):
        return None

    status = status_override or probe_opencli(
        timeout=min(timeout, 10),
        runner=runner,
        daemon_fetcher=daemon_fetcher,
        environ=environ,
    )
    if not status.installed:
        return _failure(url, platform=platform, length=length, warnings=[status.hint or "OpenCLI not installed"])
    if status.broken:
        return _failure(url, platform=platform, length=length, warnings=[status.hint or "OpenCLI broken"])
    if not status.extension_connected:
        login = "instagram.com" if platform == "instagram" else "facebook.com"
        hint = status.hint or (
            f"OpenCLI extension not connected; enable the bridge and log into {login} in Chrome"
        )
        return _failure(
            url,
            platform=platform,
            length=length,
            warnings=[
                hint,
                f"Log into {login} in the same Chrome profile that has the OpenCLI extension.",
            ],
        )

    argv = _build_command(url, platform=platform)
    try:
        result = run_opencli(argv, timeout=timeout, runner=runner, environ=environ)
    except Exception as exc:  # noqa: BLE001
        return _failure(
            url,
            platform=platform,
            length=length,
            warnings=[f"opencli execution failed: {redact_secrets(str(exc))}"],
        )

    parsed: Any = None
    try:
        parsed = parse_json_payload(result.stdout)
    except Exception:
        # Some opencli builds emit markdown on success without JSON.
        markdown = (result.stdout or "").strip()
        if result.returncode == 0 and markdown and not markdown.lstrip().startswith("{"):
            max_chars = max(50, min(int(length or 900), 10_000))
            summary = trim_text(markdown, max_chars)
            out = {
                "url": url,
                "content_type": "article",
                "title": trim_text(markdown, 80) if markdown else None,
                "summary": summary,
                "length": max_chars,
                "fetch_method": "opencli",
                "status": "ok" if summary else "partial",
                "warnings": [],
                "platform": platform,
            }
            if include_content:
                out["content"] = markdown
            return out
        parsed = None

    if result.returncode != 0 or parsed is None:
        err = redact_secrets((result.stderr or result.stdout or "opencli failed").strip().splitlines()[0][:300])
        lower = err.lower()
        warnings = [err]
        if any(token in lower for token in ("auth", "login", "unauthorized", "checkpoint", "session")):
            site = "instagram.com" if platform == "instagram" else "facebook.com"
            warnings.append(f"OpenCLI could not use a logged-in Chrome session for {site}; log in and retry.")
        return _failure(url, platform=platform, length=length, warnings=warnings)

    title, body, author = _json_to_text(parsed)
    max_chars = max(50, min(int(length or 900), 10_000))
    summary = trim_text(body or title or "", max_chars)
    out: dict[str, Any] = {
        "url": url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": "opencli",
        "status": "ok" if summary else "partial",
        "warnings": [] if summary else ["opencli returned no readable text"],
        "platform": platform,
        "author": author,
    }
    if include_content:
        out["content"] = body or ""
    return out


def extract_instagram(url: str, **kwargs: Any) -> dict[str, Any] | None:
    return extract_meta_opencli(url, platform="instagram", **kwargs)


def extract_facebook(url: str, **kwargs: Any) -> dict[str, Any] | None:
    return extract_meta_opencli(url, platform="facebook", **kwargs)
