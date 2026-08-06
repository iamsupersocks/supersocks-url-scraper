"""Opt-in Firecrawl v2 scrape/parse OCR client (stdlib HTTP only).

Safety:
- never enabled by API key presence alone
- never used for credentialed, non-HTTP(S), localhost, or private/internal hosts
- validates URL before the request; callers should also pass the post-redirect final URL
- never logs or returns the API key
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..social.domains import is_safe_public_http_url
from .models import DocumentContent, FirecrawlOcrError
from .detect import title_from_markdown

DEFAULT_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2"
DEFAULT_MAX_PAGES = 50
DEFAULT_TIMEOUT_SECONDS = 60


def firecrawl_api_key(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get("FIRECRAWL_API_KEY") or "").strip()


def firecrawl_scrape_url(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get("FIRECRAWL_API_URL") or DEFAULT_FIRECRAWL_SCRAPE_URL).strip() or DEFAULT_FIRECRAWL_SCRAPE_URL


def resolve_document_mode(value: str | None = None, *, environ: dict[str, str] | None = None) -> str:
    """Return local|auto|firecrawl. Default local — key alone never activates cloud OCR."""
    env = environ if environ is not None else os.environ
    raw = (value if value is not None else env.get("DOCUMENT_MODE", "local") or "local").strip().lower()
    if raw in {"local", "auto", "firecrawl"}:
        return raw
    return "local"


def firecrawl_ocr_allowed(mode: str, *, api_key: str) -> bool:
    """Cloud OCR requires an explicit mode (auto/firecrawl) AND a configured key."""
    return mode in {"auto", "firecrawl"} and bool(api_key.strip())


def assert_safe_cloud_url(*urls: str) -> None:
    for url in urls:
        if not url:
            continue
        if not is_safe_public_http_url(url):
            raise FirecrawlOcrError(
                "firecrawl OCR blocked: unsafe URL (credentials, non-HTTP(S), localhost, or private/internal host)",
                kind="blocked",
            )


def _redact_secrets(text: str, api_key: str) -> str:
    out = text or ""
    if api_key and api_key in out:
        out = out.replace(api_key, "[redacted]")
    return out


def scrape_pdf_ocr(
    url: str,
    *,
    api_key: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    scrape_endpoint: str = DEFAULT_FIRECRAWL_SCRAPE_URL,
    mode: str = "ocr",
    final_url: str | None = None,
    opener: Callable[..., Any] | None = None,
) -> DocumentContent:
    """Call Firecrawl v2 scrape with PDF parser OCR. Stdlib HTTP only."""
    assert_safe_cloud_url(url, final_url or "")
    target = final_url or url
    assert_safe_cloud_url(target)
    if not api_key.strip():
        raise FirecrawlOcrError("firecrawl OCR unavailable: API key not configured", kind="auth")

    pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), 10_000))
    body = {
        "url": target,
        "formats": ["markdown"],
        "parsers": [{"type": "pdf", "mode": mode if mode in {"fast", "auto", "ocr"} else "ocr", "maxPages": pages}],
        "timeout": max(1000, min(int(timeout * 1000), 300_000)),
    }
    request = Request(
        scrape_endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    open_fn = opener or urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            raw = response.read(8_000_000)
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2048).decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        detail = _redact_secrets(detail, api_key)
        if exc.code in {401, 403}:
            raise FirecrawlOcrError(f"firecrawl OCR auth failed (HTTP {exc.code})", kind="auth") from exc
        if exc.code == 429:
            raise FirecrawlOcrError("firecrawl OCR quota exceeded (HTTP 429)", kind="quota") from exc
        raise FirecrawlOcrError(f"firecrawl OCR HTTP {exc.code}: {detail[:200]}", kind="network") from exc
    except TimeoutError as exc:
        raise FirecrawlOcrError("firecrawl OCR timed out", kind="timeout") from exc
    except (URLError, OSError) as exc:
        raise FirecrawlOcrError(f"firecrawl OCR network error: {type(exc).__name__}", kind="network") from exc

    if status and int(status) >= 400:
        raise FirecrawlOcrError(f"firecrawl OCR HTTP {status}", kind="network")

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise FirecrawlOcrError("firecrawl OCR returned malformed JSON", kind="malformed") from exc

    if not isinstance(payload, dict):
        raise FirecrawlOcrError("firecrawl OCR returned malformed payload", kind="malformed")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise FirecrawlOcrError("firecrawl OCR returned malformed data", kind="malformed")

    markdown = data.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        # Some responses nest under metadata-less success wrappers.
        raise FirecrawlOcrError("firecrawl OCR returned empty markdown", kind="malformed")

    text = markdown.strip()
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    title = None
    if isinstance(meta.get("title"), str) and meta["title"].strip():
        title = meta["title"].strip()
    else:
        title = title_from_markdown(text)

    page_count = None
    for key in ("pagesParsed", "pageCount", "page_count", "numPages"):
        value = meta.get(key) if key in meta else data.get(key)
        if isinstance(value, int) and value > 0:
            page_count = value
            break

    return DocumentContent(
        title=title,
        text=text,
        format="pdf",
        method="firecrawl",
        page_count=page_count,
        pdf_classification=None,
        ocr_used=True,
        ocr_provider="firecrawl",
        warnings=("external OCR used: firecrawl",),
    )
