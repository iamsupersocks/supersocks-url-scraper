from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from supersocks_url_scraper import reader
from supersocks_url_scraper.reader import clean_text, detect_content_type, extract_title, read_url, to_markdown, FetchedResource


HTML = """
<!doctype html>
<html>
<head>
  <title>Fallback title</title>
  <meta property="og:title" content="OpenGraph title">
  <meta property="og:image" content="https://example.test/og.jpg">
  <meta name="description" content="This is a long enough metadata description for the scraper to return it as the readable summary without looking at paragraphs.">
  <script type="application/ld+json">{"@type":"Article","datePublished":"2026-01-01T00:00:00Z","articleBody":"This article body is intentionally long enough to act as a fallback when metadata is missing."}</script>
</head>
<body>
  <p>This paragraph is also long enough to be extracted if no metadata exists in the document.</p>
  <p>A second paragraph gives the extractive summarizer enough source text to build a stable article summary.</p>
</body>
</html>
"""

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 2048


def test_clean_text_strips_html_and_normalizes_whitespace() -> None:
    assert clean_text("<p>Hello&nbsp;   world</p>") == "Hello world"


def test_extract_title_prefers_og_title() -> None:
    assert extract_title(HTML) == "OpenGraph title"


def test_detect_content_type_uses_magic_bytes() -> None:
    pdf = FetchedResource("u", "u", 200, b"%PDF-1.7\n", "application/octet-stream", {})
    image = FetchedResource("u", "u", 200, PNG_BYTES, "application/octet-stream", {})
    assert detect_content_type(pdf) == "pdf"
    assert detect_content_type(image) == "image"


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/image.png":
            body = PNG_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
        else:
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def fixture_base_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_read_url_extracts_article_contract(fixture_base_url: str) -> None:
    result = read_url(f"{fixture_base_url}/article", include_content=True)
    assert result["status"] == "ok"
    assert result["content_type"] == "article"
    assert result["title"] == "OpenGraph title"
    assert "paragraph" in result["summary"].lower()
    assert result["fetch_method"] == "http"
    assert result["image_url"] == "https://example.test/og.jpg"
    assert "content" in result


def test_read_url_rejects_non_http_url() -> None:
    result = read_url("file:///etc/passwd")
    assert result["status"] == "error"
    assert result["warnings"] == ["invalid http(s) URL"]


def test_read_url_image_placeholder(fixture_base_url: str) -> None:
    result = read_url(f"{fixture_base_url}/image.png")
    assert result["status"] == "ok"
    assert result["content_type"] == "image"
    assert result["title"] == "image.png"
    assert "No vision model" in result["summary"]


def test_to_markdown_contains_summary_and_content(fixture_base_url: str) -> None:
    result = read_url(f"{fixture_base_url}/article", include_content=True)
    md = to_markdown(result)
    assert md.startswith("# OpenGraph title")
    assert "## Summary" in md
    assert "## Content" in md


def test_strategy_cache_records_http_success(fixture_base_url: str, tmp_path: Path) -> None:
    cache = tmp_path / "strategies.json"
    result = read_url(f"{fixture_base_url}/article", strategy_cache_path=str(cache))
    assert result["status"] == "ok"
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert "127.0.0.1" in data
    assert data["127.0.0.1"]["fetch_method"] == "http"


def test_browser_fallback_after_http_and_seo_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><head><title>Browser article</title></head><body>
    <article><p>This browser-rendered article paragraph is long enough to be extracted as a useful summary after HTTP and SEO both fail.</p></article>
    </body></html>
    """

    def fail_fetch(*args: object, **kwargs: object) -> FetchedResource:
        raise reader.FetchError("HTTP 403")

    def fake_browser(*args: object, **kwargs: object) -> FetchedResource:
        return FetchedResource(
            "https://blocked.example/article",
            "https://blocked.example/article",
            200,
            html.encode("utf-8"),
            "text/html; charset=utf-8",
            {"x-fetch-method": "cloak", "content-type": "text/html; charset=utf-8"},
        )

    monkeypatch.setattr(reader, "fetch_url", fail_fetch)
    monkeypatch.setattr(reader, "fetch_with_seo_variants", fail_fetch)
    monkeypatch.setattr(reader, "fetch_with_browser", fake_browser)

    result = read_url("https://blocked.example/article", browser_fallback=True, include_content=True)
    assert result["status"] == "ok"
    assert result["fetch_method"] == "cloak"
    assert result["title"] == "Browser article"
    assert any("browser fallback used: cloak" in warning for warning in result["warnings"])
    assert "browser-rendered article" in result["content"]
