from __future__ import annotations

import ipaddress
from urllib.error import URLError
from unittest.mock import Mock

import pytest

from supersocks_url_scraper import reader
from supersocks_url_scraper.browser_fetcher import BrowserFetchError, fetch_with_cloak
from supersocks_url_scraper.reader import FetchError, fetch_url, fetch_with_browser, read_url
from supersocks_url_scraper.ssrf import (
    BLOCKED_URL_WARNING,
    RevalidateRedirectHandler,
    host_is_blocked,
    ip_is_blocked,
    url_is_blocked,
)


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
    )
    allowed = ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888")
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


def test_fetch_with_cloak_rejects_private_url_without_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CloakBrowser must not launch for a blocked URL")

    monkeypatch.setattr("supersocks_url_scraper.browser_fetcher._browser_semaphore", boom)
    with pytest.raises(BrowserFetchError, match=BLOCKED_URL_WARNING):
        fetch_with_cloak("http://169.254.169.254/")
