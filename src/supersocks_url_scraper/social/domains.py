"""Robust social-domain helpers.

Matching rules:
- Exact host or real subdomain of an allowlisted root (no suffix lookalikes).
- Reject URLs that carry userinfo/credentials.
- Reject non-HTTP(S) schemes for social routing.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_PLATFORM_ROOTS: dict[str, frozenset[str]] = {
    "youtube": frozenset({"youtube.com", "youtu.be"}),
    "linkedin": frozenset({"linkedin.com"}),
}


def _normalized_host(hostname: str | None) -> str:
    host = (hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def host_matches_root(hostname: str | None, root: str) -> bool:
    """True only for exact root or a subdomain of root (never suffix lookalikes)."""
    host = _normalized_host(hostname)
    root = root.lower().strip(".")
    if not host or not root:
        return False
    return host == root or host.endswith("." + root)


def url_has_userinfo(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""))


def is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_private_or_local_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().strip(".")
    if not host:
        return True
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        return True
    # Strip IPv6 brackets if present.
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # Hostnames that look like dotted numbers but are invalid are treated as non-private DNS names.
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", candidate):
            return True
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def detect_platform(url: str) -> str | None:
    """Return platform id when URL is a clean public social host, else None."""
    if not is_http_url(url) or url_has_userinfo(url):
        return None
    host = urlparse(url).hostname
    if is_private_or_local_host(host):
        return None
    for platform, roots in _PLATFORM_ROOTS.items():
        if any(host_matches_root(host, root) for root in roots):
            return platform
    return None


def is_safe_public_http_url(url: str) -> bool:
    """Strict safety gate for external readers (no credentials, no private hosts)."""
    if not is_http_url(url) or url_has_userinfo(url):
        return False
    parsed = urlparse(url)
    return not is_private_or_local_host(parsed.hostname)
