"""Specialized public LinkedIn HTML extractor.

Public guest pages only. No login, cookies, Voyager private APIs, stealth
browsers, proxies, or authenticated MCP flows.

Extraction priority:
1. OpenGraph / meta description
2. Valid JSON-LD (@graph aware)
3. Stable public LinkedIn guest selectors

Auth walls, challenges, navigation-only chrome, and too-poor useful content
never return status=ok.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse

from .domains import detect_platform, is_safe_public_http_url

DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2"
MAX_HTML_BYTES = 5 * 1024 * 1024
MIN_USEFUL_CHARS = 80

HtmlFetcher = Callable[..., dict[str, Any]]

_PAGE_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("profile", re.compile(r"^/in/[^/]+/?$", re.I)),
    ("company", re.compile(r"^/company/[^/]+/?$", re.I)),
    ("school", re.compile(r"^/school/[^/]+/?$", re.I)),
    ("showcase", re.compile(r"^/showcase/[^/]+/?$", re.I)),
    ("job", re.compile(r"^/(?:jobs/view|jobs-guest/jobs/view)/[^/?#]+", re.I)),
    ("article", re.compile(r"^/(?:pulse|articles)/", re.I)),
    ("post", re.compile(r"^/(?:posts/|feed/update/)", re.I)),
)

_AUTHWALL_STRUCTURAL = re.compile(
    r"""(?:\bid|\bclass)=["'][^"']*(?:authwall|auth-wall|join-form|sign-in-modal|contextual-sign-in-modal)[^"']*["']""",
    re.I,
)
_AUTHWALL_PHRASES = (
    "join to view",
    "sign in to view",
    "sign in to continue",
    "join linkedin",
    "already on linkedin? sign in",
    "make the most of your professional life",
    "session redirect",
)

_CHALLENGE_MARKERS = (
    "security verification",
    "security challenge",
    "captcha",
    "are you a robot",
    "please complete the security check",
    "checkpoint/challenge",
    "funcaptcha",
)

_CTA_OR_NAV_MARKERS = (
    "agree & join",
    "agree and join",
    "join now",
    "sign in",
    "skip to main content",
    "linkedin ©",
    "user agreement",
    "privacy policy",
    "cookie policy",
    "accessibility",
    "help center",
)

_NOISE_SELECTORS = (
    r'<nav\b[^>]*>.*?</nav>',
    r'<header\b[^>]*>.*?</header>',
    r'<footer\b[^>]*>.*?</footer>',
    r'<aside\b[^>]*>.*?</aside>',
    r'<dialog\b[^>]*>.*?</dialog>',
    r'<form\b[^>]*(?:authwall|join|login|sign-?in)[^>]*>.*?</form>',
    r'<div\b[^>]*(?:modal|authwall|join-form|sign-in-modal|contextual-sign-in-modal|global-nav)[^>]*>.*?</div>',
)

_MAIN_CONTENT_CLASS = re.compile(
    r"(?:^|\s)(?:article-main__content|reader-article-content|article-content)(?:\s|$)",
    re.I,
)
_SIDEBAR_OR_NOISE_CLASS = re.compile(
    r"(?:^|\s)(?:core-section-container|categories|global-nav|sign-in-modal|contextual-sign-in-modal|authwall|join-form)(?:\s|$)",
    re.I,
)

_PUBLIC_TEXT_SELECTORS = (
    r'<h1\b[^>]*class=["\'][^"\']*(?:top-card-layout__title|top-card__title|profile-topcard|org-top-card|job-details-jobs-unified-top-card__job-title)[^"\']*["\'][^>]*>(.*?)</h1>',
    r'<h1\b[^>]*>(.*?)</h1>',
    r'<div\b[^>]*class=["\'][^"\']*(?:top-card-layout__headline|profile-topcard-person-entity__headline|org-top-card-summary__tagline)[^"\']*["\'][^>]*>(.*?)</div>',
    r'<div\b[^>]*class=["\'][^"\']*(?:show-more-less-html__markup|core-section-container__content|description__text|reader-article-content|article-content|feed-shared-update-v2__description|break-words)[^"\']*["\'][^>]*>(.*?)</div>',
    r'<section\b[^>]*class=["\'][^"\']*(?:core-section-container|show-more-less-html)[^"\']*["\'][^>]*>(.*?)</section>',
    r'<p\b[^>]*>(.*?)</p>',
)

