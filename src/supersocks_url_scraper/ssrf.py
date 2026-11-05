"""Fail-closed private-network guards for outbound URL fetches.

Resolve and validate the destination before opening a connection, then pin the
socket to a validated address. Reject loopback, link-local, private, CGNAT
(100.64.0.0/10), IPv6 ULA, cloud metadata, and embedded forms of those
addresses (IPv4-mapped, IPv4-compatible, NAT64, 6to4, Teredo). Redirect hops
are revalidated the same way. These fetches do not honor HTTP(S)_PROXY.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

BLOCKED_URL_WARNING = "blocked private or local URL"

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_ULA = ipaddress.ip_network("fc00::/7")
_LINK_LOCAL_V6 = ipaddress.ip_network("fe80::/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPAT = ipaddress.ip_network("::/96")
_METADATA_V4 = ipaddress.ip_address("169.254.169.254")


def _embedded_addresses(
    ip: ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address]:
    """Unwrap tunnel/mapped forms so the embedded IPv4 is what we policy-check."""
    mapped = ip.ipv4_mapped
    if mapped is not None:
        return [mapped]
    if ip in _IPV4_COMPAT and int(ip) > 1:
        return [ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)]
    if ip in _NAT64:
        return [ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)]
    sixtofour = ip.sixtofour
    if sixtofour is not None:
        return [sixtofour]
    teredo = ip.teredo
    if teredo is not None:
        server, client = teredo
        return [server, client]
    return []


def _literal_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _CGNAT or ip == _METADATA_V4:
            return True
    elif ip in _ULA or ip in _LINK_LOCAL_V6 or int(ip) == 0:
        return True
    return bool(
        (not ip.is_global)
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_addresses(ip)
        if embedded:
            return any(ip_is_blocked(item) for item in embedded)
    return _literal_is_blocked(ip)


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


def _safe_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Connect only to a sockaddr that was checked after DNS, never via a proxy.

    If any resolved address is blocked, refuse the whole host and do not connect.
    """
    host = address[0]
    port = address[1]
    if isinstance(host, str):
        host = host.strip().strip("[]")
    if not host:
        raise URLError(BLOCKED_URL_WARNING)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise URLError(BLOCKED_URL_WARNING) from exc
    if not infos:
        raise URLError(BLOCKED_URL_WARNING)
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise URLError(BLOCKED_URL_WARNING) from exc
        if ip_is_blocked(ip):
            raise URLError(BLOCKED_URL_WARNING)
    # Every address was checked above; try them in order like create_connection.
    last_error: OSError | None = None
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
        except OSError as exc:
            sock.close()
            last_error = exc
            continue
        except Exception:
            sock.close()
            raise
        return sock
    raise last_error if last_error is not None else URLError(BLOCKED_URL_WARNING)


class _SafeHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_create_connection


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_create_connection


class SafeHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class SafeHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        # Python 3.12 dropped HTTPSHandler._check_hostname and the
        # check_hostname argument; hostname checking lives on the context.
        return self.do_open(_SafeHTTPSConnection, req, context=self._context)


def safe_opener_handlers():
    """Handlers for direct reads: no proxy, pinned DNS, redirects rechecked."""
    return [
        ProxyHandler({}),
        SafeHTTPHandler(),
        SafeHTTPSHandler(),
        RevalidateRedirectHandler(),
    ]
