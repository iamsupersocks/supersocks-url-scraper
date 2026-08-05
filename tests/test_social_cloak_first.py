"""Mocked tests for Cloak-first Reddit/Instagram/Facebook social routing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from supersocks_url_scraper import cli
from supersocks_url_scraper.browser_fetcher import resolve_headless
from supersocks_url_scraper.reader import read_url
from supersocks_url_scraper.social.cloak_social import (
    detect_social_gate,
    extract_cloak_social,
    parse_cloak_social_html,
)
from supersocks_url_scraper.social.domains import detect_platform, host_matches_root
from supersocks_url_scraper.social.meta_opencli import extract_instagram
from supersocks_url_scraper.social.opencli import OpenCLIStatus
from supersocks_url_scraper.social.reddit_rdt import extract_reddit_rdt
from supersocks_url_scraper.social.backend import CommandResult
from supersocks_url_scraper.social.routing import try_social_read


@dataclass
class FakePage:
    final_url: str
    status_code: int
    html: str
    title: str | None = None
    method: str = "cloak"
    consent_action: str | None = None


REDDIT_HTML = """
<html><head>
<meta property="og:title" content="Public Reddit announcement" />
<meta property="og:description" content="A long enough Reddit selftext body for summary extraction and routing tests." />
<meta property="article:published_time" content="2026-08-01T12:00:00Z" />
<meta name="author" content="u/spez" />
</head><body>
<shreddit-title title="Public Reddit announcement"></shreddit-title>
<div data-testid="post-content"><p>A long enough Reddit selftext body for summary extraction and routing tests.</p></div>
</body></html>
"""

INSTAGRAM_HTML = """
<html><head>
<meta property="og:title" content="NASA on Instagram" />
<meta property="og:description" content="Explore the universe with enough caption text for a useful Instagram summary body." />
<meta name="author" content="nasa" />
</head><body>
<article>
  <h1>NASA on Instagram</h1>
  <p>Explore the universe with enough caption text for a useful Instagram summary body.</p>
