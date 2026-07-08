from __future__ import annotations

import html
import json
import re
import socket
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlsplit
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 20
MAX_BYTES = 25 * 1024 * 1024
DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2 (+https://github.com/iamsupersocks/supersocks-url-scraper)"

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT_UA = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ContentType = str
Status = str


@dataclass(frozen=True)
class FetchedResource:
    url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    headers: dict[str, str]

    @property
    def text(self) -> str:
        encoding = "utf-8"
        ctype = self.content_type.lower()
        if "charset=" in ctype:
            encoding = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or encoding
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ArticleContent:
    title: str | None
    text: str
    method: str


@dataclass(frozen=True)
class PdfContent:
    title: str | None
    text: str
    page_count: int


@dataclass(frozen=True)
class StrategyRecord:
    fetch_method: str
    detail: str = ""
    success_count: int = 0
    failure_count: int = 0
    last_success_at: str = ""
    last_failure_at: str = ""


class FetchError(RuntimeError):
    pass


class PdfDependencyError(RuntimeError):
    pass


class PdfParseError(RuntimeError):
    pass


def clean_text(value: object, limit: int | None = None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def meta_content(markup: str, *, name: str | None = None, prop: str | None = None) -> str:
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
            return clean_text(match.group(1))
    return ""


def iter_jsonld(markup: str) -> list[dict[str, Any]]:
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
    for prop in ("og:title",):
        value = meta_content(markup, prop=prop)
        if value:
            return value
    for name in ("twitter:title", "title"):
        value = meta_content(markup, name=name)
        if value:
            return value
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    return clean_text(match.group(1), 220) if match else ""


def extract_image_url(markup: str) -> str:
    for prop in ("og:image", "twitter:image"):
        value = meta_content(markup, prop=prop) or meta_content(markup, name=prop)
        if value:
            return value
    return ""


GENERIC_UI_TITLES = {
    "paramètres d'affichage",
    "paramètres d’affichage",
    "parametres d'affichage",
    "parametres d’affichage",
    "display settings",
    "settings",
    "menu",
    "navigation",
    "affichage",
    "table de concordance",
    "votre avis nous intéresse !",
    "votre avis nous interesse !",
    "droit national",
    "publications officielles",
}


def is_generic_title(title: str | None) -> bool:
    normalized = " ".join((title or "").strip().lower().split())
    return not normalized or normalized in GENERIC_UI_TITLES or len(normalized) < 4


def better_title_from_markup(markup: str, current: str | None = None) -> str | None:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        title = extract_title(markup)
        if title and not is_generic_title(title):
            return title
        return current if current and not is_generic_title(current) else None
    try:
        soup = BeautifulSoup(markup, "lxml")
    except Exception:
        title = extract_title(markup)
        if title and not is_generic_title(title):
            return title
        return current if current and not is_generic_title(current) else None
    for selector in ({"property": "og:title"}, {"name": "twitter:title"}, {"name": "title"}):
        tag = soup.find("meta", attrs=selector)
        value = tag.get("content", "").strip() if tag else ""
        if value and not is_generic_title(value):
            return value
    if current and not is_generic_title(current):
        return current
    for heading_name in ("h1", "h2"):
        for heading in soup.find_all(heading_name):
            value = heading.get_text(" ", strip=True)
            if value and len(value) >= 6 and not is_generic_title(value):
                return value
    title = extract_title(markup)
    return None if is_generic_title(title) else title


def detect_content_type(resource: FetchedResource) -> ContentType:
    ctype = (resource.content_type or "").split(";", 1)[0].strip().lower()
    if ctype.startswith("image/"):
        return "image"
    if ctype in {"application/pdf", "application/x-pdf"}:
        return "pdf"
    if ctype in {"text/html", "application/xhtml+xml"} or ctype.startswith("text/"):
        return "article"
    head = resource.content[:512]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if any(sig in head[:64] for sig in (b"<!doctype", b"<html", b"<HTML", b"<!DOCTYPE")):
        return "article"
    if (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"GIF87a")
        or head.startswith(b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    ):
        return "image"
    return "unknown"


def fetch_url(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
    headers: dict[str, str] | None = None,
    fetch_method: str = "http",
) -> FetchedResource:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FetchError("invalid http(s) URL")
    request_headers = {"User-Agent": user_agent, "Accept": "*/*", **(headers or {})}
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise FetchError(f"response exceeds max_bytes={max_bytes}")
            out_headers = {k.lower(): v for k, v in response.headers.items()}
            out_headers["x-fetch-method"] = fetch_method
            return FetchedResource(
                url=url,
                final_url=response.geturl(),
                status_code=getattr(response, "status", 200),
                content=raw,
                content_type=response.headers.get("content-type", "").lower(),
                headers=out_headers,
            )
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise FetchError(f"fetch failed: {type(exc).__name__}") from exc


def seo_variants() -> list[tuple[str, dict[str, str]]]:
    return [
        ("googlebot", {"User-Agent": GOOGLEBOT_UA}),
        ("bingbot", {"User-Agent": BINGBOT_UA}),
        ("referer-google", {"User-Agent": DESKTOP_UA, "Referer": "https://www.google.com/"}),
        ("referer-facebook", {"User-Agent": DESKTOP_UA, "Referer": "https://www.facebook.com/"}),
        ("referer-tco-amp", {"User-Agent": DESKTOP_UA, "Referer": "https://t.co/x?amp=1"}),
    ]


def fetch_with_browser(
    url: str,
    *,
    timeout: int = 60,
    max_bytes: int = MAX_BYTES,
    profile_dir: str = "",
    post_load_wait_ms: int = 8000,
) -> FetchedResource:
    from .browser_fetcher import fetch_with_cloak

    page = fetch_with_cloak(
        url,
        timeout_seconds=float(timeout),
        post_load_wait_ms=post_load_wait_ms,
        profile_dir=profile_dir,
    )
    raw = page.html.encode("utf-8")
    if len(raw) > max_bytes:
        raise FetchError(f"browser response exceeds max_bytes={max_bytes}")
    headers = {"content-type": "text/html; charset=utf-8", "x-fetch-method": page.method}
    if page.title:
        headers["x-browser-title"] = page.title
    return FetchedResource(
        url=url,
        final_url=page.final_url,
        status_code=page.status_code,
        content=raw,
        content_type="text/html; charset=utf-8",
        headers=headers,
    )


def fetch_with_seo_variants(url: str, *, preferred_method: str | None = None, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = MAX_BYTES) -> FetchedResource:
    variants = seo_variants()
    if preferred_method:
        variants = [v for v in variants if v[0] == preferred_method] + [v for v in variants if v[0] != preferred_method]
    errors: list[str] = []
    for method, headers in variants:
        try:
            resource = fetch_url(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                user_agent=headers.get("User-Agent", DESKTOP_UA),
                headers={k: v for k, v in headers.items() if k != "User-Agent"} | {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                fetch_method="seo",
            )
            resource.headers["x-seo-method"] = method
            return resource
        except FetchError as exc:
            errors.append(f"{method}: {exc}")
    raise FetchError("seo fallback failed: " + "; ".join(errors))




def _looks_like_bad_archive_snapshot(markup: str) -> bool:
    normalized = " ".join((markup or "").lower().split())
    if not normalized:
        return True
    bad_markers = [
        "welcome to nginx",
        "the nginx web server is successfully installed and working",
        "datadome captcha",
        "captcha-delivery.com",
        "please enable js and disable any ad blocker",
    ]
    return any(marker in normalized for marker in bad_markers)


def archive_candidates(url: str) -> list[tuple[str, str]]:
    """Return public cache/archive lookup URLs for an already-saved snapshot.

    This mirrors Celeste's last-resort architecture: do not submit/save pages;
    only read snapshots/cache entries that already exist.
    """
    encoded = quote(url, safe=":/")
    google_cache_encoded = "cache:" + quote(url, safe="")
    return [
        ("google-cache", f"https://webcache.googleusercontent.com/search?q={google_cache_encoded}"),
        ("archive.today", f"https://archive.today/latest/{encoded}"),
        ("archive.is", f"https://archive.is/latest/{encoded}"),
        ("wayback", f"https://web.archive.org/web/2/{encoded}"),
    ]


def fetch_from_archives(url: str, *, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = MAX_BYTES) -> FetchedResource:
    errors: list[str] = []
    for method, candidate_url in archive_candidates(url):
        try:
            resource = fetch_url(candidate_url, timeout=timeout, max_bytes=max_bytes, fetch_method="archive")
        except FetchError as exc:
            errors.append(f"{method}: {exc}")
            continue
        if _looks_like_bad_archive_snapshot(resource.text):
            errors.append(f"{method}: unusable archive snapshot")
            continue
        headers = dict(resource.headers)
        headers["x-fetch-method"] = "archive"
        headers["x-archive-method"] = method
        headers["x-archive-url"] = resource.final_url
        return FetchedResource(
            url=url,
            final_url=url,
            status_code=resource.status_code,
            content=resource.content,
            content_type=resource.content_type,
            headers=headers,
        )
    raise FetchError("archive fallback failed: " + ("; ".join(errors) if errors else "no archive candidates tried"))


def _try_trafilatura(markup: str, url: str) -> ArticleContent | None:
    try:
        import trafilatura
        from trafilatura.settings import use_config
    except ImportError:
        return None
    config = use_config()
    config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    text = trafilatura.extract(markup, url=url, include_comments=False, include_tables=False, favor_recall=True, config=config)
    if not text:
        return None
    title = None
    try:
        metadata = trafilatura.extract_metadata(markup)
        if metadata is not None:
            title = metadata.title
    except Exception:
        title = None
    return ArticleContent(title=title, text=text.strip(), method="trafilatura")


def _try_readability(markup: str) -> ArticleContent | None:
    try:
        from bs4 import BeautifulSoup
        from readability import Document
    except ImportError:
        return None
    try:
        doc = Document(markup)
        title = doc.short_title() or None
        soup = BeautifulSoup(doc.summary(html_partial=True), "lxml")
        text = "\n".join(p.get_text(" ", strip=True) for p in soup.find_all("p")).strip()
    except Exception:
        return None
    return ArticleContent(title=title, text=text, method="readability") if text else None


def _bs4_or_regex_fallback(markup: str) -> ArticleContent:
    title = extract_title(markup) or None
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", markup, flags=re.I | re.S)
        paragraphs = [clean_text(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", body, re.I | re.S)]
        text = "\n".join(p for p in paragraphs if len(p) > 20).strip()
        return ArticleContent(title=title, text=text or clean_text(body), method="regex")
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 20).strip()
    if not text:
        text = soup.get_text("\n", strip=True)
    return ArticleContent(title=title, text=text, method="bs4")


def extract_article(markup: str, url: str) -> ArticleContent:
    if not markup.strip():
        return ArticleContent(title=None, text="", method="empty")
    for candidate in (_try_trafilatura(markup, url), _try_readability(markup)):
        if candidate and candidate.text:
            title = better_title_from_markup(markup, candidate.title) or candidate.title or extract_title(markup) or None
            return ArticleContent(title=title, text=candidate.text, method=candidate.method)
    fallback = _bs4_or_regex_fallback(markup)
    return ArticleContent(
        title=better_title_from_markup(markup, fallback.title) or fallback.title,
        text=fallback.text,
        method=fallback.method,
    )


def extract_pdf(data: bytes) -> PdfContent:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PdfDependencyError("PyMuPDF (package 'pymupdf') is required to extract PDF text") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PdfParseError(f"Cannot open PDF: {exc}") from exc
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip() or None
        parts = []
        for page in doc:
            try:
                parts.append(page.get_text("text"))
            except Exception:
                continue
        return PdfContent(title=title, text="\n".join(p.strip() for p in parts if p and p.strip()), page_count=doc.page_count)
    finally:
        doc.close()


_SENTENCE_SPLIT = re.compile(r"(?<=[\.\?\!…])\s+(?=[A-ZÉÈÀÂÊÎÔÛÇ0-9«\"'(])")
_WORD = re.compile(r"[\w\-']+", re.UNICODE)
_STOPWORDS = frozenset("""
le la les un une des du de d l et ou mais donc or ni car que qui quoi dont ce cet cette ces son sa ses leur leurs il elle ils elles on nous vous je tu se me te y en à au aux pour par avec sans sur sous dans entre vers chez comme plus moins très trop pas ne n est sont été être avoir ont a ai as était étaient fait faire ça cela
the a an and or but if then else of in on for to from by with without is are was were be been being it this that these those he she they we you i his her their our as at so than such into about over under again further while during
""".split())


def extractive_summary(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if max_chars <= 0 or not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(normalized) if len(s.strip()) >= 20]
    if not sentences:
        return _trim(normalized, max_chars)
    freq: Counter[str] = Counter()
    tokenized: list[list[str]] = []
    for sent in sentences:
        words = [w.lower() for w in _WORD.findall(sent) if w.lower() not in _STOPWORDS]
        tokenized.append(words)
        freq.update(words)
    if not freq:
        return _trim(normalized, max_chars)
    max_freq = max(freq.values())
    scores = []
    for words in tokenized:
        scores.append(sum((freq[w] / max_freq) for w in words) / (len(words) ** 0.5) if words else 0.0)
    picked: set[int] = set()
    running = 0
    for idx in sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True):
        added = len(sentences[idx]) + (1 if picked else 0)
        if running + added > max_chars and picked:
            continue
        picked.add(idx)
        running += added
        if running >= max_chars:
            break
    return _trim(" ".join(sentences[i] for i in sorted(picked)), max_chars)


def _trim(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    for sep in (". ", "! ", "? ", "… ", "; "):
        idx = cut.rfind(sep)
        if idx >= int(max_chars * 0.5):
            return cut[: idx + 1].strip()
    space = cut.rfind(" ", 0, max_chars)
    return ((cut[:space] if space >= int(max_chars * 0.5) else cut[: max_chars - 1]) + "…").strip()


def article_boilerplate_reason(title: str | None, text: str) -> str | None:
    normalized_title = (title or "").strip().lower()
    normalized_text = " ".join((text or "").lower().split())
    if "javascript is not available" in normalized_title or "javascript is not available" in normalized_text:
        return "javascript-required page"
    social_stub_markers = [
        "log in sign up",
        "don’t miss what’s happening",
        "don't miss what's happening",
        "people on x are the first to know",
        "x.com needs javascript",
        "please wait for verification",
    ]
    if any(marker in normalized_text for marker in social_stub_markers) and len(normalized_text) < 1200:
        return "social/login/javascript stub"
    if normalized_title in {"page not found", "not found", "404"} or "page not found" in normalized_title:
        return "page-not-found title"
    cookie_markers = ["data collected and processed", "device characteristics", "device identifiers", "privacy choices", "cookie duration", "authentication-derived identifiers"]
    if sum(1 for marker in cookie_markers if marker in normalized_text) >= 3:
        return "cookie/consent wall markers"
    if len(normalized_text) < 180 and any(marker in normalized_text for marker in ["page not found", "could not find the page", "404"]):
        return "short error page"
    subscriber_markers = ["ce service est réservé aux abonnés", "ce service est reserve aux abonnes", "s'identifier", "s’identifier", "connectez-vous pour lire la suite"]
    if sum(1 for marker in subscriber_markers if marker in normalized_text) >= 2 and len(normalized_text) < 1200:
        return "subscriber-only teaser/paywall"
    compact_title = normalized_title.removeprefix("www.")
    compact_text = normalized_text.removeprefix("www.")
    if len(normalized_text) < 80 and compact_text and compact_text == compact_title:
        return "domain/title-only stub"
    return None


def describe_image_placeholder(url: str, content_type: str, size_bytes: int, max_chars: int) -> tuple[str, str]:
    parsed = urlparse(url)
    filename = parsed.path.rsplit("/", 1)[-1] or "(no filename)"
    size_kb = max(1, round(size_bytes / 1024))
    desc = (
        f"Image detected at {parsed.netloc or parsed.path} ({filename}). MIME type: "
        f"{(content_type.split(';', 1)[0] or 'image/unknown').lower()}. Size: ~{size_kb} KB. "
        "No vision model is configured, so no visual description is generated."
    )
    return filename, _trim(desc, max_chars)


def normalize_domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


class StrategyCache:
    def __init__(self, path: str | None = None):
        self.path = Path(path).expanduser() if path else None

    def get(self, url: str) -> StrategyRecord | None:
        if not self.path or not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = raw.get(normalize_domain(url))
        if not isinstance(value, dict):
            return None
        method = str(value.get("fetch_method") or "")
        if method not in {"http", "seo", "cloak", "cloak-profile", "archive", "fallback"}:
            return None
        return StrategyRecord(method, str(value.get("detail") or ""), int(value.get("success_count") or 0), int(value.get("failure_count") or 0), str(value.get("last_success_at") or ""), str(value.get("last_failure_at") or ""))

    def record_success(self, url: str, fetch_method: str, *, detail: str = "") -> None:
        if not self.path or fetch_method not in {"http", "seo", "cloak", "cloak-profile", "archive", "fallback"}:
            return
        domain = normalize_domain(url)
        if not domain:
            return
        data: dict[str, Any]
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            data = {}
        old = data.get(domain) if isinstance(data.get(domain), dict) else {}
        data[domain] = {
            "fetch_method": fetch_method,
            "detail": detail,
            "success_count": int(old.get("success_count") or 0) + 1,
            "failure_count": int(old.get("failure_count") or 0),
            "last_success_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)


def _fetch_method(resource: FetchedResource) -> str:
    return resource.headers.get("x-fetch-method", "http") if resource.headers else "http"


def _try_browser_resource(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    browser_fallback: bool,
    browser_profile_dir: str,
    browser_post_load_wait_ms: int,
    warnings: list[str],
) -> FetchedResource | None:
    if not browser_fallback:
        return None
    try:
        resource = fetch_with_browser(
            url,
            timeout=max(timeout, 60),
            max_bytes=max_bytes,
            profile_dir=browser_profile_dir,
            post_load_wait_ms=browser_post_load_wait_ms,
        )
        method = resource.headers.get("x-fetch-method", "cloak")
        warnings.append(f"browser fallback used: {method} (initial_status={resource.status_code})")
        return resource
    except Exception as browser_error:
        warnings.append(f"browser fallback failed: {browser_error}")
        return None


def _try_archive_resource(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    archive_fallback: bool,
    warnings: list[str],
) -> FetchedResource | None:
    if not archive_fallback:
        return None
    try:
        resource = fetch_from_archives(url, timeout=timeout, max_bytes=max_bytes)
        method = resource.headers.get("x-archive-method", "archive")
        archive_url = resource.headers.get("x-archive-url", "")
        warnings.append(f"archive fallback used: {method} ({archive_url})")
        return resource
    except FetchError as archive_error:
        warnings.append(str(archive_error))
        return None


def _fetch_with_pipeline(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    user_agent: str,
    seo_fallback: bool,
    strategy_cache_path: str | None,
    browser_fallback: bool,
    browser_profile_dir: str,
    browser_post_load_wait_ms: int,
    archive_fallback: bool,
    warnings: list[str],
) -> FetchedResource:
    cache = StrategyCache(strategy_cache_path)
    cached = cache.get(url)
    if cached and cached.fetch_method in {"cloak", "cloak-profile"}:
        profile = browser_profile_dir if cached.fetch_method == "cloak-profile" else ""
        warnings.append(f"strategy cache preferred: {cached.fetch_method}")
        resource = _try_browser_resource(url, timeout=timeout, max_bytes=max_bytes, browser_fallback=True, browser_profile_dir=profile, browser_post_load_wait_ms=browser_post_load_wait_ms, warnings=warnings)
        if resource is not None:
            cache.record_success(url, resource.headers.get("x-fetch-method", cached.fetch_method))
            return resource
        warnings.append("strategy cache preferred method failed; falling back to full pipeline")
    if cached and cached.fetch_method == "seo":
        warnings.append(f"strategy cache preferred: seo/{cached.detail}")
        try:
            resource = fetch_with_seo_variants(url, preferred_method=cached.detail or None, timeout=timeout, max_bytes=max_bytes)
            cache.record_success(url, "seo", detail=resource.headers.get("x-seo-method", ""))
            return resource
        except FetchError as exc:
            warnings.append(f"strategy cache preferred method failed: {exc}")
    if cached and cached.fetch_method == "archive":
        warnings.append(f"strategy cache preferred: archive/{cached.detail}")
        resource = _try_archive_resource(url, timeout=timeout, max_bytes=max_bytes, archive_fallback=True, warnings=warnings)
        if resource is not None:
            cache.record_success(url, "archive", detail=resource.headers.get("x-archive-method", ""))
            return resource
        warnings.append("strategy cache preferred method failed; falling back to full pipeline")

    try:
        resource = fetch_url(url, timeout=timeout, max_bytes=max_bytes, user_agent=user_agent)
        cache.record_success(url, "http")
        return resource
    except FetchError as first_error:
        if seo_fallback:
            try:
                resource = fetch_with_seo_variants(url, timeout=timeout, max_bytes=max_bytes)
                method = resource.headers.get("x-seo-method", "")
                warnings.append(f"seo fallback used: {method}")
                cache.record_success(url, "seo", detail=method)
                return resource
            except FetchError as seo_error:
                warnings.append(str(seo_error))
        resource = _try_browser_resource(url, timeout=timeout, max_bytes=max_bytes, browser_fallback=browser_fallback, browser_profile_dir=browser_profile_dir, browser_post_load_wait_ms=browser_post_load_wait_ms, warnings=warnings)
        if resource is not None:
            cache.record_success(url, resource.headers.get("x-fetch-method", "cloak"))
            return resource
        resource = _try_archive_resource(url, timeout=timeout, max_bytes=max_bytes, archive_fallback=archive_fallback, warnings=warnings)
        if resource is not None:
            cache.record_success(url, "archive", detail=resource.headers.get("x-archive-method", ""))
            return resource
        raise first_error


def read_url(
    url: str,
    *,
    length: int = 900,
    include_content: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
    seo_fallback: bool = True,
    strategy_cache_path: str | None = None,
    browser_fallback: bool = False,
    browser_profile_dir: str = "",
    browser_post_load_wait_ms: int = 8000,
    archive_fallback: bool = True,
) -> dict[str, Any]:
    warnings: list[str] = []
    max_chars = max(50, min(int(length or 900), 10_000))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"url": url, "content_type": "unknown", "title": None, "summary": "", "length": max_chars, "fetch_method": "http", "status": "error", "warnings": ["invalid http(s) URL"]}
    try:
        resource = _fetch_with_pipeline(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            user_agent=user_agent,
            seo_fallback=seo_fallback,
            strategy_cache_path=strategy_cache_path,
            browser_fallback=browser_fallback,
            browser_profile_dir=browser_profile_dir,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
            archive_fallback=archive_fallback,
            warnings=warnings,
        )
    except FetchError as exc:
        return {"url": url, "content_type": "unknown", "title": None, "summary": "", "length": max_chars, "fetch_method": "http", "status": "error", "warnings": warnings + [f"fetch failed: {exc}"]}

    content_type = detect_content_type(resource)
    fetch_method = _fetch_method(resource)
    if content_type == "article":
        article = extract_article(resource.text, resource.final_url)
        if is_generic_title(article.title) or "legifrance.gouv.fr" in normalize_domain(resource.final_url):
            first_line = next((line.strip(" -") for line in article.text.splitlines() if line.strip(" -")), "")
            if first_line.lower().startswith("article "):
                article = ArticleContent(title=_trim(first_line, 180), text=article.text, method=article.method)
        reason = article_boilerplate_reason(article.title, article.text)

        # Celeste-style second-stage fallback: many publishers return HTTP 200
        # with only a subscriber teaser/cookie wall. Treat that as a failed
        # extraction and reroute through browser, then archive/cache.
        if reason and fetch_method not in {"cloak", "cloak-profile", "browser", "archive"}:
            warnings.append(f"{fetch_method} article looked unusable ({reason}); trying browser/archive fallback")
            browser_resource = _try_browser_resource(url, timeout=timeout, max_bytes=max_bytes, browser_fallback=browser_fallback, browser_profile_dir=browser_profile_dir, browser_post_load_wait_ms=browser_post_load_wait_ms, warnings=warnings)
            if browser_resource is not None:
                browser_article = extract_article(browser_resource.text, browser_resource.final_url)
                browser_reason = article_boilerplate_reason(browser_article.title, browser_article.text)
                if not browser_reason:
                    resource = browser_resource
                    article = browser_article
                    reason = None
                    fetch_method = _fetch_method(resource)
                else:
                    warnings.append(f"browser content looked unusable ({browser_reason}); trying archive fallback")
            if reason:
                archive_resource = _try_archive_resource(url, timeout=timeout, max_bytes=max_bytes, archive_fallback=archive_fallback, warnings=warnings)
                if archive_resource is not None:
                    archive_article = extract_article(archive_resource.text, archive_resource.final_url)
                    archive_reason = article_boilerplate_reason(archive_article.title, archive_article.text)
                    if not archive_reason:
                        resource = archive_resource
                        article = archive_article
                        reason = None
                        fetch_method = _fetch_method(resource)
                    else:
                        warnings.append(f"archive content looked unusable ({archive_reason})")
        elif reason and fetch_method in {"cloak", "cloak-profile", "browser"}:
            warnings.append(f"browser content looked unusable ({reason}); trying archive fallback")
            archive_resource = _try_archive_resource(url, timeout=timeout, max_bytes=max_bytes, archive_fallback=archive_fallback, warnings=warnings)
            if archive_resource is not None:
                archive_article = extract_article(archive_resource.text, archive_resource.final_url)
                archive_reason = article_boilerplate_reason(archive_article.title, archive_article.text)
                if not archive_reason:
                    resource = archive_resource
                    article = archive_article
                    reason = None
                    fetch_method = _fetch_method(resource)
                else:
                    warnings.append(f"archive content looked unusable ({archive_reason})")

        if reason:
            warnings.append(f"article extraction looks like boilerplate/non-article content: {reason}")
            return {"url": resource.final_url, "content_type": "article", "title": article.title, "summary": "", "length": max_chars, "fetch_method": fetch_method, "status": "partial", "warnings": warnings, "content": article.text if include_content else None}
        summary = extractive_summary(article.text, max_chars)
        warnings.append(f"local extractive summary (method={article.method})")
        payload = {"url": resource.final_url, "content_type": "article", "title": article.title or extract_title(resource.text) or None, "summary": summary, "length": max_chars, "fetch_method": fetch_method, "status": "ok" if summary else "partial", "warnings": warnings, "image_url": extract_image_url(resource.text) or None}
        if include_content:
            payload["content"] = article.text
        return payload

    if content_type == "pdf":
        try:
            pdf = extract_pdf(resource.content)
        except (PdfDependencyError, PdfParseError) as exc:
            return {"url": resource.final_url, "content_type": "pdf", "title": None, "summary": "", "length": max_chars, "fetch_method": fetch_method, "status": "error", "warnings": warnings + [str(exc)]}
        if not pdf.text.strip():
            return {"url": resource.final_url, "content_type": "pdf", "title": pdf.title, "summary": "", "length": max_chars, "fetch_method": fetch_method, "status": "partial", "warnings": warnings + ["PDF parsed but no extractable text"]}
        summary = extractive_summary(pdf.text, max_chars)
        payload = {"url": resource.final_url, "content_type": "pdf", "title": pdf.title, "summary": summary, "length": max_chars, "fetch_method": fetch_method, "status": "ok" if summary else "partial", "warnings": warnings + [f"local extractive summary (pages={pdf.page_count})"]}
        if include_content:
            payload["content"] = pdf.text
        return payload

    if content_type == "image":
        title, summary = describe_image_placeholder(resource.final_url, resource.content_type, len(resource.content), max_chars)
        return {"url": resource.final_url, "content_type": "image", "title": title, "summary": summary, "length": max_chars, "fetch_method": fetch_method, "status": "ok", "warnings": warnings + ["placeholder image description (no vision provider configured)"]}

    return {"url": resource.final_url, "content_type": "unknown", "title": None, "summary": "", "length": max_chars, "fetch_method": fetch_method, "status": "error", "warnings": warnings + [f"unsupported content type: {resource.content_type!r}"]}


def to_markdown(result: dict[str, Any]) -> str:
    title = result.get("title") or result.get("url") or "Untitled"
    lines = [f"# {title}", "", f"URL: {result.get('url', '')}", f"Status: {result.get('status', '')}", f"Content type: {result.get('content_type', '')}", f"Fetch method: {result.get('fetch_method', '')}"]
    if result.get("warnings"):
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in result["warnings"]]
    if result.get("summary"):
        lines += ["", "## Summary", "", str(result["summary"])]
    if result.get("content"):
        lines += ["", "## Content", "", str(result["content"])]
    return "\n".join(lines).rstrip() + "\n"
