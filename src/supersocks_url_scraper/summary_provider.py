from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SummaryProviderError(RuntimeError):
    """Raised when an optional external summary provider cannot return a usable summary."""


def summarize_with_provider(
    *,
    provider: str | None,
    text: str,
    length: int,
    url: str = "",
    title: str | None = None,
    content_type: str = "article",
    endpoint: str | None = None,
    token: str | None = None,
    timeout: int = 30,
) -> str | None:
    """Return an external summary, or None when local summarization should be used.

    The public package ships no provider credentials and enables no external
    provider by default. The only built-in provider is a generic HTTP adapter so
    operators can wire their own internal/OpenAI-compatible/etc. summarizer
    without this package depending on a specific SDK or private key.
    """
    selected = (provider or "local").strip().lower()
    if selected in {"", "local", "extractive", "none"}:
        return None
    if selected != "http":
        raise SummaryProviderError(f"unsupported summary provider: {provider}")
    if not endpoint:
        raise SummaryProviderError("SUMMARY_PROVIDER_URL is required for summary_provider=http")
    if not text.strip():
        raise SummaryProviderError("cannot summarize empty content")

    payload = {
        "url": url,
        "title": title,
        "content_type": content_type,
        "length": int(length),
        "content": text,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain;q=0.9, */*;q=0.1",
        "User-Agent": "supersocks-url-scraper/summary-provider",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=max(1, int(timeout))) as response:
            raw = response.read(2_000_000)
            ctype = response.headers.get("content-type", "").lower()
    except HTTPError as exc:
        raise SummaryProviderError(f"summary provider HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise SummaryProviderError(f"summary provider failed: {type(exc).__name__}") from exc

    body = raw.decode("utf-8", errors="replace").strip()
    if not body:
        raise SummaryProviderError("summary provider returned empty response")
    if "json" in ctype or body[:1] in {"{", "["}:
        try:
            data: Any = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SummaryProviderError("summary provider returned invalid JSON") from exc
        if isinstance(data, dict):
            summary = data.get("summary") or data.get("text") or data.get("result")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
        raise SummaryProviderError("summary provider JSON must contain a non-empty summary/text/result string")
    return body