</article>
</body></html>
"""

FACEBOOK_HTML = """
<html><head>
<meta property="og:title" content="Meta public page" />
<meta property="og:description" content="Building community tools with enough Facebook page text for a readable summary across routing tests." />
</head><body>
<div data-testid="post_message">Building community tools with enough Facebook page text for a readable summary across routing tests.</div>
</body></html>
"""

LOGIN_HTML = """
<html><body>
<h1>Log in</h1>
<p>Sign in to continue. Create an account. Please log in to continue viewing this content.</p>
</body></html>
"""

CAPTCHA_HTML = """
<html><body>
<p>Unusual traffic. Please complete the captcha security check. Are you a robot?</p>
</body></html>
"""

REDDIT_HUMANITY_HTML = """
<html><head>
<title>Reddit - Prove your humanity</title>
<meta property="og:title" content="Misleading cached post title" />
<meta property="og:description" content="A long enough description that could otherwise look like useful content if the challenge gate missed this Reddit anti-bot page." />
</head><body>
<h1>Prove your humanity</h1>
<p>Complete the challenge to continue to Reddit.</p>
</body></html>
"""

FACEBOOK_FR_CONSENT_HTML = """
<html><head>
<title>Facebook</title>
<meta property="og:title" content="Misleading cached Facebook page title" />
<meta property="og:description" content="A long enough description that could otherwise look like useful content if the French consent gate missed this Facebook cookie banner." />
</head><body>
<h1>Autoriser l'utilisation des cookies de Facebook sur ce navigateur ?</h1>
<p>Nous utilisons des cookies et des technologies similaires pour fournir nos Services.</p>
<button>Autoriser tous les cookies</button>
<button>Refuser les cookies optionnels</button>
</body></html>
"""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.reddit.com/r/announcements/", "reddit"),
        ("https://redd.it/abc123", "reddit"),
        ("https://www.instagram.com/nasa/", "instagram"),
        ("https://www.facebook.com/zuck", "facebook"),
        ("https://notreddit.com/r/x", None),
        ("https://reddit.com.evil.example/r/x", None),
        ("https://user:pass@www.reddit.com/r/x", None),
        ("https://reddit.com@evil.example/r/x", None),
    ],
)
def test_detect_cloak_social_platforms(url: str, expected: str | None) -> None:
    assert detect_platform(url) == expected


def test_reddit_host_rejects_lookalikes() -> None:
    assert host_matches_root("www.reddit.com", "reddit.com")
    assert host_matches_root("old.reddit.com", "reddit.com")
    assert not host_matches_root("notreddit.com", "reddit.com")
    assert not host_matches_root("reddit.com.evil.example", "reddit.com")


def test_parse_reddit_instagram_facebook_html() -> None:
    reddit = parse_cloak_social_html(REDDIT_HTML, platform="reddit", url="https://www.reddit.com/r/a/comments/1/")
    assert reddit["status"] == "ok"
    assert reddit["fetch_method"] == "cloak"
    assert reddit["author"] == "u/spez"
    assert reddit["published_at"] == "2026-08-01T12:00:00Z"
    assert "Reddit selftext" in reddit["summary"]

    ig = parse_cloak_social_html(INSTAGRAM_HTML, platform="instagram", url="https://www.instagram.com/nasa/")
    assert ig["status"] == "ok"
    assert ig["author"] == "nasa"
    assert "universe" in ig["summary"]

    fb = parse_cloak_social_html(FACEBOOK_HTML, platform="facebook", url="https://www.facebook.com/zuck")
    assert fb["status"] == "ok"
    assert "Facebook page text" in fb["summary"]


def test_reddit_prove_your_humanity_is_captcha_not_content() -> None:
    assert detect_social_gate(
        REDDIT_HUMANITY_HTML,
        platform="reddit",
        page_title="Reddit - Prove your humanity",
    ) == "CAPTCHA/challenge"
    parsed = parse_cloak_social_html(
        REDDIT_HUMANITY_HTML,
        platform="reddit",
        url="https://www.reddit.com/r/test/comments/abc/title/",
        page_title="Reddit - Prove your humanity",
    )
    assert parsed["status"] == "error"
    assert any("CAPTCHA" in w for w in parsed["warnings"])
    assert parsed["status"] != "ok"


def test_facebook_autoriser_tous_les_cookies_is_consent_not_content() -> None:
    assert detect_social_gate(
        FACEBOOK_FR_CONSENT_HTML,
        platform="facebook",
        page_title="Facebook",
    ) == "consent wall"
    parsed = parse_cloak_social_html(
        FACEBOOK_FR_CONSENT_HTML,
        platform="facebook",
        url="https://www.facebook.com/zuck",
        page_title="Facebook",
    )
    assert parsed["status"] == "error"
    assert any("consent" in w.lower() for w in parsed["warnings"])
    assert parsed["status"] != "ok"
    assert parsed["status"] != "partial"
    assert "Autoriser tous les cookies" not in (parsed.get("content") or parsed.get("summary") or "")


def test_login_and_captcha_never_ok() -> None:
    assert detect_social_gate(LOGIN_HTML, platform="instagram") == "login/auth wall"
    assert detect_social_gate(CAPTCHA_HTML, platform="facebook") == "CAPTCHA/challenge"
    login = parse_cloak_social_html(LOGIN_HTML, platform="instagram", url="https://www.instagram.com/x/")
    assert login["status"] == "error"
    assert any("login" in w.lower() for w in login["warnings"])
    captcha = parse_cloak_social_html(CAPTCHA_HTML, platform="reddit", url="https://www.reddit.com/r/x/")
    assert captcha["status"] == "error"
    assert any("CAPTCHA" in w for w in captcha["warnings"])


def test_cloak_order_before_opencli_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def cloak_fetcher(url, **kwargs):
        calls.append("cloak")
        return FakePage(url, 200, INSTAGRAM_HTML, title="NASA on Instagram", method="cloak")

    def boom_opencli(*args, **kwargs):
        calls.append("opencli")
        raise AssertionError("OpenCLI must not run when Cloak succeeds")

    monkeypatch.setattr(
        "supersocks_url_scraper.social.routing.extract_instagram",
        boom_opencli,
    )
    result = try_social_read(
        "https://www.instagram.com/nasa/",
        cloak_fetcher=cloak_fetcher,
        opencli_fallback=True,
    )
    assert result is not None
    assert result["status"] == "ok"
    assert result["fetch_method"] == "cloak"
    assert calls == ["cloak"]


def test_opencli_fallback_only_when_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    def cloak_fetcher(url, **kwargs):
        return FakePage(url, 200, LOGIN_HTML, title="Log in", method="cloak")

    monkeypatch.delenv("SOCIAL_OPENCLI_FALLBACK", raising=False)
    monkeypatch.delenv("OPENCLI_FALLBACK", raising=False)
    opencli_calls = {"n": 0}

    def opencli_probe(**kwargs):
        opencli_calls["n"] += 1
        return OpenCLIStatus(installed=False, hint="missing")

    monkeypatch.setattr("supersocks_url_scraper.social.meta_opencli.probe_opencli", opencli_probe)
    blocked = try_social_read(
        "https://www.instagram.com/nasa/",
        cloak_fetcher=cloak_fetcher,
        opencli_fallback=False,
    )
    assert blocked is not None
    assert blocked["fetch_method"] == "cloak"
    assert opencli_calls["n"] == 0
    assert any("opt-in" in w.lower() for w in blocked["warnings"])

    monkeypatch.setattr(
        "supersocks_url_scraper.social.meta_opencli.probe_opencli",
        lambda **kwargs: OpenCLIStatus(installed=False, hint="OpenCLI missing"),
    )
    with_fallback = try_social_read(
        "https://www.instagram.com/nasa/",
        cloak_fetcher=cloak_fetcher,
        opencli_fallback=True,
    )
    assert with_fallback is not None
    assert any("opencli" in w.lower() for w in with_fallback["warnings"])


def test_missing_cloak_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.social.cloak_social.cloakbrowser_available",
        lambda: False,
    )
    result = extract_cloak_social("https://www.reddit.com/r/announcements/", platform="reddit")
    assert result is not None
    assert result["status"] == "error"
    assert result["fetch_method"] == "cloak"
    assert any("browser extra" in w for w in result["warnings"])


def test_missing_profile_dir(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-profile"
    result = extract_cloak_social(
        "https://www.facebook.com/zuck",
        platform="facebook",
        browser_profile_dir=str(missing),
        cloak_fetcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert result is not None
    assert result["status"] == "error"
    assert result["fetch_method"] == "cloak-profile"
    assert any("configured social Cloak profile is absent" in w for w in result["warnings"])
    assert "no-such-profile" not in json.dumps(result)


def test_missing_profile_warning_never_discloses_secret_basename(tmp_path: Path) -> None:
    secret_profile = tmp_path / "profiles" / "auth_token=SUPERSECRET"
    result = extract_cloak_social(
        "https://www.facebook.com/zuck",
        platform="facebook",
        browser_profile_dir=str(secret_profile),
        cloak_fetcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    dumped = json.dumps(result)
    assert "SUPERSECRET" not in dumped
    assert "auth_token" not in dumped
    assert secret_profile.name not in dumped
    assert any("configured social Cloak profile is absent" in w for w in result["warnings"])


def test_fetcher_called_once_on_typeerror() -> None:
    calls = {"n": 0}

    def broken_fetcher(url, **kwargs):
        calls["n"] += 1
        raise TypeError("unexpected keyword argument 'headless'")

    result = extract_cloak_social(
        "https://www.reddit.com/r/announcements/",
        platform="reddit",
        cloak_fetcher=broken_fetcher,
    )
    assert calls["n"] == 1
    assert result is not None
    assert result["status"] == "error"
    assert any("cloak social render failed" in w for w in result["warnings"])


def test_captcha_markers_in_script_do_not_trigger_gate() -> None:
    html = """
    <html><head>
    <meta property="og:title" content="Public post" />
    <meta property="og:description" content="Enough visible caption text for extraction without triggering a false challenge gate from embedded JavaScript." />
    </head><body>
    <article><p>Enough visible caption text for extraction without triggering a false challenge gate from embedded JavaScript.</p></article>
    <script>var captcha = "recaptcha"; function verifyYouAreHuman() {}</script>
    </body></html>
    """
    assert detect_social_gate(html, platform="instagram") is None
    parsed = parse_cloak_social_html(html, platform="instagram", url="https://www.instagram.com/nasa/")
    assert parsed["status"] == "ok"
    assert not any("CAPTCHA" in w for w in parsed["warnings"])


def test_rdt_cli_opt_in_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RDT_CLI_FALLBACK", raising=False)
    assert extract_reddit_rdt("https://www.reddit.com/r/announcements/") is None

    def runner(argv, timeout=30, env=None):
        assert argv[:2] == ["rdt", "read"]
        assert env is not None
        assert "RDT_COOKIE_FILE" not in env
        payload = {
            "title": "Hello Reddit",
            "selftext": "A long enough mocked Reddit body for the rdt-cli fallback path summary.",
            "author": "spez",
        }
        return CommandResult(0, json.dumps(payload), "")

    result = extract_reddit_rdt(
        "https://www.reddit.com/r/announcements/",
        enabled=True,
        runner=runner,
    )
    assert result is not None
    assert result["fetch_method"] == "rdt-cli"
    assert result["status"] == "ok"
    assert result["author"] == "spez"


def test_cloak_then_rdt_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def cloak_fetcher(url, **kwargs):
        order.append("cloak")
        return FakePage(url, 200, LOGIN_HTML, method="cloak")

    def runner(argv, timeout=30, env=None):
        order.append("rdt")
        return CommandResult(
            0,
            json.dumps(
                {
                    "title": "Recovered",
                    "selftext": (
                        "Recovered Reddit body with enough characters from the "
                        "opt-in rdt-cli fallback path for routing coverage."
                    ),
                    "author": "spez",
                }
            ),
            "",
        )

    result = try_social_read(
        "https://www.reddit.com/r/announcements/comments/abc/title/",
        cloak_fetcher=cloak_fetcher,
        rdt_cli_fallback=True,
        rdt_runner=runner,
    )
    assert order == ["cloak", "rdt"]
    assert result is not None
    assert result["fetch_method"] == "rdt-cli"
    assert result["status"] == "ok"


def test_no_secret_leak_in_cloak_warnings(tmp_path: Path) -> None:
    secret_profile = tmp_path / "profiles" / "auth_token=SUPERSECRET"
    secret_profile.mkdir(parents=True)
    html = REDDIT_HTML + "<!-- auth_token=SUPERSECRET cookie=leak -->"

    def cloak_fetcher(url, **kwargs):
        assert kwargs.get("profile_dir")
        return FakePage(url, 200, html, method="cloak-profile")

    result = extract_cloak_social(
        "https://www.reddit.com/r/announcements/",
        platform="reddit",
        browser_profile_dir=str(secret_profile),
        cloak_fetcher=cloak_fetcher,
        include_content=True,
    )
    dumped = json.dumps(result)
    assert "SUPERSECRET" not in dumped
    # HTML comment garbage is cleaned into text space; ensure explicit secret patterns are redacted if present in warnings.
    assert result["status"] == "ok"


def test_headed_requires_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("CLOAK_HEADLESS", "0")
    assert resolve_headless(None) is False
    from supersocks_url_scraper.browser_fetcher import BrowserFetchError, _ensure_display_for_headed

    with pytest.raises(BrowserFetchError, match="DISPLAY"):
        _ensure_display_for_headed(False)


def test_health_and_openapi_include_reddit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.explicit_twitter_credentials_present", lambda: False)
    monkeypatch.setattr(
        "supersocks_url_scraper.social.opencli.probe_opencli",
        lambda timeout=3, **kwargs: OpenCLIStatus(installed=False),
    )
    monkeypatch.setattr("supersocks_url_scraper.social.reddit_rdt.rdt_cli_available", lambda: False)
    body = cli.health_payload()
    assert "reddit" in body["social"]["platforms"]
    assert body["social"]["cloak_first_platforms"] == ["reddit", "instagram", "facebook"]
    assert body["social"]["opencli_fallback_default"] is False
    assert body["social"]["rdt_cli_fallback_default"] is False
    assert "TWITTER_AUTH_TOKEN" not in json.dumps(body)

    schema = cli.openapi_payload()
    platform_enum = schema["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["platform"]["enum"]
    assert "reddit" in platform_enum
    methods = schema["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["fetch_method"]["enum"]
    assert "rdt-cli" in methods
    assert "cloak" in methods


def test_read_url_routes_reddit_cloak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.social.routing.extract_cloak_social",
        lambda url, **kwargs: parse_cloak_social_html(
            REDDIT_HTML,
            platform="reddit",
            url=url,
            page_title="Public Reddit announcement",
            length=kwargs.get("length", 900),
            include_content=kwargs.get("include_content", False),
            fetch_method="cloak",
        ),
    )
    result = read_url("https://www.reddit.com/r/announcements/")
    assert result["platform"] == "reddit"
    assert result["fetch_method"] == "cloak"
    assert result["status"] == "ok"


def test_direct_opencli_extractor_still_works() -> None:
    """OpenCLI module remains available for explicit/opt-in callers."""
    result = extract_instagram(
        "https://www.instagram.com/nasa/",
        status_override=OpenCLIStatus(installed=False, hint="OpenCLI not available on PATH; install hint"),
    )
    assert result["fetch_method"] == "opencli"
    assert result["status"] == "error"
