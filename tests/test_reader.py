from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from supersocks_url_scraper.reader import clean_text, extract_title, read_url


HTML = """
<!doctype html>
<html>
<head>
  <title>Fallback title</title>
  <meta property="og:title" content="OpenGraph title">
  <meta name="description" content="This is a long enough metadata description for the scraper to return it as the readable summary without looking at paragraphs.">
  <script type="application/ld+json">{"@type":"Article","datePublished":"2026-01-01T00:00:00Z","articleBody":"This article body is intentionally long enough to act as a fallback when metadata is missing."}</script>
</head>
<body><p>This paragraph is also long enough to be extracted if no metadata exists in the document.</p></body>
</html>
"""


def test_clean_text_strips_html_and_normalizes_whitespace() -> None:
    assert clean_text("<p>Hello&nbsp;   world</p>") == "Hello world"


def test_extract_title_prefers_og_title() -> None:
    assert extract_title(HTML) == "OpenGraph title"


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def fixture_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/article"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_read_url_extracts_title_summary_and_content_type(fixture_url: str) -> None:
    result = read_url(fixture_url)
    assert result["status"] == "ok"
    assert result["title"] == "OpenGraph title"
    assert "metadata description" in result["summary"]
    assert result["content_type"].startswith("text/html")
    assert result["warnings"] == []


def test_read_url_rejects_non_http_url() -> None:
    result = read_url("file:///etc/passwd")
    assert result["status"] == "error"
    assert "invalid http(s) URL" in result["warnings"]
