from __future__ import annotations

import json
from io import BytesIO
from urllib.error import URLError

import pytest

from supersocks_url_scraper import cli
from supersocks_url_scraper.reader import read_url
from supersocks_url_scraper.social.domains import detect_platform, host_matches_root, is_safe_public_http_url
from supersocks_url_scraper.social.jina import fetch_jina_reader
from supersocks_url_scraper.social.opencli import OpenCLIStatus
from supersocks_url_scraper.social.routing import try_social_read
from supersocks_url_scraper.social.youtube import _fetch_text, extract_youtube


class FakeYDL:
    def __init__(self, info: dict):
        self.info = info

    def extract_info(self, url: str, download: bool = False):
        assert download is False
        assert "youtube.com" in url or "youtu.be" in url
        return self.info


class FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {"content-type": "text/plain; charset=utf-8"}

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_host_matches_root_rejects_lookalikes() -> None:
    assert host_matches_root("youtube.com", "youtube.com")
    assert host_matches_root("www.youtube.com", "youtube.com")
    assert host_matches_root("m.youtube.com", "youtube.com")
    assert not host_matches_root("notyoutube.com", "youtube.com")
    assert not host_matches_root("youtube.com.evil.example", "youtube.com")
    assert not host_matches_root("evil-youtube.com", "youtube.com")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.linkedin.com/pulse/hello", "linkedin"),
        ("https://x.com/user/status/1", "x"),
        ("https://www.instagram.com/nasa/", "instagram"),
        ("https://www.facebook.com/zuck", "facebook"),
        ("https://notyoutube.com/watch?v=abc", None),
        ("https://youtube.com.evil.example/watch?v=abc", None),
        ("https://user:pass@www.youtube.com/watch?v=abc", None),
        ("https://youtube.com@evil.example/watch?v=abc", None),
        ("file:///tmp/x", None),
        ("https://127.0.0.1/watch?v=abc", None),
    ],
)
def test_detect_platform_routing(url: str, expected: str | None) -> None:
    assert detect_platform(url) == expected


def test_is_safe_public_http_url_blocks_credentials_and_private_hosts() -> None:
    assert is_safe_public_http_url("https://www.linkedin.com/pulse/x")
    assert not is_safe_public_http_url("https://user:token@www.linkedin.com/pulse/x")
    assert not is_safe_public_http_url("http://localhost/pulse/x")
    assert not is_safe_public_http_url("http://192.168.1.10/pulse/x")
    assert not is_safe_public_http_url("http://10.0.0.8/pulse/x")
    assert not is_safe_public_http_url("ftp://www.linkedin.com/pulse/x")


def test_youtube_missing_dependency_falls_back_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.youtube.yt_dlp_available", lambda: False)
    monkeypatch.setattr("supersocks_url_scraper.reader.yt_dlp_available", lambda: False, raising=False)

    def fail_fetch(*args, **kwargs):
        from supersocks_url_scraper.reader import FetchError

        raise FetchError("offline")

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fail_fetch)
    result = read_url("https://www.youtube.com/watch?v=abc123")
    assert result["platform"] == "youtube"
    assert result["status"] == "error"
    assert any("yt-dlp not installed" in w for w in result["warnings"])
    assert result["fetch_method"] == "http"


def test_youtube_metadata_and_subtitles_with_mocks() -> None:
    info = {
        "title": "Demo Video",
        "uploader": "Demo Channel",
        "channel": "Demo Channel",
        "description": "A description that is long enough to summarize when needed for fallback text.",
        "duration": 125,
        "upload_date": "20260115",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "subtitles": {
            "en": [{"url": "https://example.test/subs.vtt", "ext": "vtt"}],
        },
        "automatic_captions": {},
    }
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello from captions\n\n00:00:02.000 --> 00:00:04.000\nand more words\n"

    result = extract_youtube(
        "https://www.youtube.com/watch?v=abc123",
        include_content=True,
        ydl_factory=lambda: FakeYDL(info),
        subtitle_fetcher=lambda url, timeout=20: vtt,
    )
    assert result is not None
    assert result["status"] == "ok"
    assert result["platform"] == "youtube"
    assert result["fetch_method"] == "yt-dlp"
    assert result["title"] == "Demo Video"
    assert result["author"] == "Demo Channel"
    assert result["published_at"] == "2026-01-15"
    assert result["duration"] == 125
    assert result["transcript_source"] == "manual"
    assert "Hello from captions" in result["transcript"]
    assert "Hello from captions" in result["summary"]


