from __future__ import annotations

from pathlib import Path

import pytest

from supersocks_url_scraper.reader import read_url
from supersocks_url_scraper.social.linkedin import (
    classify_linkedin_page_type,
    extract_linkedin,
    extract_linkedin_html,
)
from supersocks_url_scraper.social.routing import try_social_read
from supersocks_url_scraper.social.youtube import extract_youtube

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.linkedin.com/in/alex-example", "profile"),
        ("https://www.linkedin.com/company/example-labs/", "company"),
        ("https://www.linkedin.com/school/example-university", "school"),
        ("https://www.linkedin.com/showcase/example-showcase", "showcase"),
        ("https://www.linkedin.com/jobs/view/123456789", "job"),
        ("https://www.linkedin.com/jobs-guest/jobs/view/123456789", "job"),
        ("https://www.linkedin.com/pulse/public-extractors", "article"),
        ("https://www.linkedin.com/articles/public-extractors", "article"),
        ("https://www.linkedin.com/posts/example-labs_update-activity-1", "post"),
        ("https://www.linkedin.com/feed/update/urn:li:activity:1", "post"),
        ("https://www.linkedin.com/search/results/all/", "unknown"),
    ],
)
def test_classify_linkedin_page_type(url: str, expected: str) -> None:
    assert classify_linkedin_page_type(url) == expected


def test_profile_fixture_extracts_structured_and_meta() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/in/alex-example",
        _load("profile.html"),
        include_content=True,
    )
    assert result["status"] == "ok"
    assert result["platform"] == "linkedin"
    assert result["linkedin_page_type"] == "profile"
    assert result["title"] and "Alex Example" in result["title"]
    assert "open documentation" in result["summary"].lower()
    assert result["structured_data"]["@type"] == "Person"
    assert result["image_url"]


def test_company_fixture() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/company/example-labs",
        _load("company.html"),
    )
    assert result["status"] == "ok"
    assert result["linkedin_page_type"] == "company"
    assert result["structured_data"]["name"] == "Example Labs"
    assert "documentation fixtures" in result["summary"].lower()


def test_job_fixture_jobposting_jsonld() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/jobs/view/123456789",
        _load("job.html"),
    )
    assert result["status"] == "ok"
    assert result["linkedin_page_type"] == "job"
    assert result["structured_data"]["@type"] == "JobPosting"
    assert result["structured_data"]["title"] == "Documentation Engineer"
    assert result["author"] == "Example Labs"
    assert result["published_at"] == "2026-07-01"
    assert "job posting" in result["summary"].lower() or "documentation engineer" in result["summary"].lower()


def test_article_and_post_fixtures() -> None:
    article = extract_linkedin_html(
        "https://www.linkedin.com/pulse/public-extractors",
        _load("article.html"),
    )
    assert article["status"] == "ok"
    assert article["linkedin_page_type"] == "article"
    assert article["author"] == "Sam Writer"
    assert "login" in article["summary"].lower() or "guest" in article["summary"].lower()

    post = extract_linkedin_html(
        "https://www.linkedin.com/posts/example-labs_update-activity-1",
        _load("post.html"),
    )
    assert post["status"] == "ok"
    assert post["linkedin_page_type"] == "post"
    assert "feed update" in post["summary"].lower() or "posts" in post["summary"].lower()


@pytest.mark.parametrize(
    ("fixture", "url", "needle"),
    [
        ("authwall.html", "https://www.linkedin.com/in/alex-example", "authwall"),
        ("challenge.html", "https://www.linkedin.com/in/alex-example", "challenge"),
        ("nav_only.html", "https://www.linkedin.com/company/example-labs", "navigation/cta"),
        ("empty.html", "https://www.linkedin.com/pulse/x", "empty"),
        ("missing_values.html", "https://www.linkedin.com/in/alex-example", "too poor"),
    ],
)
def test_gates_and_poor_content_are_partial(fixture: str, url: str, needle: str) -> None:
    result = extract_linkedin_html(url, _load(fixture))
    assert result["status"] == "partial"
    assert any(needle in w.lower() for w in result["warnings"])


def test_malformed_jsonld_falls_back_to_meta() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/pulse/malformed",
        _load("malformed_jsonld.html"),
    )
    assert result["status"] == "ok"
    assert any("malformed JSON-LD" in w for w in result["warnings"])
    assert "open graph" in result["summary"].lower()
    assert "structured_data" not in result or not result.get("structured_data")


def test_non_linkedin_url_errors() -> None:
    result = extract_linkedin_html("https://example.com/not-linkedin", "<html><body>hi</body></html>")
    assert result["status"] == "error"
    assert any("not a LinkedIn" in w for w in result["warnings"])


def test_extract_linkedin_uses_html_fetcher() -> None:
    html = _load("job.html")

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        assert "linkedin.com" in url
        return {"final_url": url, "text": html, "fetch_method": "http"}

    result = extract_linkedin(
        "https://www.linkedin.com/jobs-guest/jobs/view/123456789",
        html_fetcher=fetcher,
    )
    assert result["status"] == "ok"
    assert result["linkedin_page_type"] == "job"


