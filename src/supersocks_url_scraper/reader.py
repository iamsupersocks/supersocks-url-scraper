from __future__ import annotations

import html
import json
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 12
MAX_BYTES = 1_500_000
DEFAULT_USER_AGENT = "supersocks-url-scraper/0.1 (+https://github.com/supersocks/supersocks-url-scraper)"


@dataclass(frozen=True)
class ReadOptions:
    length: int = 900
    include_content: bool = False
    timeout: int = DEFAULT_TIMEOUT
    max_bytes: int = MAX_BYTES
    user_agent: str = DEFAULT_USER_AGENT


def clean_text(value: object, limit: int | None = None) -> str:
    """Strip HTML tags, unescape entities, normalize whitespace, and optionally truncate."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def meta_content(markup: str, *, name: str | None = None, prop: str | None = None) -> str:
    """Extract a simple meta name/property content value."""
    attr = "name" if name else "property"
    value = name or prop
    if not value:
        return ""

    # Handles both common attribute orders:
    # <meta name="description" content="...">
    # <meta content="..." name="description">
    escaped = re.escape(value)
    patterns = [
        rf"<meta[^>]+{attr}=[\"']{escaped}[\"'][^>]+content=[\"']([^\"']{{1,5000}})[\"']",
        rf"<meta[^>]+content=[\"']([^\"']{{1,5000}})[\"'][^>]+{attr}=[\"']{escaped}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, markup, re.I | re.S)
        if match:
            return clean_text(match.group(1))
    return ""


def iter_jsonld(markup: str) -> list[dict[str, Any]]:
    """Return flattened JSON-LD objects found in the document."""
    out: list[dict[str, Any]] = []
    raw_scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        markup,
        re.I | re.S,
    )
    for raw in raw_scripts:
        try:
            data = json.loads(html.unescape(raw).strip())
        except Exception:
            continue
        stack: list[Any] = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                out.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(x for x in graph if isinstance(x, dict))
            elif isinstance(item, list):
                stack.extend(x for x in item if isinstance(x, dict))
    return out


def extract_jsonld(markup: str, length: int) -> tuple[str, str]:
    """Extract article-ish text and publication date from JSON-LD when present."""
    article_types = {"newsarticle", "article", "blogposting", "report", "techarticle"}
    for item in iter_jsonld(markup):
        raw_type = item.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if not any(str(t).lower() in article_types for t in types):
            continue

        parts = [item.get("description")]
        body = item.get("articleBody")
        if isinstance(body, str):
            parts.append(body[: max(length * 2, 1200)])
        text = clean_text(" ".join(str(x or "") for x in parts), length)
        date = str(item.get("datePublished") or item.get("dateCreated") or item.get("dateModified") or "")
        if len(text) >= 80 or date:
            return text, date
    return "", ""


def extract_title(markup: str) -> str:
    """Extract OpenGraph or HTML title."""
    og = meta_content(markup, prop="og:title")
    if og:
        return og
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    return clean_text(match.group(1), 220) if match else ""


def extract_summary(markup: str, length: int) -> tuple[str, list[str], str]:
    """Extract the best available short readable summary from HTML markup."""
    warnings: list[str] = []
    jsonld_text, jsonld_date = extract_jsonld(markup, length)
    candidates = [
        meta_content(markup, name="description"),
        meta_content(markup, prop="og:description"),
        meta_content(markup, name="twitter:description"),
        jsonld_text,
    ]
    for candidate in candidates:
        if len(candidate) >= 80:
            return clean_text(candidate, length), warnings, jsonld_date

    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", markup, flags=re.I | re.S)
    paragraphs: list[str] = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", body, re.I | re.S):
        text = clean_text(raw)
        if len(text) >= 80:
            paragraphs.append(text)
        if sum(len(p) for p in paragraphs) >= length * 1.5:
            break
    if paragraphs:
        return clean_text("\n\n".join(paragraphs), length), warnings, jsonld_date

    warnings.append("no substantial metadata or paragraph text extracted")
    return clean_text(markup, length), warnings, jsonld_date


def read_url(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    """Fetch a URL and return title, summary, published date, content type, and warnings.

    This reader intentionally does not execute JavaScript. Pages behind login walls,
    bot checks, or heavy client rendering may return partial/boilerplate content.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"status": "error", "warnings": ["invalid http(s) URL"]}

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes + 1)
    except HTTPError as exc:
        return {"status": "error", "url": url, "warnings": [f"HTTP {exc.code}"]}
    except (URLError, TimeoutError, socket.timeout) as exc:
        return {"status": "error", "url": url, "warnings": [f"fetch failed: {type(exc).__name__}"]}

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]

    markup = raw.decode("utf-8", errors="replace")
    bounded_length = max(120, min(int(length or 900), 3000))
    title = extract_title(markup)
    summary, warnings, published = extract_summary(markup, bounded_length)
    if truncated:
        warnings.append("content truncated at reader byte limit")

    status = "ok" if len(summary) >= 80 else "partial"
    payload: dict[str, Any] = {
        "status": status,
        "url": url,
        "title": title,
        "summary": summary,
        "published": published,
        "content_type": content_type,
        "warnings": warnings,
        "reader": "supersocks-url-scraper/0.1",
    }
    if include_content:
        payload["content"] = clean_text(markup, 12000)
    return payload
