"""Strictly offline, opt-in API endpoint discovery from a local HAR file.

Given a HAR captured by a human in a browser, this module classifies every
HTTP(S) exchange and keeps only the ones that are safe, read-only, public API
candidates:

- HTTPS GET only (no writes, no POST/PUT/DELETE/PATCH)
- public host (no private/loopback/link-local, no credentials in URL)
- JSON response bodies (or a JSON-looking content-type)
- size-bounded (no excessive response bodies)
- no Authorization / Cookie / token headers

Everything else is excluded with a machine-readable reason. Sensitive query
params and headers are redacted from the report. The module never opens a
socket and never executes anything — it only reads a local file and emits a
report plus a *disabled* candidate recipe that must be manually reviewed before
it could ever run.

The candidate recipe is always emitted with ``network.mode`` in a blocked state
(``fixture_only`` / ``off``) and a ``status: review_required`` marker, so it can
never be executed or promoted automatically. Activation is a deliberate,
documented, human/agent step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse

from ..social.domains import is_private_or_local_host, url_has_userinfo
from .security import SENSITIVE_HEADER_NAMES

# Query parameter names whose values are considered sensitive and must be
# redacted from any report / candidate recipe.
SENSITIVE_PARAM_NAMES = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "api-key",
        "x-api-key",
        "key",
        "auth",
        "authorization",
        "auth_token",
        "auth-token",
        "session",
        "session_id",
        "sessionid",
        "cookie",
        "cookie_id",
        "csrftoken",
        "csrf_token",
        "xsrf",
        "xsrf_token",
        "secret",
        "password",
        "pass",
        "pwd",
        "signature",
        "sig",
        "signed",
        "signdata",
        "hmac",
        "jwt",
        "code",
        "client_secret",
        "client-secret",
        "grant_type",
        "refresh",
        "credential",
        "credentials",
        "password_hash",
        "oauth_token",
        "oauth_token_secret",
        "bearer",
        "private",
        "private_key",
    }
)

# Regex fallback: any query key containing these tokens is treated as sensitive.
SENSITIVE_PARAM_RE = re.compile(
    r"(?i)(token|api[_-]?key|auth|secret|password|passwd|pwd|credential|session|"
    r"cookie|jwt|bearer|signature|signed|hmac|private[_-]?key|client[_-]?secret|"
    r"refresh[_-]?token|access[_-]?token)"
)

SENSITIVE_HEADER_RE = re.compile(
    r"(?i)(^|[-_])(token|auth|authorization|secret|cookie|credential|session)($|[-_])|"
    r"api[-_]?key|bearer"
)

# Reason codes for excludable entries.
REASON_WRITE_METHOD = "excluded: non-GET method"
REASON_NON_HTTPS = "excluded: not https"
REASON_PRIVATE_HOST = "excluded: private/loopback/local host"
REASON_URL_CREDENTIALS = "excluded: credentials in URL"
REASON_NON_JSON = "excluded: response is not JSON"
REASON_TOO_LARGE = "excluded: response body too large"
REASON_ERROR_STATUS = "excluded: HTTP >= 400"
REASON_REDIRECT_STATUS = "excluded: redirect 3xx found"
REASON_NO_RESPONSE = "excluded: no response recorded"
REASON_SENSITIVE_HEADER = "excluded: sensitive header present"
REASON_SENSITIVE_PARAM = "excluded: sensitive query param present"

DEFAULT_MAX_ENTRY_BYTES = 512 * 1024
DEFAULT_MAX_REPORT_CANDIDATES = 50

JSON_CONTENT_TYPES = (
    "application/json",
    "application/ld+json",
    "application/hal+json",
    "application/vnd.api+json",
    "text/json",
)


@dataclass(frozen=True)
class CandidateEntry:
    """A single classified HAR exchange."""

    url: str
    method: str
    status_code: int
    content_type: str
    size_bytes: int
    classification: str  # "candidate" | "excluded"
    reason: str
    host: str = ""
    redacted_url: str = ""
    redacted_headers: dict[str, str] = field(default_factory=dict)
    sensitive_query_keys: tuple[str, ...] = ()
    sensitive_headers: tuple[str, ...] = ()
    json_keys: tuple[str, ...] = ()
    score: float = 0.0
    score_reasons: tuple[str, ...] = ()


@dataclass
class DiscoveryReport:
    """Classified result of a HAR discovery run."""

    source_har: str
    generated_at: str
    total_entries: int
    candidates: list[CandidateEntry] = field(default_factory=list)
    excluded: list[CandidateEntry] = field(default_factory=list)
    candidate_recipe: dict[str, Any] | None = None
    source_url: str | None = None

    def counts(self) -> dict[str, int]:
        return {
            "total_entries": self.total_entries,
            "candidates": len(self.candidates),
            "excluded": len(self.excluded),
        }


_NOISE_TOKENS = (
    "consent",
    "cookie",
    "ads",
    "analytics",
    "collect",
    "telemetry",
    "metrics",
    "geolocation",
    "manifest",
    "banner",
)


def infer_source_url_from_har(har: dict[str, Any]) -> str | None:
    """Infer the final page/document URL when none was supplied.

    HAR files commonly contain the landing page followed by the page the user
    actually inspected, plus iframe documents. Prefer documents on the initial
    page host, then the last query-bearing document because its public
    identifiers are useful for ranking.
    """
    document_urls: list[str] = []
    for entry in iter_har_entries(har):
        if str(entry.get("_resourceType") or "").lower() == "document":
            req = entry.get("request")
            if isinstance(req, dict):
                url = str(req.get("url") or "").strip()
                if url.startswith("https://"):
                    document_urls.append(url)
    if document_urls:
        initial_host = urlparse(document_urls[0]).hostname
        same_host = [url for url in document_urls if urlparse(url).hostname == initial_host]
        page_urls = same_host or document_urls
        with_query = [url for url in page_urls if urlparse(url).query]
        return (with_query or page_urls)[-1]

    html_urls: list[str] = []
    for entry in iter_har_entries(har):
        req = entry.get("request")
        if not isinstance(req, dict) or str(req.get("method") or "").upper() != "GET":
            continue
        url = str(req.get("url") or "").strip()
        if not url.startswith("https://"):
            continue
        resp = entry.get("response")
        content = resp.get("content") if isinstance(resp, dict) else {}
        mime = _norm_content_type(str(content.get("mimeType") or "") if isinstance(content, dict) else "")
        if "text/html" in mime:
            html_urls.append(url)
    if not html_urls:
        return None
    with_query = [url for url in html_urls if urlparse(url).query]
    return (with_query or html_urls)[-1]


def _family_key(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    segments: list[str] = []
    for segment in (parsed.path or "").split("/"):
        if not segment:
            continue
        if segment.isdigit() or len(segment) >= 8 or re.fullmatch(r"[A-Za-z0-9_-]{6,}", segment):
            segments.append("{}")
        else:
            segments.append(segment.lower())
    return (parsed.hostname or "", "/".join(segments))


def _family_counts(candidates: list[CandidateEntry]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for entry in candidates:
        key = _family_key(entry.url or entry.redacted_url)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _non_sensitive_query_values(source_url: str) -> list[str]:
    parsed = urlparse(source_url)
    values: list[str] = []
    for key, value in parse_qsl(parsed.query or "", keep_blank_values=True):
        if key.lower() in SENSITIVE_PARAM_NAMES or SENSITIVE_PARAM_RE.search(key):
            continue
        text = str(value).strip()
        if len(text) >= 2:
            values.append(text)
    return values


def score_candidate(
    entry: CandidateEntry,
    *,
    source_url: str | None = None,
    family_counts: dict[tuple[str, str], int] | None = None,
) -> tuple[float, list[str]]:
    """Non-blocking heuristic ranking for API-like HAR candidates."""
    score = 0.0
    reasons: list[str] = []
    url = entry.redacted_url or entry.url
    parsed = urlparse(url)
    haystack = f"{parsed.path}?{parsed.query}".lower()

    for token in _NOISE_TOKENS:
        if token in haystack:
            score -= 40.0
            reasons.append(f"penalty:{token}")

    if source_url:
        endpoint_text = url.lower()
        endpoint_qs = dict(parse_qsl(parsed.query or "", keep_blank_values=True))
        for value in _non_sensitive_query_values(source_url):
            if value.lower() in endpoint_text:
                score += 120.0
                reasons.append(f"source_value_match:{value[:24]}")
                break
            if any(value == str(existing) for existing in endpoint_qs.values()):
                score += 120.0
                reasons.append(f"source_value_match:{value[:24]}")
                break

    if family_counts:
        family = _family_key(url)
        repeat = family_counts.get(family, 0)
        if repeat >= 2:
            # Repetition is a useful pattern signal, not an unlimited vote.
            # Capping it prevents telemetry or ad families from dominating a
            # source-linked endpoint merely because they are very chatty.
            bonus = min(35.0 * float(repeat - 1), 140.0)
            score += bonus
            reasons.append(f"family_repeat:{repeat}")

    score += min(entry.size_bytes / 20000.0, 5.0)
    return score, reasons


def rank_candidates(
    candidates: list[CandidateEntry],
    *,
    source_url: str | None = None,
) -> list[CandidateEntry]:
    """Return candidates sorted by score (desc), then size (desc)."""
    families = _family_counts(candidates)
    ranked: list[CandidateEntry] = []
    for entry in candidates:
        score, reasons = score_candidate(entry, source_url=source_url, family_counts=families)
        ranked.append(
            CandidateEntry(
                url=entry.url,
                method=entry.method,
                status_code=entry.status_code,
                content_type=entry.content_type,
                size_bytes=entry.size_bytes,
                classification=entry.classification,
                reason=entry.reason,
                host=entry.host,
                redacted_url=entry.redacted_url,
                redacted_headers=entry.redacted_headers,
                sensitive_query_keys=entry.sensitive_query_keys,
                sensitive_headers=entry.sensitive_headers,
                json_keys=entry.json_keys,
                score=score,
                score_reasons=tuple(reasons),
            )
        )
    ranked.sort(key=lambda item: (item.score, item.size_bytes), reverse=True)
    return ranked


def _norm_content_type(raw: str) -> str:
    return (raw or "").split(";", 1)[0].strip().lower()


def _is_json_content_type(ctype: str) -> bool:
    ctype = _norm_content_type(ctype)
    if ctype in JSON_CONTENT_TYPES:
        return True
    if ctype.endswith("+json"):
        return True
    return False


def _looks_like_json(raw: bytes) -> bool:
    head = (raw or b"")[:4096].lstrip()
    if not head:
        return False
    return head.startswith((b"{", b"["))


def _classify_query(url: str) -> tuple[bool, tuple[str, ...]]:
    """Return (has_sensitive, sensitive_keys)."""
    parsed = urlparse(url)
    if not parsed.query:
        return False, ()
    sensitive: list[str] = []
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_PARAM_NAMES or SENSITIVE_PARAM_RE.search(key):
            sensitive.append(key)
    return bool(sensitive), tuple(sorted(set(sensitive)))


def redact_query(url: str) -> str:
    """Replace sensitive query-param values and drop fragments from reports."""
    parsed = urlparse(url)
    pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_PARAM_NAMES or SENSITIVE_PARAM_RE.search(key):
            pairs.append((key, "[REDACTED]"))
        else:
            pairs.append((key, value))
    new_query = urlencode(pairs, doseq=True)
    # Keep the sentinel readable in outputs (urlencode would percent-encode '[REDACTED]').
    new_query = new_query.replace("%5BREDACTED%5D", "[REDACTED]")
    # Never echo userinfo from a rejected HAR URL. Preserve an explicit port
    # while rebuilding a credential-free netloc.
    hostname = parsed.hostname or ""
    safe_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    try:
        safe_netloc = f"{safe_host}:{parsed.port}" if parsed.port is not None else safe_host
    except ValueError:
        safe_netloc = safe_host
    return parsed._replace(netloc=safe_netloc, query=new_query, fragment="").geturl()


def _sensitive_header_names(headers: dict[str, str] | None) -> tuple[str, ...]:
    if not headers:
        return ()
    found: list[str] = []
    for name in headers:
        lowered = str(name).lower()
        if lowered in SENSITIVE_HEADER_NAMES or SENSITIVE_HEADER_RE.search(lowered):
            found.append(lowered)
    return tuple(sorted(set(found)))


def _redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        lowered = str(name).lower()
        if lowered in SENSITIVE_HEADER_NAMES or SENSITIVE_HEADER_RE.search(lowered):
            out[lowered] = "[REDACTED]"
        else:
            out[str(name)] = (
                "[REDACTED]"
                if re.search(r"(?i)(bearer\s+\S+|auth[_-]?token|api[_-]?key)", str(value))
                else str(value)
            )
    return out


def iter_har_entries(har: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield each HAR 1.2 exchange entry (request + response)."""
    log = har.get("log") if isinstance(har, dict) else None
    if not isinstance(log, dict):
        return
    entries = log.get("entries")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if isinstance(entry, dict):
            yield entry


