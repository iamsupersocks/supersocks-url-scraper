"""Security gates for optional API recipes (read-only HTTPS GET).

Rules:
- GET only; no remote writes
- HTTPS only for recipe endpoints
- no Authorization / Cookie / token headers
- block private/loopback/link-local hosts (name + DNS)
- validate each redirect hop
- bound size, timeout, fanout, and cadence
- scrub sensitive headers from outputs
- surface 401/403/429 without retry loops
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPErrorProcessor, HTTPSHandler

from ..social.backend import redact_secrets
from ..social.domains import is_private_or_local_host, url_has_userinfo

SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "set-cookie2",
        "x-api-key",
        "x-auth-token",
        "x-access-token",
        "api-key",
        "apikey",
    }
)

BLOCKED_REQUEST_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "x-auth-token",
        "x-access-token",
        "api-key",
        "apikey",
    }
)

DEFAULT_USER_AGENT = "supersocks-url-scraper/0.2 (+api-recipe; read-only)"
DEFAULT_TIMEOUT = 8
DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_MAX_FANOUT = 8
DEFAULT_MIN_INTERVAL_MS = 50


class ApiRecipeSecurityError(RuntimeError):
    """Raised when a recipe request violates safety policy."""


@dataclass(frozen=True)
class SafeGetResult:
    url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    headers: dict[str, str]
    elapsed_ms: int


class _NoRedirect(HTTPErrorProcessor):
    """Leave redirects to the caller so each hop can be validated."""

    def http_response(self, request, response):  # noqa: ANN001
        return response

    https_response = http_response


def scrub_headers(headers: dict[str, str] | Any) -> dict[str, str]:
    """Return a lower-cased header map without sensitive values."""
    out: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else []
    for key, value in items:
        name = str(key).lower()
        if name in SENSITIVE_HEADER_NAMES:
            out[name] = "[REDACTED]"
            continue
        text = str(value)
        if re.search(r"(?i)(bearer\s+\S+|auth[_-]?token|api[_-]?key)", text):
            out[name] = "[REDACTED]"
        else:
            out[name] = text
    return out


def sanitize_request_headers(headers: dict[str, str] | None, *, user_agent: str = DEFAULT_USER_AGENT) -> dict[str, str]:
    cleaned: dict[str, str] = {"User-Agent": user_agent, "Accept": "application/json, text/plain, */*;q=0.1"}
    for key, value in (headers or {}).items():
        name = str(key)
        if name.lower() in BLOCKED_REQUEST_HEADER_NAMES:
            raise ApiRecipeSecurityError(f"blocked request header: {name}")
        if name.lower() == "user-agent":
            cleaned["User-Agent"] = str(value)
            continue
        if name.lower() == "accept":
            cleaned["Accept"] = str(value)
            continue
        # Allow only a small allowlist of non-auth headers (Referer/Origin/Accept-Language).
        if name.lower() in {"referer", "origin", "accept-language", "accept-encoding", "cache-control", "pragma"}:
            cleaned[name] = str(value)
            continue
        raise ApiRecipeSecurityError(f"disallowed request header: {name}")
    return cleaned


def host_is_blocked(hostname: str | None, *, resolve_dns: bool = True) -> bool:
    """True when hostname is missing, private/local, or resolves to a blocked address."""
    if not hostname or is_private_or_local_host(hostname):
        return True
    if not resolve_dns:
        return False
    host = hostname.strip(".")
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        infos = socket.getaddrinfo(candidate, None)
    except socket.gaierror as exc:
        raise ApiRecipeSecurityError(f"DNS resolution failed for host: {candidate}") from exc
    if not infos:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def assert_safe_https_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str] | None = None,
    resolve_dns: bool = True,
) -> None:
    if url_has_userinfo(url):
        raise ApiRecipeSecurityError("URL must not include credentials")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ApiRecipeSecurityError("API recipes require https URLs")
    host = parsed.hostname
    if host_is_blocked(host, resolve_dns=resolve_dns):
        raise ApiRecipeSecurityError("blocked private/loopback host")
    if allowed_hosts is not None:
        normalized = (host or "").lower().strip(".")
        allowed = {h.lower().strip(".") for h in allowed_hosts}
        if normalized not in allowed:
            raise ApiRecipeSecurityError(f"host not in recipe allowlist: {normalized}")


def safe_get(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    headers: dict[str, str] | None = None,
    allowed_hosts: frozenset[str] | set[str] | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    opener: Any | None = None,
    resolve_dns: bool = True,
) -> SafeGetResult:
    """Perform a bounded, read-only HTTPS GET with redirect validation."""
    assert_safe_https_url(url, allowed_hosts=allowed_hosts, resolve_dns=resolve_dns)
    request_headers = sanitize_request_headers(headers, user_agent=user_agent)
    current = url
    hops = 0
    started = time.monotonic()
    open_fn = opener
    if open_fn is None:
        open_fn = build_opener(_NoRedirect, HTTPSHandler()).open

    while True:
        assert_safe_https_url(current, allowed_hosts=allowed_hosts, resolve_dns=resolve_dns)
        request = Request(current, headers=request_headers, method="GET")
        try:
            with open_fn(request, timeout=max(1, int(timeout))) as response:
                status = int(getattr(response, "status", getattr(response, "code", 200)) or 200)
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location") or response.headers.get("location")
                    if not location:
                        raise ApiRecipeSecurityError(f"redirect {status} without Location")
                    hops += 1
                    if hops > max_redirects:
                        raise ApiRecipeSecurityError(f"too many redirects (>{max_redirects})")
                    # Absolute or relative Location
                    from urllib.parse import urljoin

                    current = urljoin(current, location)
                    continue
                if status in {401, 403, 429}:
                    raise ApiRecipeSecurityError(f"HTTP {status} (access/rate limited; not retried)")
                if status >= 400:
                    raise ApiRecipeSecurityError(f"HTTP {status}")
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ApiRecipeSecurityError(f"response exceeds max_bytes={max_bytes}")
                out_headers = scrub_headers({k.lower(): v for k, v in response.headers.items()})
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return SafeGetResult(
                    url=url,
                    final_url=getattr(response, "geturl", lambda: current)() or current,
                    status_code=status,
                    content=raw,
                    content_type=(response.headers.get("content-type") or "").lower(),
                    headers=out_headers,
                    elapsed_ms=elapsed_ms,
                )
        except ApiRecipeSecurityError:
            raise
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                raise ApiRecipeSecurityError(f"HTTP {exc.code} (access/rate limited; not retried)") from exc
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise ApiRecipeSecurityError(f"redirect {exc.code} without Location") from exc
                hops += 1
                if hops > max_redirects:
                    raise ApiRecipeSecurityError(f"too many redirects (>{max_redirects})") from exc
                from urllib.parse import urljoin

                current = urljoin(current, location)
                continue
            raise ApiRecipeSecurityError(f"HTTP {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ApiRecipeSecurityError(f"fetch failed: {redact_secrets(type(exc).__name__)}") from exc


class RateLimiter:
    """Simple process-local cadence guard for recipe fanout."""

    def __init__(self, min_interval_ms: int = DEFAULT_MIN_INTERVAL_MS):
        self.min_interval_ms = max(0, int(min_interval_ms))
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval_ms <= 0:
            return
        now = time.monotonic()
        elapsed = (now - self._last) * 1000
        remaining = self.min_interval_ms - elapsed
        if remaining > 0:
            time.sleep(remaining / 1000.0)
        self._last = time.monotonic()