_STRUCTURED_KEYS = (
    "name",
    "headline",
    "description",
    "articleBody",
    "title",
    "datePosted",
    "datePublished",
    "dateModified",
    "validThrough",
    "employmentType",
    "jobLocation",
    "hiringOrganization",
    "author",
    "url",
    "sameAs",
    "industry",
    "numberOfEmployees",
    "address",
)


def _trim(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    space = cut.rfind(" ", 0, max_chars)
    return ((cut[:space] if space >= int(max_chars * 0.5) else cut[: max_chars - 1]) + "…").strip()


def _clean_text(value: object, limit: int | None = None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _meta_content(markup: str, *, name: str | None = None, prop: str | None = None) -> str:
    attr = "name" if name else "property"
    value = name or prop
    if not value:
        return ""
    escaped = re.escape(value)
    patterns = [
        rf"<meta[^>]+{attr}=[\"']{escaped}[\"'][^>]+content=[\"']([^\"']{{1,5000}})[\"']",
        rf"<meta[^>]+content=[\"']([^\"']{{1,5000}})[\"'][^>]+{attr}=[\"']{escaped}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, markup, re.I | re.S)
        if match:
            return _clean_text(match.group(1))
    return ""


def classify_linkedin_page_type(url: str) -> str:
    """Classify a LinkedIn public URL into a coarse page type."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    for page_type, pattern in _PAGE_TYPE_PATTERNS:
        if pattern.search(path):
            return page_type
    # Guest job variants sometimes carry the id only under /jobs/.
    if re.search(r"/jobs/(?:view/)?", path, re.I):
        return "job"
    return "unknown"


def strip_linkedin_chrome(markup: str) -> str:
    """Remove common LinkedIn navigation/modals/CTA chrome before text scrape."""
    cleaned = markup or ""
    for pattern in _NOISE_SELECTORS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I | re.S)
    # Drop obvious CTA phrases that dominate guest shells.
    for marker in _CTA_OR_NAV_MARKERS:
        cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.I)
    return cleaned


def _normalized_blob(markup: str) -> str:
    return " ".join(_clean_text(markup).lower().split())


def _authwall_signals(markup: str) -> bool:
    """Return True when sign-in/authwall chrome is present (may coexist with public content)."""
    blob = _normalized_blob(markup)
    structural_authwall = bool(_AUTHWALL_STRUCTURAL.search(markup or ""))
    phrase_authwall = any(marker in blob for marker in _AUTHWALL_PHRASES)
    return structural_authwall or phrase_authwall


class _MainContentParser(HTMLParser):
    """Extract primary article/main body text, skipping aside and sign-in chrome."""

    _SKIP_TAGS = frozenset({"script", "style", "nav", "header", "footer", "aside", "dialog", "form"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._capture_depth = 0
        self._parts: list[str] = []
        self._found_main = False

    @staticmethod
    def _class_str(attrs: list[tuple[str, str | None]]) -> str:
        for key, value in attrs:
            if key == "class" and value:
                return value
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = self._class_str(attrs)
        if tag in self._SKIP_TAGS or _SIDEBAR_OR_NOISE_CLASS.search(cls):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if not self._found_main:
            is_main_tag = tag in {"article", "main"}
            is_main_class = bool(_MAIN_CONTENT_CLASS.search(cls))
            if is_main_class or (is_main_tag and not _SIDEBAR_OR_NOISE_CLASS.search(cls)):
                self._capture_depth = 1
                self._found_main = True
                return
        if self._capture_depth:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if self._capture_depth:
            self._capture_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_depth > 0 and not self._skip_depth:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        return _clean_text(" ".join(self._parts))


def _extract_article_main_text(markup: str) -> str:
    """Prefer article-main__content (and equivalents) over sidebar/aside regions."""
    parser = _MainContentParser()
    try:
        parser.feed(markup or "")
        parser.close()
    except Exception:
        return ""
    return parser.get_text()


def _structured_is_substantial(data: dict[str, Any] | None, page_type: str) -> bool:
    if not data:
        return False
    text, _, _ = _text_from_structured(data)
    if len(text) >= MIN_USEFUL_CHARS:
        return True
    preferred: dict[str, tuple[str, ...]] = {
        "profile": ("person", "profilepage"),
        "company": ("organization", "corporation"),
        "school": ("organization", "collegeoruniversity", "educationalorganization"),
        "showcase": ("organization",),
        "job": ("jobposting",),
        "article": ("article", "newsarticle", "blogposting", "techarticle"),
        "post": ("socialmediaposting", "discussionforumposting", "article", "blogposting"),
    }
    want = {t.lower() for t in preferred.get(page_type, ())}
    types = _as_types(data)
    if want and not any(t in want for t in types):
        return False
    for key in ("name", "headline", "title", "description", "articleBody"):
        value = data.get(key)
        if isinstance(value, str) and len(value.strip()) >= 40:
            return True
    return False


def _has_strong_public_signal(
    *,
    page_type: str,
    structured: dict[str, Any] | None,
    structured_text: str,
    selector_text: str,
    og_desc: str,
) -> bool:
    if _structured_is_substantial(structured, page_type):
        return True
    for text in (selector_text, structured_text, og_desc):
        if _is_useful(text):
            return True
    return False


def detect_linkedin_gate(markup: str) -> str | None:
    """Return an explicit gate reason for challenge/nav-only/empty shells."""
    blob = _normalized_blob(markup)
    if not blob:
        return "empty LinkedIn HTML"
    if any(marker in blob for marker in _CHALLENGE_MARKERS) and (
        "captcha" in blob or "security verification" in blob or "checkpoint/challenge" in blob or len(blob) < 1600
    ):
        return "linkedin security challenge"
    stripped = strip_linkedin_chrome(markup)
    remaining = _clean_text(stripped)
    if len(remaining) < 60 and sum(1 for marker in _CTA_OR_NAV_MARKERS if marker in blob) >= 3:
        return "linkedin navigation/CTA shell without useful public content"
    return None


def _as_types(item: dict[str, Any]) -> list[str]:
    raw = item.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return [str(v).lower() for v in values if v]


def iter_jsonld(markup: str) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    raw_scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        markup,
        re.I | re.S,
    )
    malformed = 0
    for raw in raw_scripts:
        try:
            data = json.loads(html.unescape(raw).strip())
        except Exception:
            malformed += 1
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
    return out, malformed


def _sanitize_structured(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key.startswith("@") and key not in {"@type"}:
                continue
            if key not in _STRUCTURED_KEYS and key != "@type":
                continue
            cleaned = _sanitize_structured(item, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                out[key] = cleaned
        return out
    if isinstance(value, list):
        items = [_sanitize_structured(v, depth=depth + 1) for v in value[:20]]
        return [v for v in items if v not in (None, "", [], {})]
    if isinstance(value, (int, float, bool)):
        return value
    text = _clean_text(value, 2000)
    return text or None


def extract_structured_data(markup: str, page_type: str) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    items, malformed = iter_jsonld(markup)
    if malformed:
        warnings.append(f"ignored {malformed} malformed JSON-LD block(s)")
    preferred: dict[str, tuple[str, ...]] = {
        "profile": ("person", "profilepage"),
        "company": ("organization", "corporation"),
        "school": ("organization", "collegeoruniversity", "educationalorganization"),
        "showcase": ("organization",),
        "job": ("jobposting",),
        "article": ("article", "newsarticle", "blogposting", "techarticle"),
        "post": ("socialmediaposting", "discussionforumposting", "article", "blogposting"),
        "unknown": ("person", "organization", "jobposting", "article", "socialmediaposting"),
    }
    want = {t.lower() for t in preferred.get(page_type, preferred["unknown"])}
    chosen: dict[str, Any] | None = None
    for item in items:
        types = _as_types(item)
        if not types:
            continue
        if want and not any(t in want for t in types):
            continue
        sanitized = _sanitize_structured(item)
        if isinstance(sanitized, dict) and sanitized:
            chosen = sanitized
            break
    return chosen, warnings


def _text_from_structured(data: dict[str, Any] | None) -> tuple[str, str | None, str | None]:
    if not data:
        return "", None, None
    title = None
    for key in ("title", "name", "headline"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break
    author = None
    raw_author = data.get("author") or data.get("hiringOrganization")
    if isinstance(raw_author, dict):
        author = _clean_text(raw_author.get("name")) or None
    elif isinstance(raw_author, str):
        author = raw_author.strip() or None
    parts = [
        data.get("headline"),
        data.get("description"),
        data.get("articleBody"),
        data.get("title"),
        data.get("name"),
    ]
    org = data.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name"):
        parts.append(f"Hiring organization: {org.get('name')}")
    loc = data.get("jobLocation")
    if isinstance(loc, dict):
        parts.append(_clean_text(json.dumps(loc, ensure_ascii=False)))
    elif isinstance(loc, str):
        parts.append(loc)
    text = _clean_text(" ".join(str(p) for p in parts if p))
    return text, title, author


def _selector_text(markup: str, *, page_type: str = "unknown") -> str:
    if page_type == "article":
        article_main = _extract_article_main_text(markup)
        if _is_useful(article_main):
            return article_main
    cleaned = strip_linkedin_chrome(markup)
    chunks: list[str] = []
    for pattern in _PUBLIC_TEXT_SELECTORS:
        for match in re.finditer(pattern, cleaned, re.I | re.S):
            value = _clean_text(match.group(1), 4000)
            if value and value.lower() not in {m.lower() for m in _CTA_OR_NAV_MARKERS}:
                chunks.append(value)
            if sum(len(c) for c in chunks) >= 6000:
                break
        if sum(len(c) for c in chunks) >= 6000:
            break
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for chunk in chunks:
        key = chunk.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return _clean_text(" ".join(unique))


def _title_from_markup(markup: str) -> str:
    for prop in ("og:title", "twitter:title"):
        value = _meta_content(markup, prop=prop) if prop.startswith("og:") else _meta_content(markup, name=prop)
        if value:
            return value
    value = _meta_content(markup, name="title")
    if value:
        return value
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    return _clean_text(match.group(1), 220) if match else ""


def _is_useful(text: str) -> bool:
    compact = " ".join((text or "").split())
    if len(compact) < MIN_USEFUL_CHARS:
        return False
    lower = compact.lower()
    cta_hits = sum(1 for marker in _CTA_OR_NAV_MARKERS if marker in lower)
    if cta_hits >= 3 and len(compact) < 220:
        return False
    return True


def extract_linkedin_html(
    url: str,
    markup: str,
    *,
    length: int = 900,
    include_content: bool = False,
    fetch_method: str = "http",
    final_url: str | None = None,
) -> dict[str, Any]:
    """Extract a public LinkedIn payload from already-fetched guest HTML."""
    max_chars = max(50, min(int(length or 900), 10_000))
    page_url = final_url or url
    page_type = classify_linkedin_page_type(page_url)
    warnings: list[str] = [f"linkedin public extractor ({page_type})"]

    if detect_platform(page_url) != "linkedin" and detect_platform(url) != "linkedin":
        return {
            "url": page_url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": fetch_method,
            "status": "error",
            "warnings": ["not a LinkedIn URL"],
            "platform": "linkedin",
            "linkedin_page_type": "unknown",
        }

    gate = detect_linkedin_gate(markup)
    structured, jsonld_warnings = extract_structured_data(markup, page_type)
    warnings.extend(jsonld_warnings)

    og_title = _title_from_markup(markup)
    og_desc = _meta_content(markup, prop="og:description") or _meta_content(markup, name="description")
    structured_text, structured_title, structured_author = _text_from_structured(structured)
    selector_text = _selector_text(markup, page_type=page_type)
    authwall = _authwall_signals(markup)
    has_public = _has_strong_public_signal(
        page_type=page_type,
        structured=structured,
        structured_text=structured_text,
        selector_text=selector_text,
        og_desc=og_desc,
    )

    title = structured_title or og_title or None
    if title and title.lower() in {"linkedin", "linkedin login", "sign up", "join linkedin"}:
        title = structured_title or None
        if not title:
            warnings.append("discarded generic LinkedIn chrome title")

    body_parts = [part for part in (structured_text, og_desc, selector_text) if part]
    # Prefer richer unique text.
    body = ""
    for part in body_parts:
        if len(part) > len(body):
            body = part
    if structured_text and og_desc and structured_text not in og_desc and og_desc not in structured_text:
        body = _clean_text(f"{structured_text} {og_desc}")
    if selector_text and selector_text not in body and len(selector_text) > 40:
        body = _clean_text(f"{body} {selector_text}".strip())

    summary = _trim(body, max_chars)
    author = structured_author
    published_at = None
    if structured:
        for key in ("datePosted", "datePublished", "dateModified"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                published_at = value.strip()
                break

    if gate:
        warnings.append(gate)
        if authwall:
            warnings.append("linkedin authwall/login gate")
        status = "partial"
        if not _is_useful(summary):
            summary = _trim(summary or og_desc or "", max_chars)
            warnings.append("useful public content too poor after LinkedIn gate detection")
    elif authwall and not has_public:
        warnings.append("linkedin authwall/login gate")
        status = "partial"
        if not _is_useful(summary):
            summary = _trim(summary or og_desc or "", max_chars)
            warnings.append("useful public content too poor after LinkedIn gate detection")
    elif authwall and has_public:
        warnings.append("linkedin sign-in chrome ignored; public content extracted")
        status = "ok" if _is_useful(summary) else "partial"
        if status == "partial":
            warnings.append("useful public LinkedIn content too poor")
    elif not markup.strip():
        status = "partial"
        warnings.append("empty LinkedIn HTML")
        summary = ""
    elif not _is_useful(summary):
        status = "partial"
        warnings.append("useful public LinkedIn content too poor")
    else:
        status = "ok"

    payload: dict[str, Any] = {
        "url": page_url,
        "content_type": "article",
        "title": title,
        "summary": summary,
        "length": max_chars,
        "fetch_method": fetch_method,
        "status": status,
        "warnings": warnings,
        "platform": "linkedin",
        "linkedin_page_type": page_type,
        "author": author,
        "published_at": published_at,
    }
    if structured:
        payload["structured_data"] = structured
    image = _meta_content(markup, prop="og:image") or _meta_content(markup, name="twitter:image")
    if image:
        payload["image_url"] = image
    if include_content:
        payload["content"] = body or summary
    return payload


def _default_html_fetcher(url: str, *, timeout: int = 20, max_bytes: int = MAX_HTML_BYTES) -> dict[str, Any]:
    from ..reader import FetchError, fetch_url

    if not is_safe_public_http_url(url):
        raise FetchError("linkedin fetch blocked: unsafe URL")
    resource = fetch_url(
        url,
        timeout=timeout,
        max_bytes=min(max_bytes, MAX_HTML_BYTES),
        user_agent=DEFAULT_USER_AGENT,
        fetch_method="http",
    )
    if not is_safe_public_http_url(resource.final_url):
        raise FetchError("linkedin fetch blocked: unsafe redirect target")
    if detect_platform(resource.final_url) != "linkedin":
        raise FetchError("linkedin fetch blocked: redirect left linkedin.com")
    return {
        "final_url": resource.final_url,
        "text": resource.text,
        "fetch_method": resource.headers.get("x-fetch-method", "http"),
    }


def extract_linkedin(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = 20,
    max_bytes: int = MAX_HTML_BYTES,
    html_fetcher: HtmlFetcher | None = None,
) -> dict[str, Any]:
    """Fetch (optional) and extract a public LinkedIn page."""
    max_chars = max(50, min(int(length or 900), 10_000))
    if detect_platform(url) != "linkedin" or not is_safe_public_http_url(url):
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "http",
            "status": "error",
            "warnings": ["linkedin extractor blocked: unsafe or non-LinkedIn URL"],
            "platform": "linkedin",
            "linkedin_page_type": classify_linkedin_page_type(url),
        }

    fetcher = html_fetcher or _default_html_fetcher
    try:
        fetched = fetcher(url, timeout=timeout, max_bytes=min(max_bytes, MAX_HTML_BYTES))
    except Exception as exc:
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": "http",
            "status": "error",
            "warnings": [f"linkedin fetch failed: {exc}"],
            "platform": "linkedin",
            "linkedin_page_type": classify_linkedin_page_type(url),
        }

    final_url = str(fetched.get("final_url") or url)
    markup = str(fetched.get("text") or "")
    fetch_method = str(fetched.get("fetch_method") or "http")
    if not is_safe_public_http_url(final_url) or detect_platform(final_url) != "linkedin":
        return {
            "url": url,
            "content_type": "unknown",
            "title": None,
            "summary": "",
            "length": max_chars,
            "fetch_method": fetch_method,
            "status": "error",
            "warnings": ["linkedin fetch blocked: unsafe or non-LinkedIn final URL"],
            "platform": "linkedin",
            "linkedin_page_type": classify_linkedin_page_type(url),
        }
    return extract_linkedin_html(
        url,
        markup,
        length=max_chars,
        include_content=include_content,
        fetch_method=fetch_method,
        final_url=final_url,
    )
