"""Social routing entrypoints for YouTube and LinkedIn MVP channels."""

from __future__ import annotations

from typing import Any, Callable

from .domains import detect_platform
from .jina import fetch_jina_reader
from .linkedin import extract_linkedin
from .youtube import extract_youtube, yt_dlp_available

ReadUrlFn = Callable[..., dict[str, Any]]
HtmlFetcher = Callable[..., dict[str, Any]]


def _result_has_useful_text(result: dict[str, Any]) -> bool:
    summary = str(result.get("summary") or "").strip()
    content = str(result.get("content") or "").strip()
    return len(summary) >= 80 or len(content) >= 80


def try_social_read(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 20,
    jina_fallback: bool = False,
    generic_read: ReadUrlFn | None = None,
    generic_kwargs: dict[str, Any] | None = None,
    ydl_factory: Any | None = None,
    subtitle_fetcher: Any | None = None,
    jina_opener: Any | None = None,
    html_fetcher: HtmlFetcher | None = None,
) -> dict[str, Any] | None:
    """Attempt a social-specific read.

    Returns:
    - a completed social payload when handled
    - None when the URL is not a supported social host or YouTube should fall
      through to the generic pipeline (missing yt-dlp)
    """
    platform = detect_platform(url)
    if platform is None:
        return None

    if platform == "youtube":
        if ydl_factory is None and not yt_dlp_available():
            return None
        return extract_youtube(
            url,
            length=length,
            include_content=include_content,
            timeout=timeout,
            ydl_factory=ydl_factory,
            subtitle_fetcher=subtitle_fetcher,
        )

    if platform == "linkedin":
        specialized = extract_linkedin(
            url,
            length=length,
            include_content=include_content,
            timeout=timeout,
            html_fetcher=html_fetcher,
        )
        specialized = dict(specialized)
        specialized["platform"] = "linkedin"

        gate_hit = any(
            "authwall" in str(w).lower() or "challenge" in str(w).lower() or "navigation/cta" in str(w).lower()
            for w in (specialized.get("warnings") or [])
        )
        if specialized.get("status") == "ok" and _result_has_useful_text(specialized):
            return specialized

        result = specialized

        # Generic pipeline is a last resort only (never for clear authwall/challenge shells
        # unless specialized produced no payload at all).
        if generic_read is not None and not gate_hit:
            kwargs = dict(generic_kwargs or {})
            # Prevent recursive social routing when generic_read is read_url.
            kwargs["skip_social_routing"] = True
            generic = dict(generic_read(url, **kwargs))
            generic["platform"] = "linkedin"
            generic_warnings = list(generic.get("warnings") or [])
            generic["warnings"] = list(result.get("warnings") or []) + generic_warnings + [
                "generic pipeline used as LinkedIn last resort"
            ]
            if generic.get("status") == "ok" and _result_has_useful_text(generic) and not any(
                "authwall" in str(w).lower() or "challenge" in str(w).lower() for w in generic_warnings
            ):
                # Preserve specialized page typing when available.
                if result.get("linkedin_page_type") and "linkedin_page_type" not in generic:
                    generic["linkedin_page_type"] = result["linkedin_page_type"]
                if result.get("structured_data") and "structured_data" not in generic:
                    generic["structured_data"] = result["structured_data"]
                result = generic
            elif _result_has_useful_text(generic) and not _result_has_useful_text(result):
                if result.get("linkedin_page_type") and "linkedin_page_type" not in generic:
                    generic["linkedin_page_type"] = result["linkedin_page_type"]
                if result.get("structured_data") and "structured_data" not in generic:
                    generic["structured_data"] = result["structured_data"]
                # Never promote gated/poor specialized failures to ok via generic chrome.
                if generic.get("status") == "ok" and not _result_has_useful_text(generic):
                    generic["status"] = "partial"
                result = generic
            else:
                # Keep specialized payload; fold useful generic warnings.
                result["warnings"] = list(result.get("warnings") or []) + [
                    w for w in generic_warnings if w not in (result.get("warnings") or [])
                ]

        if jina_fallback and result.get("status") in {"error", "partial"}:
            jina_result = fetch_jina_reader(
                url,
                length=length,
                include_content=include_content,
                timeout=timeout,
                platform="linkedin",
                opener=jina_opener,
            )
            # Prefer Jina when it produced readable content; otherwise keep prior result.
            if jina_result.get("status") in {"ok", "partial"} and (
                jina_result.get("summary") or jina_result.get("content")
            ):
                merged_warnings = list(result.get("warnings") or []) + list(jina_result.get("warnings") or [])
                jina_result["warnings"] = merged_warnings
                if result.get("linkedin_page_type"):
                    jina_result["linkedin_page_type"] = result["linkedin_page_type"]
                if result.get("structured_data"):
                    jina_result["structured_data"] = result["structured_data"]
                return jina_result
            if jina_result.get("warnings"):
                result["warnings"] = list(result.get("warnings") or []) + list(jina_result["warnings"])
        return result

    return None


def youtube_missing_dependency_warning() -> str:
    return "yt-dlp not installed; falling back to generic pipeline (install optional extra: youtube)"
