from __future__ import annotations

import json
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from supersocks_url_scraper import reader
from supersocks_url_scraper.reader import FetchedResource, read_url
from supersocks_url_scraper.summary_provider import summarize_with_provider

ARTICLE_HTML = """
<html><head><title>Provider article</title></head><body>
<article><p>This article has enough substantial text for extraction before it is handed to the optional external summary provider.</p>
<p>The provider should receive content, title, URL, length, and content type, then return the summary used in the final response.</p></article>
</body></html>
"""


class ProviderHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("content-length", "0") or 0)
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        self.__class__.calls.append({"payload": payload, "authorization": self.headers.get("authorization")})
        body = json.dumps({"summary": f"provider summary for {payload['title']} at {payload['length']} chars"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def provider_url() -> Generator[str, None, None]:
    ProviderHandler.calls.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/summarize"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_http_summary_provider_contract(provider_url: str) -> None:
    summary = summarize_with_provider(
        provider="http",
        endpoint=provider_url,
        token="provider-token",
        text="hello world " * 20,
        title="Provider article",
        url="https://example.com/provider",
        content_type="article",
        length=180,
    )

    assert summary == "provider summary for Provider article at 180 chars"
    assert ProviderHandler.calls[-1]["authorization"] == "Bearer provider-token"
    assert ProviderHandler.calls[-1]["payload"]["content_type"] == "article"
    assert "hello world" in ProviderHandler.calls[-1]["payload"]["content"]


def test_read_url_uses_optional_provider_after_extraction(monkeypatch: pytest.MonkeyPatch, provider_url: str) -> None:
    def fake_fetch(*args: object, **kwargs: object) -> FetchedResource:
        return FetchedResource(
            "https://example.com/provider",
            "https://example.com/provider",
            200,
            ARTICLE_HTML.encode("utf-8"),
            "text/html; charset=utf-8",
            {"x-fetch-method": "http", "content-type": "text/html; charset=utf-8"},
        )

    monkeypatch.setattr(reader, "fetch_url", fake_fetch)
    result = read_url(
        "https://example.com/provider",
        length=180,
        summary_provider="http",
        summary_provider_url=provider_url,
        summary_provider_token="provider-token",
    )

    assert result["status"] == "ok"
    assert result["summary"] == "provider summary for Provider article at 180 chars"
    assert "external summary provider used: http" in result["warnings"]
    assert not any(w.startswith("local extractive summary") for w in result["warnings"])


def test_read_url_falls_back_to_local_when_provider_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(*args: object, **kwargs: object) -> FetchedResource:
        return FetchedResource(
            "https://example.com/provider",
            "https://example.com/provider",
            200,
            ARTICLE_HTML.encode("utf-8"),
            "text/html; charset=utf-8",
            {"x-fetch-method": "http", "content-type": "text/html; charset=utf-8"},
        )

    monkeypatch.setattr(reader, "fetch_url", fake_fetch)
    result = read_url("https://example.com/provider", length=180, summary_provider="http")

    assert result["status"] == "ok"
    assert "provider summary" not in result["summary"]
    assert any(w.startswith("external summary provider failed; using local extractive summary") for w in result["warnings"])
    assert any(w.startswith("local extractive summary") for w in result["warnings"])