UNSAFE_SUBTITLE_URL = "http://user:password@127.0.0.1/subs.vtt?token=SECRET_MARKER"


def test_youtube_blocks_unsafe_subtitle_url_without_fetch() -> None:
    info = {
        "title": "Blocked Sub URL",
        "uploader": "Channel",
        "duration": 10,
        "upload_date": "20260201",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "subtitles": {
            "en": [{"url": UNSAFE_SUBTITLE_URL, "ext": "vtt"}],
        },
        "automatic_captions": {},
    }
    fetch_called = False

    def fetcher(url: str, timeout: int = 20) -> str:
        nonlocal fetch_called
        fetch_called = True
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nShould not fetch\n"

    result = extract_youtube(
        "https://www.youtube.com/watch?v=abc123",
        ydl_factory=lambda: FakeYDL(info),
        subtitle_fetcher=fetcher,
    )
    assert result is not None
    assert fetch_called is False
    assert any("subtitle URL blocked by safety policy" in w for w in result["warnings"])
    leaked = json.dumps(result["warnings"])
    assert UNSAFE_SUBTITLE_URL not in leaked
    assert "SECRET_MARKER" not in leaked
    assert "user:password" not in leaked
    assert "127.0.0.1" not in leaked


def test_fetch_text_rejects_unsafe_url_without_leaking_secrets() -> None:
    with pytest.raises(ValueError, match="subtitle URL blocked by safety policy") as exc_info:
        _fetch_text(UNSAFE_SUBTITLE_URL, timeout=5)
    msg = str(exc_info.value)
    assert UNSAFE_SUBTITLE_URL not in msg
    assert "SECRET_MARKER" not in msg
    assert "user:password" not in msg
    assert "127.0.0.1" not in msg


