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


class _FakeHTTPResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "application/json") -> None:
        self._body = body
        self.status = status
        self.headers = {"content-type": content_type}

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_kimi_provider_posts_openai_compatible_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        from urllib.request import Request

        assert isinstance(request, Request)
        payload = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.headers.get("Authorization") or request.get_header("Authorization")
        captured["payload"] = payload
        body = json.dumps({"choices": [{"message": {"content": "  kimi summary text  "}}]}).encode("utf-8")
        return _FakeHTTPResponse(body)

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.delenv("KIMI_API_URL", raising=False)
    monkeypatch.delenv("KIMI_MODEL", raising=False)
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    summary = summarize_with_provider(
        provider="kimi",
        text="Only summarize this extracted article text about widgets.",
        length=120,
        timeout=17,
    )

    assert summary == "kimi summary text"
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["timeout"] == 17
    assert captured["authorization"] == "Bearer kimi-secret-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "kimi-k2.5"
    assert payload["messages"][0]["role"] == "system"
    assert "at most 120 characters" in payload["messages"][0]["content"]
    assert "Do not fetch or scrape URLs" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {
        "role": "user",
        "content": "Only summarize this extracted article text about widgets.",
    }
    # API key must never appear in returned summary text
    assert "kimi-secret-key" not in summary


def test_kimi_provider_uses_configured_model_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        from urllib.request import Request

        assert isinstance(request, Request)
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        return _FakeHTTPResponse(body)

    monkeypatch.setenv("KIMI_API_KEY", "env-key")
    monkeypatch.setenv("KIMI_MODEL", "kimi-from-env")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    summary = summarize_with_provider(
        provider="kimi",
        text="article text",
        length=80,
        endpoint="https://example.invalid/v1/chat/completions",
        token="token-override",
    )

    assert summary == "ok"
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["authorization"] == "Bearer token-override"
    assert captured["payload"]["model"] == "kimi-from-env"

    summary = summarize_with_provider(
        provider="kimi",
        text="article text",
        length=80,
        endpoint="https://example.invalid/v1/chat/completions",
        token="token-override",
        model="kimi-explicit",
    )
    assert summary == "ok"
    assert captured["payload"]["model"] == "kimi-explicit"


def test_kimi_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"urlopen": False}

    def fake_urlopen(*args: object, **kwargs: object):
        called["urlopen"] = True
        raise AssertionError("urlopen must not be called without a key")

    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    with pytest.raises(Exception, match="KIMI_API_KEY is required"):
        summarize_with_provider(provider="kimi", text="article text", length=80)

    assert called["urlopen"] is False


def test_kimi_provider_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        return _FakeHTTPResponse(b"not-json")

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    with pytest.raises(Exception, match="invalid JSON"):
        summarize_with_provider(provider="kimi", text="article text", length=80)


def test_kimi_provider_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError
    from io import BytesIO

    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        raise HTTPError("https://api.moonshot.ai/v1/chat/completions", 401, "Unauthorized", hdrs=None, fp=BytesIO(b"{}"))

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    with pytest.raises(Exception, match="kimi provider HTTP 401"):
        summarize_with_provider(provider="kimi", text="article text", length=80)


def test_kimi_provider_schema_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        return _FakeHTTPResponse(json.dumps({"choices": [{"message": {}}]}).encode("utf-8"))

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    with pytest.raises(Exception, match="choices\\[0\\]\\.message\\.content"):
        summarize_with_provider(provider="kimi", text="article text", length=80)


def test_local_provider_is_non_opt_in_and_skips_kimi(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"urlopen": False}

    def fake_urlopen(*args: object, **kwargs: object):
        called["urlopen"] = True
        raise AssertionError("local provider must not call kimi")

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    assert summarize_with_provider(provider="local", text="article text", length=80) is None
    assert summarize_with_provider(provider=None, text="article text", length=80) is None
    assert called["urlopen"] is False


def test_read_url_uses_kimi_after_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(*args: object, **kwargs: object) -> FetchedResource:
        return FetchedResource(
            "https://example.com/provider",
            "https://example.com/provider",
            200,
            ARTICLE_HTML.encode("utf-8"),
            "text/html; charset=utf-8",
            {"x-fetch-method": "http", "content-type": "text/html; charset=utf-8"},
        )

    def fake_urlopen(request: object, timeout: int = 0):  # noqa: ANN001
        body = json.dumps({"choices": [{"message": {"content": "kimi article summary"}}]}).encode("utf-8")
        return _FakeHTTPResponse(body)

    monkeypatch.setattr(reader, "fetch_url", fake_fetch)
    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret-key")
    monkeypatch.setattr("supersocks_url_scraper.summary_provider.urlopen", fake_urlopen)

    result = read_url("https://example.com/provider", length=180, summary_provider="kimi")

    assert result["status"] == "ok"
    assert result["summary"] == "kimi article summary"
    assert "external summary provider used: kimi" in result["warnings"]
    assert not any(w.startswith("local extractive summary") for w in result["warnings"])
