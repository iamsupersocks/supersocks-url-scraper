"""Opt-in Jina Reader fallback for public HTTP(S) URLs only.

Safety rules:
- disabled by default
- never used for credentialed URLs, non-HTTP(S), or private/local hosts
- never forwards caller headers, cookies, or tokens
"""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .domains import is_safe_public_http_url

JINA_READER_PREFIX = "https://r.jina.ai/"
DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2"


def _trim(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    space = cut.rfind(" ", 0, max_chars)
    return ((cut[:space] if space >= int(max_chars * 0.5) else cut[: max_chars - 1]) + "…").strip()


def _extract_title(text: str) -> str | None:
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("# "):
            return value[2:].strip() or None
        if value.startswith("Title:"):
            return value.split(":", 1)[1].strip() or None
    return None


def fetch_jina_reader(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 20,
    platform: str | None = None,
    opener: Any | None = None,
) -> dict[str, Any]:
    """Fetch a normalized result through Jina Reader, or an error payload."""
    max_chars = max(50, min(int(length or 900), 10_000))
    if not is_safe_public_http_url(url):
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "jina",
            "status": "error",
            "warnings": ["jina fallback blocked: unsafe URL (credentials, non-HTTP(S), or private/local host)"],
            **({"platform": platform} if platform else {}),
        }

    reader_url = JINA_READER_PREFIX + quote(url, safe=":/?&=%#")
    request = Request(
        reader_url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/plain, text/markdown, */*;q=0.1",
        },
    )
    try:
        open_fn = opener or urlopen
        with open_fn(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
        text = raw.decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "jina",
            "status": "error",
            "warnings": [f"jina reader HTTP {exc.code}", "external reader used: jina"],
            **({"platform": platform} if platform else {}),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "jina",
            "status": "error",
            "warnings": [f"jina reader failed: {type(exc).__name__}", "external reader used: jina"],
            **({"platform": platform} if platform else {}),
        }

    if not text:
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "jina",
            "status": "partial",
            "warnings": ["jina reader returned empty body", "external reader used: jina"],
            **({"platform": platform} if platform else {}),
        }

    title = _extract_title(text)
    summary = _trim(text, max_chars)
    payload: dict[str, Any] = {
        "url": url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": "jina",
        "status": "ok" if summary else "partial",
        "warnings": ["external reader used: jina"],
    }
    if platform:
        payload["platform"] = platform
    if include_content:
        payload["content"] = text
    return payload