def test_fetch_text_rejects_oversized_subtitle_track(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = b"x" * (5 * 1024 * 1024 + 1)

    def fake_urlopen(request, timeout=20):
        return FakeResponse(oversized)

    monkeypatch.setattr("supersocks_url_scraper.social.youtube.urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="exceeds"):
        _fetch_text("https://example.test/subs.vtt", timeout=5)


def test_youtube_subtitle_size_limit_surfaces_warning() -> None:
    info = {
        "title": "Large Subtitle Video",
        "uploader": "Channel",
        "duration": 10,
        "upload_date": "20260201",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "subtitles": {
            "en": [{"url": "https://example.test/subs.vtt", "ext": "vtt"}],
        },
        "automatic_captions": {},
    }

    def oversized_fetcher(url: str, timeout: int = 20) -> str:
        raise ValueError("subtitle track exceeds 5242880 bytes")

    result = extract_youtube(
        "https://www.youtube.com/watch?v=abc123",
        ydl_factory=lambda: FakeYDL(info),
        subtitle_fetcher=oversized_fetcher,
    )
    assert result is not None
    assert any("subtitle fetch failed" in w and "exceeds" in w for w in result["warnings"])


def test_youtube_prefers_auto_captions_when_manual_missing() -> None:
    info = {
        "title": "Auto Caption Video",
        "uploader": "Channel",
        "duration": 10,
        "upload_date": "20260201",
        "webpage_url": "https://youtu.be/xyz",
        "subtitles": {},
        "automatic_captions": {
            "en": [{"url": "https://example.test/auto.vtt", "ext": "vtt"}],
        },
    }
    result = extract_youtube(
        "https://youtu.be/xyz",
        ydl_factory=lambda: FakeYDL(info),
        subtitle_fetcher=lambda url, timeout=20: "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAuto text\n",
    )
    assert result is not None
    assert result["transcript_source"] == "auto-captions"
    assert "Auto text" in result["summary"]


def test_linkedin_uses_specialized_extractor_first() -> None:
    html = """
    <html><head>
    <meta property="og:title" content="Public LinkedIn note"/>
    <meta property="og:description" content="Specialized LinkedIn public extractor summary that is intentionally long enough for the reader to accept as usable guest text."/>
    </head><body><p>Specialized LinkedIn public extractor summary that is intentionally long enough for the reader to accept as usable guest text.</p></body></html>
    """
    calls: list[dict] = []

    def fake_generic(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return {
            "status": "ok",
            "url": url,
            "content_type": "article",
            "title": "Public LinkedIn post",
            "summary": "Generic pipeline summary that is long enough.",
            "length": 900,
            "fetch_method": "http",
            "warnings": ["local extractive summary (method=meta)"],
        }

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": html, "fetch_method": "http"}

    result = try_social_read(
        "https://www.linkedin.com/pulse/public-example",
        jina_fallback=False,
        generic_read=fake_generic,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["platform"] == "linkedin"
    assert result["fetch_method"] == "http"
    assert result["linkedin_page_type"] == "article"
    assert "Specialized LinkedIn" in result["summary"]
    assert calls == []


def test_linkedin_jina_opt_in_after_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_generic(url: str, **kwargs):
        return {
            "status": "partial",
            "url": url,
            "content_type": "article",
            "title": "Login wall",
            "summary": "",
            "length": 900,
            "fetch_method": "http",
            "warnings": ["article extraction looks like boilerplate/non-article content: social/login/javascript stub"],
        }

    class CapturingOpener:
        def __init__(self):
            self.requests = []

        def __call__(self, request, timeout=20):
            self.requests.append(request)
            body = b"# Public post\n\nReadable LinkedIn content from external reader.\n"
            return FakeResponse(body)

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        # Authwall shell so specialized returns partial and skips generic.
        body = "<html><body class='authwall'>Sign in to view Join LinkedIn Make the most of your professional life</body></html>"
        return {"final_url": url, "text": body, "fetch_method": "http"}

    opener = CapturingOpener()
    result = try_social_read(
        "https://www.linkedin.com/pulse/public-example",
        jina_fallback=True,
        include_content=True,
        generic_read=fake_generic,
        jina_opener=opener,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["fetch_method"] == "jina"
    assert result["platform"] == "linkedin"
    assert "external reader used: jina" in result["warnings"]
    assert "Readable LinkedIn content" in result["summary"]
    assert opener.requests
    headers = {k.lower(): v for k, v in opener.requests[0].header_items()}
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert headers["user-agent"].startswith("supersocks-url-scraper/")


def test_linkedin_jina_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_generic(url: str, **kwargs):
        return {
            "status": "partial",
            "url": url,
            "content_type": "article",
            "title": None,
            "summary": "",
            "length": 900,
            "fetch_method": "http",
            "warnings": ["stub"],
        }

    called = {"jina": False}

    def boom(*args, **kwargs):
        called["jina"] = True
        raise AssertionError("jina should not run when disabled")

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": "<html><body>short</body></html>", "fetch_method": "http"}

    monkeypatch.setattr("supersocks_url_scraper.social.routing.fetch_jina_reader", boom)
    result = try_social_read(
        "https://www.linkedin.com/pulse/public-example",
        jina_fallback=False,
        generic_read=fake_generic,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["fetch_method"] in {"http", "seo"}
    assert called["jina"] is False


def test_jina_blocks_private_hosts() -> None:
    result = fetch_jina_reader("http://127.0.0.1/secret", platform="linkedin")
    assert result["status"] == "error"
    assert result["fetch_method"] == "jina"
    assert any("blocked" in w for w in result["warnings"])


def test_jina_blocks_userinfo_urls() -> None:
    result = fetch_jina_reader("https://user:pass@www.linkedin.com/pulse/x", platform="linkedin")
    assert result["status"] == "error"
    assert any("blocked" in w for w in result["warnings"])


def test_read_url_linkedin_wires_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><head><title>LinkedIn public note</title>
    <meta name="description" content="This LinkedIn public note summary is intentionally long enough for the extractive reader to accept as usable article text without needing an external reader.">
    <meta property="og:title" content="LinkedIn public note"/>
    <meta property="og:description" content="This LinkedIn public note summary is intentionally long enough for the extractive reader to accept as usable article text without needing an external reader."/>
    </head><body><p>This LinkedIn public note summary is intentionally long enough for the extractive reader to accept as usable article text without needing an external reader.</p></body></html>
    """

    from supersocks_url_scraper.reader import FetchedResource

    def fake_pipeline(url, **kwargs):
        return FetchedResource(url, url, 200, html.encode(), "text/html", {"x-fetch-method": "http"})

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url("https://www.linkedin.com/pulse/share-safe-note", jina_fallback=False)
    assert result["platform"] == "linkedin"
    assert result["fetch_method"] == "http"
    assert result["linkedin_page_type"] == "article"
    assert result["status"] in {"ok", "partial"}


def test_health_and_openapi_include_social_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JINA_FALLBACK", raising=False)
    monkeypatch.setattr(
        "supersocks_url_scraper.social.opencli.probe_opencli",
        lambda timeout=3, **kwargs: OpenCLIStatus(installed=False),
    )
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.explicit_twitter_credentials_present", lambda: False)
    health = cli.health_payload()
    assert health["fallbacks"]["jina_default"] is False
    assert health["social"]["platforms"] == ["youtube", "linkedin", "x", "instagram", "facebook"]
    assert "youtube_extra_installed" in health["social"]
    assert "js_runtime_available" in health["social"]
    assert isinstance(health["social"]["js_runtime_available"], bool)
    assert health["social"]["twitter_cli_available"] is False
    assert health["social"]["opencli_available"] is False

    schema = cli.openapi_payload()
    props = schema["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert props["jina_fallback"]["default"] is False
    result_props = schema["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert "yt-dlp" in result_props["fetch_method"]["enum"]
    assert "jina" in result_props["fetch_method"]["enum"]
    assert "twitter-cli" in result_props["fetch_method"]["enum"]
    assert "opencli" in result_props["fetch_method"]["enum"]
    assert set(result_props["platform"]["enum"]) == {"youtube", "linkedin", "x", "instagram", "facebook"}
    assert "platform" in result_props
    assert "transcript" in result_props
    assert "linkedin_page_type" in result_props
    assert "structured_data" in result_props


def test_cli_passes_jina_fallback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict = {}

    def fake_read_url(url: str, **kwargs):
        captured.update({"url": url, **kwargs})
        return {"status": "ok", "url": url, "summary": "ok", "fetch_method": "http", "warnings": [], "platform": "linkedin"}

    monkeypatch.setattr(cli, "read_url", fake_read_url)
    monkeypatch.setattr(
        "sys.argv",
        ["supersocks-url-scraper", "--jina-fallback", "https://www.linkedin.com/pulse/x"],
    )
    assert cli.main() == 0
    assert captured["jina_fallback"] is True
    out = json.loads(capsys.readouterr().out)
    assert out["platform"] == "linkedin"


def test_service_jina_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_read_url(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"status": "ok", "url": url, "summary": "ok", "fetch_method": "http", "warnings": []}

    monkeypatch.setattr(cli, "read_url", fake_read_url)
    monkeypatch.setenv("JINA_FALLBACK", "1")

    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen

    server = ThreadingHTTPServer(("127.0.0.1", 0), cli.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        req = Request(
            f"{base}/summarize",
            data=json.dumps({"url": "https://www.linkedin.com/pulse/x"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as response:
            assert response.status == 200
        assert calls[-1]["jina_fallback"] is True

        status, health = 200, None
        with urlopen(f"{base}/health", timeout=5) as response:
            health = json.loads(response.read().decode())
            status = response.status
        assert status == 200
        assert health["fallbacks"]["jina_default"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_jina_opener_failure_keeps_generic_result() -> None:
    def fake_generic(url: str, **kwargs):
        return {
            "status": "partial",
            "url": url,
            "content_type": "article",
            "title": "Generic",
            "summary": "Generic partial recovery text that is long enough to be preferred when specialized content is too poor.",
            "length": 900,
            "fetch_method": "seo",
            "warnings": ["generic partial"],
        }

    def failing_opener(request, timeout=20):
        raise URLError("offline")

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": "<html><body>Too short.</body></html>", "fetch_method": "http"}

    result = try_social_read(
        "https://www.linkedin.com/pulse/public-example",
        jina_fallback=True,
        generic_read=fake_generic,
        jina_opener=failing_opener,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["fetch_method"] == "seo"
    assert any("external reader used: jina" in w for w in result["warnings"])
    assert any("last resort" in w for w in result["warnings"])
