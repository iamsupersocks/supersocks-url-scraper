from __future__ import annotations

import ipaddress
import socket
from urllib.error import URLError
from urllib.request import ProxyHandler
from unittest.mock import Mock

import pytest

from supersocks_url_scraper import reader
from supersocks_url_scraper.browser_fetcher import BrowserFetchError, fetch_with_cloak
from supersocks_url_scraper.reader import FetchError, fetch_url, fetch_with_browser, read_url
from supersocks_url_scraper.social.domains import is_private_or_local_host
from supersocks_url_scraper.social.youtube import _fetch_text
from supersocks_url_scraper.ssrf import (
    BLOCKED_URL_WARNING,
    RevalidateRedirectHandler,
    host_is_blocked,
    ip_is_blocked,
    safe_opener_handlers,
    url_is_blocked,
)


def _teredo(server: str, client: str) -> ipaddress.IPv6Address:
    server_ip = int(ipaddress.IPv4Address(server))
    client_ip = int(ipaddress.IPv4Address(client))
    value = (0x20010000 << 96) | (server_ip << 64) | ((~client_ip) & 0xFFFFFFFF)
    return ipaddress.IPv6Address(value)


def test_ip_literals_blocked() -> None:
    blocked = (
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "192.168.1.10",
        "172.16.4.4",
        "169.254.1.1",
        "169.254.169.254",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "100.127.255.255",
        "224.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:100.64.0.1",
        "fd00::1",
        "fc00::1",
        "fe80::1",
        "64:ff9b::a00:1",
        "::10.0.0.1",
        "2002:0a00:0001::",
        str(_teredo("8.8.8.8", "10.0.0.1")),
    )
    allowed = (
        "8.8.8.8",
        "1.1.1.1",
        "2001:4860:4860::8888",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "::8.8.8.8",
        "2002:0808:0808::",
        str(_teredo("8.8.8.8", "1.1.1.1")),
    )
    for raw in blocked:
        assert ip_is_blocked(ipaddress.ip_address(raw)), raw
    for raw in allowed:
        assert not ip_is_blocked(ipaddress.ip_address(raw)), raw


def test_url_literals_blocked_without_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked_urls = (
        "http://127.0.0.1/",
        "http://localhost/status",
        "http://169.254.169.254/",
        "http://192.168.0.5/admin",
        "http://10.0.0.8/",
        "http://[::1]/",
        "file:///etc/passwd",
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("blocked URL must not open a connection")

    monkeypatch.setattr(reader, "build_opener", boom)
    for url in blocked_urls:
        assert url_is_blocked(url), url
        with pytest.raises(FetchError, match=BLOCKED_URL_WARNING if url.startswith("http") else "invalid"):
            fetch_url(url)
        result = read_url(url)
        assert result["status"] == "error"
        assert result["warnings"]


def test_resolved_private_dns_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.ssrf.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("10.0.0.9", 0))],
    )
    assert host_is_blocked("news.example")
    assert url_is_blocked("https://news.example/story")


def test_unresolved_dns_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_dns(*_args: object, **_kwargs: object) -> None:
        raise OSError("name resolution failed")

    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.getaddrinfo", fail_dns)
    assert host_is_blocked("unknown.example")
    assert url_is_blocked("https://unknown.example/")


def test_public_dns_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.ssrf.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("8.8.8.8", 0))],
    )
    assert not host_is_blocked("news.example")
    assert not url_is_blocked("https://news.example/story")


def test_redirect_handler_revalidates_target() -> None:
    handler = RevalidateRedirectHandler()
    with pytest.raises(URLError, match=BLOCKED_URL_WARNING):
        handler.redirect_request(
            req=Mock(),
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="http://127.0.0.1/secret",
        )


def test_fetch_url_does_not_open_blocked_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.ssrf.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("1.1.1.1", 0))],
    )

    class FakeOpener:
        def open(self, *_args: object, **_kwargs: object) -> None:
            raise URLError(BLOCKED_URL_WARNING)

    monkeypatch.setattr(reader, "build_opener", lambda *_args, **_kwargs: FakeOpener())
    with pytest.raises(FetchError, match=BLOCKED_URL_WARNING):
        fetch_url("https://news.example/story")


def test_fetch_with_browser_rejects_private_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("browser must not open a blocked URL")

    monkeypatch.setattr("supersocks_url_scraper.browser_fetcher.fetch_with_cloak", boom)
    with pytest.raises(FetchError, match=BLOCKED_URL_WARNING):
        fetch_with_browser("http://127.0.0.1/")


def test_redirect_refuses_cgnat_ula_and_mapped_targets() -> None:
    handler = RevalidateRedirectHandler()
    for newurl in ("http://100.64.0.1/latest", "http://[fd00::1]/", "http://[::ffff:10.0.0.1]/"):
        with pytest.raises(URLError, match=BLOCKED_URL_WARNING):
            handler.redirect_request(
                req=Mock(),
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl=newurl,
            )


def test_redirect_to_name_resolving_private_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.ssrf.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))],
    )
    handler = RevalidateRedirectHandler()
    with pytest.raises(URLError, match=BLOCKED_URL_WARNING):
        handler.redirect_request(
            req=Mock(),
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://cdn.example/next",
        )


