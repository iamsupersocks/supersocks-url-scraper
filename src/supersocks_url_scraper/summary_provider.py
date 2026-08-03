from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
DEFAULT_KIMI_MODEL = "kimi-k2.5"


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
    model: str | None = None,
) -> str | None:
    """Return an external summary, or None when local summarization should be used.

    The public package ships no provider credentials and enables no external
    provider by default. Built-in adapters are a generic HTTP endpoint and an
    opt-in OpenAI-compatible Kimi/Moonshot client. Neither path depends on a
    vendor SDK. Kimi is never called unless ``provider=kimi``.
    """
    selected = (provider or "local").strip().lower()
    if selected in {"", "local", "extractive", "none"}:
        return None
    if not text.strip():
        raise SummaryProviderError("cannot summarize empty content")
    if selected == "http":
        return _summarize_with_http(
            text=text,
            length=length,
            url=url,
            title=title,
            content_type=content_type,
            endpoint=endpoint,
            token=token,
            timeout=timeout,
        )
    if selected == "kimi":
        return _summarize_with_kimi(
            text=text,
            length=length,
            endpoint=endpoint,
            token=token,
            timeout=timeout,
            model=model,
        )
    raise SummaryProviderError(f"unsupported summary provider: {provider}")


def _summarize_with_http(
    *,
    text: str,
    length: int,
    url: str,
    title: str | None,
    content_type: str,
    endpoint: str | None,
    token: str | None,
    timeout: int,
) -> str:
    if not endpoint:
        raise SummaryProviderError("SUMMARY_PROVIDER_URL is required for summary_provider=http")

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
    raw, ctype = _post_json(request, timeout=timeout, label="summary provider")

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


def _summarize_with_kimi(
    *,
    text: str,
    length: int,
    endpoint: str | None,
    token: str | None,
    timeout: int,
    model: str | None,
) -> str:
    api_key = (token or os.environ.get("KIMI_API_KEY") or "").strip()
    if not api_key:
        raise SummaryProviderError("KIMI_API_KEY is required for summary_provider=kimi")

    api_url = (
        (endpoint or "").strip()
        or (os.environ.get("KIMI_API_URL") or "").strip()
        or (os.environ.get("SUMMARY_PROVIDER_URL") or "").strip()
        or DEFAULT_KIMI_API_URL
    )
    selected_model = (
        (model or "").strip()
        or (os.environ.get("KIMI_MODEL") or "").strip()
        or DEFAULT_KIMI_MODEL
    )
    max_chars = max(1, int(length))
    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a summarization assistant. Summarize only the user-provided text. "
                    f"Return a plain-text summary of at most {max_chars} characters. "
                    "Do not invent facts that are not present in the text. "
                    "Do not fetch or scrape URLs. Do not add commentary outside the summary."
                ),
            },
            {"role": "user", "content": text.strip()},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "supersocks-url-scraper/summary-provider",
    }
    request = Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    raw, _ctype = _post_json(request, timeout=timeout, label="kimi provider")

    body = raw.decode("utf-8", errors="replace").strip()
    if not body:
        raise SummaryProviderError("kimi provider returned empty response")
    try:
        data: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SummaryProviderError("kimi provider returned invalid JSON") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SummaryProviderError("kimi provider JSON missing choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise SummaryProviderError("kimi provider returned empty choices[0].message.content")
    return content.strip()


def _post_json(request: Request, *, timeout: int, label: str) -> tuple[bytes, str]:
    try:
        with urlopen(request, timeout=max(1, int(timeout))) as response:
            raw = response.read(2_000_000)
            ctype = response.headers.get("content-type", "").lower()
            return raw, ctype
    except HTTPError as exc:
        raise SummaryProviderError(f"{label} HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SummaryProviderError(f"{label} failed: {type(exc).__name__}") from exc
