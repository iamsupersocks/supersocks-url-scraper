"""Cloak-first social reads for Reddit, Instagram, and Facebook.

Renders public pages with CloakBrowser via the shared browser_fetcher
abstractions, then extracts title/text/author/published_at from HTML meta and
stable selectors. Never automates login/MFA/CAPTCHA, never reads cookies outside
an explicitly provided profile directory, and never prints secrets.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from .backend import redact_secrets, trim_text
from .domains import detect_platform, is_safe_public_http_url

CLOAK_SOCIAL_PLATFORMS = frozenset({"reddit", "instagram", "facebook"})
MIN_USEFUL_CHARS = 80

CloakPageFetcher = Callable[..., Any]

_LOGIN_MARKERS = (
    "log in",
    "sign in",
    "sign up",
    "create an account",
    "connexion",
    "identifiez-vous",
    "you must log in",
    "login to continue",
    "log in to continue",
    "please log in",
    "authwall",
    "checkpoint",
)
_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "are you a robot",
    "security check",
    "unusual traffic",
    "verify you are human",
    "press & hold",
)
_CONSENT_MARKERS = (
    "cookie consent",
    "consentement",
    "before you continue",
    "accept all cookies",
    "accepter tous les cookies",
    "privacy preference center",
)

_REDDIT_SHREDDIT_TITLE = re.compile(
    r"<shreddit-title[^>]*title=[\"']([^\"']+)[\"']",
    re.I,
)
_REDDIT_POST_CONTENT = re.compile(
    r"<(?:div|p|h1)[^>]*(?:slot=[\"']text-body[\"']|id=[\"']post-title[\"']|data-testid=[\"']post-content[\"']|"
    r"class=[\"'][^\"']*(?:Post|post-content|RichTextJSON-root)[^\"']*[\"'])[^>]*>(.*?)</(?:div|p|h1)>",
    re.I | re.S,
)
_IG_ARTICLE = re.compile(
    r"<article\b[^>]*>(.*?)</article>",
    re.I | re.S,
)
_FB_POST_TEXT = re.compile(
    r"<(?:div|span)[^>]*(?:data-ad-preview=[\"']message[\"']|data-testid=[\"']post_message[\"']|"
    r"class=[\"'][^\"']*(?:userContent|x1iorvi4)[^\"']*[\"'])[^>]*>(.*?)</(?:div|span)>",
    re.I | re.S,
)
_AUTHOR_SELECTORS = (
    re.compile(r"<meta[^>]+(?:name|property)=[\"'](?:author|article:author|og:article:author)[\"'][^>]+content=[\"']([^\"']+)[\"']", re.I),
    re.compile(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:name|property)=[\"'](?:author|article:author|og:article:author)[\"']", re.I),
    re.compile(r"<a\b[^>]*(?:class=[\"'][^\"']*(?:author|Author|username)[^\"']*[\"']|rel=[\"']author[\"'])[^>]*>(.*?)</a>", re.I | re.S),
)
_TIME_SELECTORS = (
    re.compile(r"<meta[^>]+(?:name|property)=[\"'](?:article:published_time|og:updated_time|datePublished)[\"'][^>]+content=[\"']([^\"']+)[\"']", re.I),
    re.compile(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:name|property)=[\"'](?:article:published_time|og:updated_time|datePublished)[\"']", re.I),
    re.compile(r"<time\b[^>]*(?:datetime|content)=[\"']([^\"']+)[\"']", re.I),
)


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def resolve_social_profile_dir(
    explicit: str = "",
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve optional persistent Cloak profile for social reads.

    Precedence: explicit argument → ``SOCIAL_BROWSER_PROFILE_DIR`` →
    ``BROWSER_PROFILE_DIR``. Never invents a profile path and never reads
    cookies outside that directory.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env = environ if environ is not None else os.environ
    for key in ("SOCIAL_BROWSER_PROFILE_DIR", "BROWSER_PROFILE_DIR"):
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return ""


def resolve_social_headless(
    headless: bool | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool | None:
    if headless is not None:
        return bool(headless)
    env = environ if environ is not None else os.environ
    for key in ("SOCIAL_CLOAK_HEADLESS", "CLOAK_HEADLESS", "BROWSER_HEADLESS"):
        raw = str(env.get(key) or "").strip().lower()
        if not raw:
            continue
        if raw in {"headed", "headful"}:
            return False
        return raw not in {"0", "false", "no", "off", "none"}
    return None


def cloakbrowser_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("cloakbrowser") is not None
    except Exception:
        return False


def _clean_text(value: object) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(text.split())


def _strip_embedded_markup(markup: str) -> str:
    """Remove embedded code/comments so gate heuristics ignore in-script markers."""
    text = markup or ""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    for tag in ("script", "style", "noscript", "template"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", text, flags=re.I | re.S)
    return text


def _meta_content(markup: str, *, name: str | None = None, prop: str | None = None) -> str:
    attr = "name" if name else "property"
    key = name or prop
    if not key:
        return ""
    escaped = re.escape(key)
    patterns = [
        rf"<meta[^>]+{attr}=[\"']{escaped}[\"'][^>]+content=[\"']([^\"']{{1,5000}})[\"']",
        rf"<meta[^>]+content=[\"']([^\"']{{1,5000}})[\"'][^>]+{attr}=[\"']{escaped}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, markup or "", re.I | re.S)
        if match:
            return _clean_text(match.group(1))
    return ""


def _json_ld_nodes(markup: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<script[^>]+type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',
        markup or "",
        re.I | re.S,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                else:
                    nodes.append(item)
            elif isinstance(item, list):
                stack.extend(item)
    return nodes


def detect_social_gate(markup: str, *, platform: str) -> str | None:
    """Return a gate reason when login/CAPTCHA/consent blocks useful extraction."""
    visible = _strip_embedded_markup(markup)
    blob = _clean_text(visible).lower()
    if not blob:
        return "empty page"
    if any(marker in blob for marker in _CAPTCHA_MARKERS):
        return "CAPTCHA/challenge"
    # Platform-specific login shells that dominate the page.
    login_hits = sum(1 for marker in _LOGIN_MARKERS if marker in blob)
    useful_meta = bool(
        _meta_content(markup, prop="og:description")
        or _meta_content(markup, name="description")
        or _meta_content(markup, prop="og:title")
    )
    if login_hits >= 2 and not useful_meta:
        return "login/auth wall"
    if platform == "instagram" and "login • instagram" in blob and not useful_meta:
        return "login/auth wall"
    if platform == "facebook" and ("log into facebook" in blob or "créer un compte" in blob) and not useful_meta:
        return "login/auth wall"
    if platform == "reddit" and ("log in" in blob and "sign up" in blob) and "shreddit-post" not in visible.lower() and not useful_meta:
        return "login/auth wall"
    if any(marker in blob for marker in _CONSENT_MARKERS) and len(blob) < 600 and not useful_meta:
        return "consent wall"
    return None


def _extract_author(markup: str, nodes: list[dict[str, Any]]) -> str | None:
    for pattern in _AUTHOR_SELECTORS:
        match = pattern.search(markup or "")
        if match:
            value = _clean_text(match.group(1))
            if value:
                return value
    for node in nodes:
        for key in ("author", "creator", "publisher"):
            value = node.get(key)
            if isinstance(value, dict):
                name = _clean_text(value.get("name") or value.get("@id") or "")
                if name:
                    return name
            elif isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    name = _clean_text(first.get("name") or "")
                    if name:
                        return name
                name = _clean_text(first)
                if name:
                    return name
            else:
                name = _clean_text(value)
                if name:
                    return name
    return None


def _extract_published_at(markup: str, nodes: list[dict[str, Any]]) -> str | None:
    for pattern in _TIME_SELECTORS:
        match = pattern.search(markup or "")
        if match:
            value = _clean_text(match.group(1))
            if value:
                return value
    for node in nodes:
        for key in ("datePublished", "uploadDate", "dateCreated", "dateModified"):
            value = _clean_text(node.get(key))
            if value:
                return value
    return None


def _platform_body(markup: str, *, platform: str) -> str:
    chunks: list[str] = []
    if platform == "reddit":
        title = _REDDIT_SHREDDIT_TITLE.search(markup or "")
        if title:
            chunks.append(_clean_text(title.group(1)))
        for match in _REDDIT_POST_CONTENT.finditer(markup or ""):
            text = _clean_text(match.group(1))
            if text and text not in chunks:
                chunks.append(text)
    elif platform == "instagram":
        for match in _IG_ARTICLE.finditer(markup or ""):
            text = _clean_text(match.group(1))
            if text:
                chunks.append(text)
                break
    elif platform == "facebook":
        for match in _FB_POST_TEXT.finditer(markup or ""):
            text = _clean_text(match.group(1))
            if text and text not in chunks:
                chunks.append(text)
    return "\n\n".join(chunks).strip()


def parse_cloak_social_html(
    markup: str,
    *,
    platform: str,
    url: str,
    page_title: str | None = None,
    length: int = 900,
    include_content: bool = False,
    fetch_method: str = "cloak",
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Parse rendered social HTML into the public JSON contract."""
    max_chars = max(50, min(int(length or 900), 10_000))
    warnings = list(extra_warnings or [])
    gate = detect_social_gate(markup, platform=platform)
    nodes = _json_ld_nodes(markup)
    title = (
        _meta_content(markup, prop="og:title")
        or _meta_content(markup, name="twitter:title")
        or _clean_text(page_title)
        or None
    )
    if not title and platform == "reddit":
        match = _REDDIT_SHREDDIT_TITLE.search(markup or "")
        if match:
            title = _clean_text(match.group(1)) or None
    for node in nodes:
        if not title:
            candidate = _clean_text(node.get("headline") or node.get("name") or node.get("title"))
            if candidate:
                title = candidate
                break

    description = (
        _meta_content(markup, prop="og:description")
        or _meta_content(markup, name="description")
        or _meta_content(markup, name="twitter:description")
    )
    body = _platform_body(markup, platform=platform)
    for node in nodes:
        for key in ("articleBody", "text", "description", "caption"):
            value = _clean_text(node.get(key))
            if value and value not in body:
                body = f"{body}\n\n{value}".strip() if body else value
    text = body or description or ""
    author = _extract_author(markup, nodes)
    published_at = _extract_published_at(markup, nodes)
    summary = trim_text(text or title or "", max_chars)
    useful = len(summary) >= MIN_USEFUL_CHARS or len(text) >= MIN_USEFUL_CHARS

    if gate:
        warnings.append(
            f"{platform} blocked by {gate}; warm an operator-owned Cloak profile once "
            f"(BROWSER_PROFILE_DIR / SOCIAL_BROWSER_PROFILE_DIR) or enable opt-in desktop fallback. "
            "Never automate login/MFA/CAPTCHA."
        )
        status = "partial" if useful else "error"
    elif useful:
        status = "ok"
    elif title or summary:
        status = "partial"
        warnings.append(f"{platform} rendered but useful text looks thin")
    else:
        status = "error"
        warnings.append(f"{platform} Cloak render produced no readable title/text")

    out: dict[str, Any] = {
        "url": url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": fetch_method,
        "status": status,
        "warnings": warnings,
        "platform": platform,
        "author": author,
        "published_at": published_at,
    }
    if include_content:
        out["content"] = text or summary
    # Never echo profile paths that might contain home directories with secrets-looking segments.
    return out