def test_domains_use_shared_block_rule() -> None:
    assert is_private_or_local_host("100.64.0.1")
    assert is_private_or_local_host("::ffff:10.0.0.1")
    assert is_private_or_local_host("fd00::1")
    assert not is_private_or_local_host("8.8.8.8")


def test_safe_opener_disables_proxies_and_revalidates_redirects() -> None:
    handlers = safe_opener_handlers()
    proxy = next(handler for handler in handlers if isinstance(handler, ProxyHandler))
    assert proxy.proxies == {}
    assert any(isinstance(handler, RevalidateRedirectHandler) for handler in handlers)


def test_fetch_url_pins_dns_and_refuses_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if port is None:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", port))]

    connected: list[object] = []

    class GuardSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, *args):
            pass

        def bind(self, *args):
            pass

        def connect(self, address):
            connected.append(address)
            raise AssertionError("blocked host must not connect")

        def close(self):
            pass

    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.socket", GuardSocket)
    with pytest.raises(FetchError, match=BLOCKED_URL_WARNING):
        fetch_url("https://files.example/report.pdf")
    assert connected == []


def test_fetch_url_connects_to_validated_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port or 0))]

    connected: list[object] = []

    class StopSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, *args):
            pass

        def bind(self, *args):
            pass

        def connect(self, address):
            connected.append(address)
            raise TimeoutError("stop after pin")

        def close(self):
            pass

    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.socket", StopSocket)
    with pytest.raises(FetchError, match="fetch failed"):
        fetch_url("https://news.example/story")
    assert connected == [("1.1.1.1", 443)]


def test_youtube_subtitle_private_resolution_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[object] = []

    class GuardSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, *args):
            pass

        def connect(self, address):
            connected.append(address)
            raise AssertionError("subtitle fetch must not connect")

        def close(self):
            pass

    monkeypatch.setattr(
        "supersocks_url_scraper.ssrf.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.2.3.4", port or 0))],
    )
    monkeypatch.setattr("supersocks_url_scraper.ssrf.socket.socket", GuardSocket)
    with pytest.raises(ValueError, match="blocked"):
        _fetch_text("https://cdn.example/captions.vtt", timeout=5)
    assert connected == []


def test_service_mode_skips_cached_cloak(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[str] = []

    def boom(*_args, **_kwargs):
        calls.append("browser")
        raise AssertionError("browser must not launch")

    monkeypatch.setattr("supersocks_url_scraper.browser_fetcher.fetch_with_cloak", boom)

    def fail_fetch(*_args, **_kwargs):
        raise FetchError("http failed")

    monkeypatch.setattr(reader, "fetch_url", fail_fetch)
    cache = tmp_path / "strategies.json"
    cache.write_text('{"news.example": {"fetch_method": "cloak"}}', encoding="utf-8")
    result = read_url(
        "https://news.example/story",
        allow_browser=False,
        browser_fallback=True,
        seo_fallback=False,
        archive_fallback=False,
        strategy_cache_path=str(cache),
        skip_social_routing=True,
    )
    assert calls == []
    assert "browser fallback disabled in service mode" in result["warnings"]
    assert result["status"] == "error"


def test_fetch_with_cloak_rejects_private_url_without_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CloakBrowser must not launch for a blocked URL")

    monkeypatch.setattr("supersocks_url_scraper.browser_fetcher._browser_semaphore", boom)
    with pytest.raises(BrowserFetchError, match=BLOCKED_URL_WARNING):
        fetch_with_cloak("http://169.254.169.254/")


def test_https_handler_passes_only_context_to_do_open(monkeypatch) -> None:
    """Python 3.12 has no HTTPSHandler._check_hostname nor check_hostname kwarg."""
    from supersocks_url_scraper import ssrf

    seen = {}

    def fake_do_open(self, conn_class, req, **kwargs):
        seen["conn"] = conn_class
        seen["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(ssrf.SafeHTTPSHandler, "do_open", fake_do_open)
    handler = ssrf.SafeHTTPSHandler()
    if hasattr(handler, "_check_hostname"):
        monkeypatch.delattr(handler, "_check_hostname", raising=False)
    assert handler.https_open(object()) == "ok"
    assert seen["conn"] is ssrf._SafeHTTPSConnection
    assert set(seen["kwargs"]) == {"context"}


def test_pinned_connection_falls_back_to_next_checked_address(monkeypatch) -> None:
    import socket as socket_mod
    from supersocks_url_scraper import ssrf

    infos = [
        (socket_mod.AF_INET6, socket_mod.SOCK_STREAM, 6, "", ("2606:4700::1111", 80, 0, 0)),
        (socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
    ]
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *a, **k: infos)
    attempts = []

    class FakeSocket:
        def __init__(self, family, socktype, proto):
            self.family = family

        def settimeout(self, value):
            pass

        def bind(self, addr):
            pass

        def connect(self, sockaddr):
            attempts.append(sockaddr[0])
            if self.family == socket_mod.AF_INET6:
                raise OSError("network unreachable")

        def close(self):
            pass

    monkeypatch.setattr(ssrf.socket, "socket", FakeSocket)
    sock = ssrf._safe_create_connection(("example.com", 80), timeout=5)
    assert isinstance(sock, FakeSocket)
    assert attempts == ["2606:4700::1111", "93.184.216.34"]


def test_ipv6_site_local_is_blocked() -> None:
    assert ip_is_blocked(ipaddress.ip_address("fec0::1"))