def test_specialized_beats_generic_when_contentful() -> None:
    html = _load("article.html")
    calls = {"generic": 0}

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": html, "fetch_method": "http"}

    def fake_generic(url: str, **kwargs):
        calls["generic"] += 1
        return {
            "status": "ok",
            "url": url,
            "content_type": "article",
            "title": "Generic should not win",
            "summary": "Generic pipeline summary that is long enough to look useful for fallback tests.",
            "length": 900,
            "fetch_method": "http",
            "warnings": [],
        }

    result = try_social_read(
        "https://www.linkedin.com/pulse/public-extractors",
        jina_fallback=False,
        generic_read=fake_generic,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["status"] == "ok"
    assert result["linkedin_page_type"] == "article"
    assert "Sam Writer" == result.get("author") or "authwall" in result["summary"].lower()
    assert calls["generic"] == 0


def test_generic_last_resort_when_specialized_poor() -> None:
    html = _load("missing_values.html")

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": html, "fetch_method": "http"}

    def fake_generic(url: str, **kwargs):
        return {
            "status": "ok",
            "url": url,
            "content_type": "article",
            "title": "Recovered by generic",
            "summary": "Generic pipeline recovered a longer summary that is intentionally long enough for LinkedIn last-resort acceptance.",
            "length": 900,
            "fetch_method": "seo",
            "warnings": ["local extractive summary"],
        }

    result = try_social_read(
        "https://www.linkedin.com/in/alex-example",
        jina_fallback=False,
        generic_read=fake_generic,
        html_fetcher=fetcher,
    )
    assert result is not None
    assert result["fetch_method"] == "seo"
    assert any("last resort" in w for w in result["warnings"])
    assert result["linkedin_page_type"] == "profile"


def test_authwall_skips_generic_but_allows_jina() -> None:
    html = _load("authwall.html")
    generic_called = {"n": 0}

    def fetcher(url: str, *, timeout: int = 20, max_bytes: int = 1) -> dict:
        return {"final_url": url, "text": html, "fetch_method": "http"}

    def fake_generic(url: str, **kwargs):
        generic_called["n"] += 1
        raise AssertionError("generic should not run for clear authwall")

    class Opener:
        def __call__(self, request, timeout=20):
            class Resp:
                def read(self, n: int = -1):
                    return b"# Public post\n\nReadable LinkedIn content from external reader for guest pages.\n"

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Resp()

    result = try_social_read(
        "https://www.linkedin.com/in/alex-example",
        jina_fallback=True,
        generic_read=fake_generic,
        html_fetcher=fetcher,
        jina_opener=Opener(),
    )
    assert generic_called["n"] == 0
    assert result is not None
    assert result["fetch_method"] == "jina"
    assert result["linkedin_page_type"] == "profile"
    assert any("authwall" in w.lower() for w in result["warnings"])


def test_read_url_linkedin_specialized_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from supersocks_url_scraper.reader import FetchedResource

    html = _load("company.html")

    def fake_pipeline(url, **kwargs):
        return FetchedResource(url, url, 200, html.encode(), "text/html", {"x-fetch-method": "http"})

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url("https://www.linkedin.com/company/example-labs", jina_fallback=False)
    assert result["platform"] == "linkedin"
    assert result["linkedin_page_type"] == "company"
    assert result["status"] == "ok"
    assert result["structured_data"]["name"] == "Example Labs"


def test_youtube_non_regression_with_mocks() -> None:
    info = {
        "title": "Demo Video",
        "uploader": "Demo Channel",
        "description": "A description that is long enough to summarize when needed for fallback text.",
        "duration": 12,
        "upload_date": "20260115",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "subtitles": {},
        "automatic_captions": {},
    }

    class FakeYDL:
        def extract_info(self, url: str, download: bool = False):
            return info

    result = extract_youtube(
        "https://www.youtube.com/watch?v=abc123",
        ydl_factory=lambda: FakeYDL(),
        subtitle_fetcher=lambda url, timeout=20: "",
    )
    assert result is not None
    assert result["platform"] == "youtube"
    assert result["status"] in {"ok", "partial"}


def test_no_real_pii_in_fixtures() -> None:
    banned = ("john doe", "jane doe", "acme corp", "@gmail.com", "social security")
    for path in FIXTURES.glob("*.html"):
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, path.name


@pytest.mark.parametrize(
    ("fixture", "url", "page_type"),
    [
        ("profile_signin_modal.html", "https://www.linkedin.com/in/alex-example", "profile"),
        ("company_signin_modal.html", "https://www.linkedin.com/company/example-labs", "company"),
        ("job_signin_modal.html", "https://www.linkedin.com/jobs/view/123456789", "job"),
        ("article_signin_modal.html", "https://www.linkedin.com/pulse/public-extractors", "article"),
        ("post_signin_modal.html", "https://www.linkedin.com/posts/example-labs_update-activity-1", "post"),
    ],
)
def test_signin_modal_with_public_content_returns_ok(fixture: str, url: str, page_type: str) -> None:
    result = extract_linkedin_html(url, _load(fixture))
    assert result["status"] == "ok"
    assert result["linkedin_page_type"] == page_type
    assert any("sign-in chrome ignored" in w.lower() for w in result["warnings"])
    assert len(result["summary"]) >= 80


def test_article_aside_before_main_prioritizes_body() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/pulse/aside-order",
        _load("article_aside_before_main.html"),
    )
    assert result["status"] == "ok"
    summary = result["summary"].lower()
    assert summary.startswith("primary article body") or "primary article body" in summary[:120].lower()
    assert "technology" not in summary[:80].lower()
    assert "sidebar category labels must not" not in summary[:120].lower()


def test_profile_rejects_mismatched_jsonld_type() -> None:
    result = extract_linkedin_html(
        "https://www.linkedin.com/in/guest-profile",
        _load("profile_wrong_jsonld.html"),
    )
    assert result["status"] == "ok"
    assert "structured_data" not in result or not result.get("structured_data")
    assert "guest profile name" in result["summary"].lower()
