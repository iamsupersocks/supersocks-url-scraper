"""Social routing entrypoints for public and opt-in desktop social channels."""

from __future__ import annotations

from typing import Any, Callable

from .cloak_social import (
    extract_cloak_social,
    opencli_fallback_enabled,
)
from .domains import detect_platform
from .jina import fetch_jina_reader
from .linkedin import extract_linkedin
from .meta_opencli import extract_facebook, extract_instagram
from .reddit_rdt import extract_reddit_rdt, rdt_cli_fallback_enabled
from .twitter_x import extract_x
from .youtube import extract_youtube, yt_dlp_available

ReadUrlFn = Callable[..., dict[str, Any]]
HtmlFetcher = Callable[..., dict[str, Any]]
CloakPageFetcher = Callable[..., Any]

SOCIAL_PLATFORMS = ("youtube", "linkedin", "x", "instagram", "facebook", "reddit")
CLOAK_FIRST_PLATFORMS = frozenset({"reddit", "instagram", "facebook"})


def _result_has_useful_text(result: dict[str, Any]) -> bool:
    summary = str(result.get("summary") or "").strip()
    content = str(result.get("content") or "").strip()
    return len(summary) >= 80 or len(content) >= 80


def _merge_warnings(primary: dict[str, Any], secondary: dict[str, Any]) -> list[str]:
    merged: list[str] = []
    for warning in list(primary.get("warnings") or []) + list(secondary.get("warnings") or []):
        text = str(warning)
        if text and text not in merged:
            merged.append(text)
    return merged


def _cloak_first_social(
    url: str,
    *,
    platform: str,
    length: int,
    include_content: bool,
    timeout: int,
    browser_profile_dir: str,
    browser_post_load_wait_ms: int,
    browser_max_concurrency: int,
    headless: bool | None,
    cloak_fetcher: CloakPageFetcher | None,
    opencli_fallback: bool | None,
    rdt_cli_fallback: bool | None,
    opencli_runner: Any | None,
    opencli_daemon_fetcher: Any | None,
    rdt_runner: Any | None,
) -> dict[str, Any]:
    cloak = extract_cloak_social(
        url,
        platform=platform,
        length=length,
        include_content=include_content,
        timeout=max(timeout, 45),
        browser_profile_dir=browser_profile_dir,
        browser_post_load_wait_ms=browser_post_load_wait_ms,
        browser_max_concurrency=browser_max_concurrency,
        headless=headless,
        cloak_fetcher=cloak_fetcher,
    )
    if cloak is None:
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max(50, min(int(length or 900), 10_000)),
            "fetch_method": "cloak",
            "status": "error",
            "warnings": [f"unsupported cloak-first platform: {platform}"],
            "platform": platform,
        }

    if cloak.get("status") == "ok" and _result_has_useful_text(cloak):
        return cloak

    gate_hit = any(
        any(token in str(w).lower() for token in ("login", "captcha", "challenge", "consent", "auth wall"))
        for w in (cloak.get("warnings") or [])
    )

    # OpenCLI is desktop opt-in only — never automatic by default.
    if platform in {"instagram", "facebook"} and opencli_fallback_enabled(opencli_fallback):
        opencli = (
            extract_instagram(
                url,
                length=length,
                include_content=include_content,
                timeout=max(timeout, 45),
                runner=opencli_runner,
                daemon_fetcher=opencli_daemon_fetcher,
            )
            if platform == "instagram"
            else extract_facebook(
                url,
                length=length,
                include_content=include_content,
                timeout=max(timeout, 45),
                runner=opencli_runner,
                daemon_fetcher=opencli_daemon_fetcher,
            )
        )
        if opencli is not None:
            opencli = dict(opencli)
            opencli["warnings"] = _merge_warnings(cloak, opencli) + [
                "opencli used as opt-in desktop fallback after Cloak-first social route"
            ]
            if opencli.get("status") == "ok" and _result_has_useful_text(opencli):
                return opencli
            if _result_has_useful_text(opencli) and not _result_has_useful_text(cloak):
                return opencli
            cloak = dict(cloak)
            cloak["warnings"] = _merge_warnings(cloak, opencli)
    elif platform in {"instagram", "facebook"} and cloak.get("status") in {"error", "partial"}:
        cloak = dict(cloak)
        cloak["warnings"] = list(cloak.get("warnings") or []) + [
            "OpenCLI desktop fallback is opt-in only; set SOCIAL_OPENCLI_FALLBACK=1 to enable after Cloak"
        ]

    if platform == "reddit" and rdt_cli_fallback_enabled(rdt_cli_fallback):
        rdt = extract_reddit_rdt(
            url,
            length=length,
            include_content=include_content,
            timeout=max(timeout, 30),
            runner=rdt_runner,
            enabled=True,
        )
        if rdt is not None:
            rdt = dict(rdt)
            rdt["warnings"] = _merge_warnings(cloak, rdt) + [
                "rdt-cli used as opt-in fallback after Cloak-first Reddit route"
            ]
            if rdt.get("status") == "ok" and _result_has_useful_text(rdt):
                return rdt
            if _result_has_useful_text(rdt) and not _result_has_useful_text(cloak):
                return rdt
            cloak = dict(cloak)
            cloak["warnings"] = _merge_warnings(cloak, rdt)
    elif platform == "reddit" and cloak.get("status") in {"error", "partial"} and not gate_hit:
        cloak = dict(cloak)
        hint = "RDT_CLI_FALLBACK=1 enables opt-in rdt-cli after Cloak (never auto-install, never auto-cookie)"
        if hint not in (cloak.get("warnings") or []):
            cloak["warnings"] = list(cloak.get("warnings") or []) + [hint]

    return cloak


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
    twitter_runner: Any | None = None,
    opencli_runner: Any | None = None,
    opencli_daemon_fetcher: Any | None = None,
    cloak_fetcher: CloakPageFetcher | None = None,
    browser_profile_dir: str = "",
    browser_post_load_wait_ms: int = 8000,
    browser_max_concurrency: int = 1,
    cloak_headless: bool | None = None,
    opencli_fallback: bool | None = None,
    rdt_cli_fallback: bool | None = None,
    rdt_runner: Any | None = None,
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

    if platform == "x":
        return extract_x(
            url,
            length=length,
            include_content=include_content,
            timeout=max(timeout, 30),
            runner=twitter_runner,
        )

    if platform in CLOAK_FIRST_PLATFORMS:
        return _cloak_first_social(
            url,
            platform=platform,
            length=length,
            include_content=include_content,
            timeout=timeout,
            browser_profile_dir=browser_profile_dir,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
            browser_max_concurrency=browser_max_concurrency,
            headless=cloak_headless,
            cloak_fetcher=cloak_fetcher,
            opencli_fallback=opencli_fallback,
            rdt_cli_fallback=rdt_cli_fallback,
            opencli_runner=opencli_runner,
            opencli_daemon_fetcher=opencli_daemon_fetcher,
            rdt_runner=rdt_runner,
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