def load_har(path: str | Path) -> dict[str, Any]:
    """Load and parse a HAR file, raising ValueError on malformed input."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid HAR JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("log"), dict):
        raise ValueError("HAR must contain a top-level 'log' object")
    return data


def _json_keys_of_response(content: bytes) -> tuple[str, ...]:
    """Top-level JSON object keys (bounded) for the report, if any."""
    try:
        value = json.loads((content or b"")[: 256 * 1024].decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ()
    if isinstance(value, dict):
        return tuple(str(k) for k in list(value.keys())[:12])
    if isinstance(value, list):
        return ("<array>",)
    return ()


def classify_har_entry(
    entry: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
) -> CandidateEntry:
    """Classify one HAR exchange into a candidate or an excluded entry."""
    request = entry.get("request")
    response = entry.get("response")

    if not isinstance(request, dict):
        return CandidateEntry(
            url="",
            method="?",
            status_code=0,
            content_type="",
            size_bytes=0,
            classification="excluded",
            reason=REASON_NO_RESPONSE,
        )

    url = str(request.get("url") or "")
    method = str(request.get("method") or "").upper()
    parsed = urlparse(url)
    host = parsed.hostname or ""

    def excluded(reason: str, **kw: Any) -> CandidateEntry:
        return CandidateEntry(
            url=url,
            method=method,
            status_code=int(kw.get("status_code", 0)),
            content_type=str(kw.get("content_type", "")),
            size_bytes=int(kw.get("size_bytes", 0)),
            classification="excluded",
            reason=reason,
            host=host,
            redacted_url=redact_query(url),
            redacted_headers=kw.get("redacted_headers", {}),
            sensitive_query_keys=kw.get("sensitive_query_keys", ()),
            sensitive_headers=kw.get("sensitive_headers", ()),
        )

    # Method gate (GET only).
    if method != "GET":
        return excluded(REASON_WRITE_METHOD)

    # Scheme gate (HTTPS only).
    if parsed.scheme != "https":
        return excluded(REASON_NON_HTTPS)

    # Host gate (public only, no credentials).
    if url_has_userinfo(url):
        return excluded(REASON_URL_CREDENTIALS)
    if not host or is_private_or_local_host(host):
        return excluded(REASON_PRIVATE_HOST)

    # Header gate (no auth/cookie/token headers).
    request_headers = request.get("headers")
    header_map: dict[str, str] = {}
    if isinstance(request_headers, list):
        for h in request_headers:
            if isinstance(h, dict) and h.get("name"):
                header_map[str(h["name"])] = str(h.get("value") or "")
    sensitive_headers = _sensitive_header_names(header_map)
    request_cookies = request.get("cookies")
    if isinstance(request_cookies, list) and request_cookies:
        sensitive_headers = tuple(sorted(set(sensitive_headers) | {"cookie"}))
    if sensitive_headers:
        return excluded(
            REASON_SENSITIVE_HEADER,
            sensitive_headers=sensitive_headers,
            redacted_headers=_redact_headers(header_map),
        )

    # Query gate (no sensitive params).
    has_sensitive_param, sensitive_params = _classify_query(url)
    if has_sensitive_param:
        return excluded(
            REASON_SENSITIVE_PARAM,
            sensitive_query_keys=sensitive_params,
            redacted_headers=_redact_headers(header_map),
        )

    # Response gates.
    if not isinstance(response, dict):
        return excluded(REASON_NO_RESPONSE)

    status = int(response.get("status") or 0)
    if status != 0 and status >= 400:
        return excluded(REASON_ERROR_STATUS, status_code=status)
    if status in {301, 302, 303, 307, 308}:
        return excluded(REASON_REDIRECT_STATUS, status_code=status)

    content_obj = response.get("content")
    content_bytes: bytes = b""
    content_size = 0
    if isinstance(content_obj, dict):
        content_size = int(content_obj.get("size") or 0)
        text = content_obj.get("text")
        if isinstance(text, str):
            content_bytes = text.encode("utf-8")
        else:
            content_bytes = content_obj.get("_bytes") or b""
    if content_size == 0 and content_bytes:
        content_size = len(content_bytes)

    ctype = ""
    response_headers = response.get("headers")
    res_header_map: dict[str, str] = {}
    if isinstance(response_headers, list):
        for h in response_headers:
            if isinstance(h, dict) and h.get("name"):
                res_header_map[str(h["name"])] = str(h.get("value") or "")
    ctype = _norm_content_type(res_header_map.get("content-type", ""))
    if not ctype:
        ctype = _norm_content_type(str(content_obj.get("mimeType") or "") if isinstance(content_obj, dict) else "")

    if content_size > max_bytes:
        return excluded(REASON_TOO_LARGE, status_code=status, content_type=ctype, size_bytes=content_size)

    if not _is_json_content_type(ctype):
        # Allow a JSON-looking body even without a JSON content-type.
        if not _looks_like_json(content_bytes):
            return excluded(REASON_NON_JSON, status_code=status, content_type=ctype, size_bytes=content_size)

    json_keys = _json_keys_of_response(content_bytes)

    return CandidateEntry(
        url=url,
        method=method,
        status_code=status,
        content_type=ctype,
        size_bytes=content_size,
        classification="candidate",
        reason="candidate: public HTTPS GET JSON",
        host=host,
        redacted_url=redact_query(url),
        redacted_headers=_redact_headers(header_map),
        sensitive_query_keys=(),
        sensitive_headers=(),
        json_keys=json_keys,
    )


def _derive_recipe_id(host: str, url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", host.split(".")[0].lower()).strip("-")
    path_slug = re.sub(r"[^a-z0-9]+", "-", (urlparse(url).path or "").strip("/").lower()).strip("-")
    tail = (path_slug or "root")[:40]
    return f"har-{slug}-{tail}"[:80]


def build_candidate_recipe(entry: CandidateEntry) -> dict[str, Any]:
    """Build a *disabled* candidate recipe from a discovered entry.

    Always blocked: ``network.mode`` is ``fixture_only`` and ``status`` is
    ``review_required``. Nothing here can execute or self-promote.
    """
    if entry.classification != "candidate":
        raise ValueError("candidate recipe requires a classified safe candidate entry")
    parsed = urlparse(entry.redacted_url or entry.url)
    host = parsed.hostname or entry.host or "unknown"
    # Keep matching exact and conservative. Guessing a registrable root from the
    # last two labels would turn api.example.co.uk into the dangerously broad
    # co.uk. An operator can widen host_roots deliberately during review.
    root = host.lower()
    path = parsed.path or ""
    path_regex = ""
    if path and path.strip("/"):
        path_regex = path.strip("/")[:80]
    id_ = _derive_recipe_id(host, entry.url)
    return {
        "id": id_,
        "version": "1",
        "title": f"Discovered public JSON endpoint on {host} (review_required)",
        "schema": "generic_json_v1",
        "confidence": 0.3,
        "ttl_seconds": 300,
        "status": "review_required",
        "review_required": True,
        "network": {
            "mode": "fixture_only",
            "consent_phrase": "I_HAVE_EXPRESS_WRITTEN_PERMISSION",
            "notes": (
                "Candidate discovered offline from a local HAR. Never executed or "
                "promoted automatically. Requires human/agent review before any "
                "activation, and remains read-only HTTPS GET (no auth headers)."
            ),
        },
        "match": {
            "host_roots": [root],
            "path_regex": path_regex,
        },
        "endpoint": {
            "method": "GET",
            "url_template": entry.redacted_url or entry.url,
            "allowed_hosts": [host],
            "timeout_seconds": 8,
            "max_bytes": min(entry.size_bytes or 65536, DEFAULT_MAX_ENTRY_BYTES),
            "max_fanout": 1,
            "min_interval_ms": 50,
            "max_redirects": 2,
            "headers": {},
        },
        "warnings": [
            "Candidate discovered offline from a HAR; undocumented endpoint may change.",
            "Review_required: verify the endpoint is public, read-only, and within the "
            "site's terms of service before any activation.",
            "No Authorization/Cookie/token headers are permitted.",
        ],
        "fallback": "http_seo_cloak_archive",
        "response": {
            "schema": "generic_json_v1",
            "json_keys": list(entry.json_keys),
        },
        "discovery": {
            "source": "har",
            "url": entry.redacted_url or entry.url,
            "status_code": entry.status_code,
            "content_type": entry.content_type,
            "size_bytes": entry.size_bytes,
            "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
    }


def discover_from_har(
    path: str | Path,
    *,
    source_url: str | None = None,
    max_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    max_candidates: int = DEFAULT_MAX_REPORT_CANDIDATES,
    build_recipe: bool = True,
) -> DiscoveryReport:
    """Run offline discovery over a HAR file and return a classified report."""
    har = load_har(path)
    inferred_source = (source_url or "").strip() or infer_source_url_from_har(har)
    candidates: list[CandidateEntry] = []
    excluded: list[CandidateEntry] = []
    total = 0
    for entry in iter_har_entries(har):
        total += 1
        classified = classify_har_entry(entry, max_bytes=max_bytes)
        if classified.classification == "candidate":
            candidates.append(classified)
        else:
            excluded.append(classified)

    candidates = rank_candidates(candidates, source_url=inferred_source or None)
    candidates = candidates[: max(0, int(max_candidates))]

    report = DiscoveryReport(
        source_har=str(path),
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        total_entries=total,
        candidates=candidates,
        excluded=excluded,
        source_url=inferred_source or None,
    )
    if build_recipe and candidates:
        report.candidate_recipe = build_candidate_recipe(candidates[0])
    return report


def render_report_json(report: DiscoveryReport) -> str:
    """Serialize a discovery report to JSON (redacted)."""
    payload = {
        "source_har": report.source_har,
        "generated_at": report.generated_at,
        "source_url": report.source_url,
        "counts": report.counts(),
        "candidates": [
            {
                "url": c.redacted_url or c.url,
                "host": c.host,
                "method": c.method,
                "status_code": c.status_code,
                "content_type": c.content_type,
                "size_bytes": c.size_bytes,
                "json_keys": list(c.json_keys),
                "score": c.score,
                "score_reasons": list(c.score_reasons),
            }
            for c in report.candidates
        ],
        "excluded": [
            {
                "url": c.redacted_url or c.url,
                "reason": c.reason,
                "sensitive_query_keys": list(c.sensitive_query_keys),
                "sensitive_headers": list(c.sensitive_headers),
            }
            for c in report.excluded
        ],
        "candidate_recipe": report.candidate_recipe,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report_markdown(report: DiscoveryReport) -> str:
    """Render a discovery report as Markdown (redacted)."""
    lines: list[str] = []
    lines.append("# API discovery report (offline HAR)")
    lines.append("")
    lines.append(f"- Source HAR: `{_md_escape(report.source_har)}`")
    lines.append(f"- Generated: `{report.generated_at}`")
    if report.source_url:
        lines.append(f"- Source URL: `{_md_escape(report.source_url)}`")
    lines.append(f"- Entries scanned: **{report.total_entries}**")
    lines.append(f"- Candidates kept: **{len(report.candidates)}**")
    lines.append(f"- Excluded: **{len(report.excluded)}**")
    lines.append("")
    lines.append("## Candidates (public HTTPS GET JSON)")
    lines.append("")
    if not report.candidates:
        lines.append("_No safe candidates found._")
    else:
        lines.append("| # | Host | Method | Status | Size | Score | Content-Type | JSON keys |")
        lines.append("|---|------|--------|--------|------|-------|--------------|-----------|")
        for i, c in enumerate(report.candidates, 1):
            keys = ", ".join(c.json_keys[:6]) if c.json_keys else "—"
            lines.append(
                f"| {i} | `{_md_escape(c.host)}` | {c.method} | {c.status_code} | "
                f"{c.size_bytes} | {c.score:.1f} | {_md_escape(c.content_type)} | `{_md_escape(keys)}` |"
            )
        lines.append("")
        lines.append("### Candidate URLs (query params redacted)")
        lines.append("")
        for i, c in enumerate(report.candidates, 1):
            lines.append(f"{i}. `{_md_escape(c.redacted_url or c.url)}`")
    lines.append("")
    lines.append("## Excluded exchanges")
    lines.append("")
    if not report.excluded:
        lines.append("_No excluded exchanges._")
    else:
        from collections import Counter

        by_reason = Counter(c.reason for c in report.excluded)
        for reason, count in by_reason.most_common():
            lines.append(f"- **{count}×** {_md_escape(reason)}")
    lines.append("")
    if report.candidate_recipe:
        lines.append("## Candidate recipe (disabled / review_required)")
        lines.append("")
        rec = report.candidate_recipe
        lines.append(
            f"- id: `{rec.get('id')}` · status: **{rec.get('status')}** · "
            f"network.mode: `{rec.get('network', {}).get('mode')}`"
        )
        lines.append("- This recipe is **disabled** and will never execute or be promoted "
                     "automatically. It must be reviewed and explicitly activated by an operator.")
    return "\n".join(lines) + "\n"


def write_report(
    report: DiscoveryReport,
    *,
    out_dir: str | Path,
    prefix: str = "discovery",
) -> dict[str, str]:
    """Write JSON + Markdown reports and a candidate recipe to out_dir.

    Returns a map of written file paths keyed by kind.
    """
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = dest / f"{prefix}-{stamp}.json"
    md_path = dest / f"{prefix}-{stamp}.md"
    json_path.write_text(render_report_json(report), encoding="utf-8")
    md_path.write_text(render_report_markdown(report), encoding="utf-8")
    written: dict[str, str] = {"json": str(json_path), "markdown": str(md_path)}
    if report.candidate_recipe:
        recipe_path = dest / f"{prefix}-candidate-recipe.v{report.candidate_recipe.get('version', '1')}.json"
        recipe_path.write_text(
            json.dumps(report.candidate_recipe, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written["recipe"] = str(recipe_path)
    return written