def _missing_backend_result(url: str, *, platform: str, length: int) -> dict[str, Any]:
    max_chars = max(50, min(int(length or 900), 10_000))
    return {
        "url": url,
        "content_type": "article",
        "title": None,
        "summary": "",
        "length": max_chars,
        "fetch_method": "cloak",
        "status": "error",
        "warnings": [
            "cloakbrowser not installed; install the browser extra: "
            "pip install 'supersocks-url-scraper[browser]'",
            "OpenCLI/rdt-cli remain opt-in desktop fallbacks only (SOCIAL_OPENCLI_FALLBACK / RDT_CLI_FALLBACK).",
        ],
        "platform": platform,
    }


def _missing_profile_result(
    url: str,
    *,
    platform: str,
    length: int,
) -> dict[str, Any]:
    max_chars = max(50, min(int(length or 900), 10_000))
    return {
        "url": url,
        "content_type": "article",
        "title": None,
        "summary": "",
        "length": max_chars,
        "fetch_method": "cloak-profile",
        "status": "error",
        "warnings": [
            "configured social Cloak profile is absent; "
            "initialize once with scripts/browser_profile_probe.py under an existing DISPLAY, "
            "then retry. Cookies stay inside the operator-provided profile only.",
        ],
        "platform": platform,
    }


