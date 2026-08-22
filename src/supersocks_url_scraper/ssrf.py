"""Fail-closed private-network guards for outbound URL fetches.

Resolve and validate the destination before opening a connection. Reject
loopback, link-local, RFC1918, IPv6 equivalents, cloud metadata, and any
host whose DNS cannot be resolved. Redirect hops are revalidated the same way.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler

BLOCKED_URL_WARNING = "blocked private or local URL"


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip == ipaddress.ip_address("169.254.169.254")
    )


def host_is_blocked(host: str) -> bool:
    hostname = (host or "").strip().lower().rstrip(".")
    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]
    if not hostname:
        return True
    if hostname in {"localhost", "metadata.google.internal"}:
        return True
    try:
        return ip_is_blocked(ipaddress.ip_address(hostname))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True
    if not infos:
        return True
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            if ip_is_blocked(ipaddress.ip_address(sockaddr[0])):
                return True
        except ValueError:
            return True
    return False


def url_is_blocked(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True
    return host_is_blocked(parsed.hostname or "")


class RevalidateRedirectHandler(HTTPRedirectHandler):
    max_repeats = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if url_is_blocked(newurl):
            raise URLError(BLOCKED_URL_WARNING)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