def extract_cloak_social(
    url: str,
    *,
    platform: str | None = None,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 45,
    browser_profile_dir: str = "",
    browser_post_load_wait_ms: int = 8000,
    browser_max_concurrency: int = 1,
    headless: bool | None = None,
    cloak_fetcher: CloakPageFetcher | None = None,
    environ: Mapping[str, str] | None = None,
    require_existing_profile: bool = True,
) -> dict[str, Any] | None:
    detected = detect_platform(url)
    platform_id = platform or detected
    if platform_id not in CLOAK_SOCIAL_PLATFORMS:
        return None
    if detected != platform_id or not is_safe_public_http_url(url):
        return None

    if cloak_fetcher is None and not cloakbrowser_available():
        return _missing_backend_result(url, platform=platform_id, length=length)

    profile_dir = resolve_social_profile_dir(browser_profile_dir, environ=environ)
    if profile_dir and require_existing_profile and not Path(profile_dir).expanduser().exists():
        return _missing_profile_result(url, platform=platform_id, length=length)

    resolved_headless = resolve_social_headless(headless, environ=environ)
    fetch = cloak_fetcher
    if fetch is None:
        from ..browser_fetcher import fetch_with_cloak

        fetch = fetch_with_cloak

    try:
        page = fetch(
            url,
            timeout_seconds=float(timeout),
            post_load_wait_ms=int(browser_post_load_wait_ms),
            profile_dir=profile_dir,
            max_concurrency=max(1, int(browser_max_concurrency or 1)),
            headless=resolved_headless,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max(50, min(int(length or 900), 10_000)),
            "fetch_method": "cloak-profile" if profile_dir else "cloak",
            "status": "error",
            "warnings": [f"cloak social render failed: {redact_secrets(str(exc))}"],
            "platform": platform_id,
        }

    method = getattr(page, "method", None) or ("cloak-profile" if profile_dir else "cloak")
    html_text = getattr(page, "html", "") or ""
    page_title = getattr(page, "title", None)
    final_url = getattr(page, "final_url", url) or url
    extra: list[str] = []
    consent = getattr(page, "consent_action", None)
    if consent:
        extra.append(f"browser consent dismissed via: {consent}")
    return parse_cloak_social_html(
        html_text,
        platform=platform_id,
        url=final_url,
        page_title=page_title,
        length=length,
        include_content=include_content,
        fetch_method=str(method),
        extra_warnings=extra,
    )


def extract_reddit(url: str, **kwargs: Any) -> dict[str, Any] | None:
    return extract_cloak_social(url, platform="reddit", **kwargs)


def extract_instagram_cloak(url: str, **kwargs: Any) -> dict[str, Any] | None:
    return extract_cloak_social(url, platform="instagram", **kwargs)


def extract_facebook_cloak(url: str, **kwargs: Any) -> dict[str, Any] | None:
    return extract_cloak_social(url, platform="facebook", **kwargs)


def opencli_fallback_enabled(
    explicit: bool | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    env = environ if environ is not None else os.environ
    return _truthy(env.get("SOCIAL_OPENCLI_FALLBACK"), False) or _truthy(env.get("OPENCLI_FALLBACK"), False)
