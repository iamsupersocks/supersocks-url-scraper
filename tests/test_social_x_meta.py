"""Mocked unit tests for X/twitter-cli and Instagram/Facebook OpenCLI routing."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from supersocks_url_scraper import cli
from supersocks_url_scraper.reader import read_url
from supersocks_url_scraper.social.backend import CommandResult, redact_secrets
from supersocks_url_scraper.social.domains import detect_platform
from supersocks_url_scraper.social.meta_opencli import extract_facebook, extract_instagram
from supersocks_url_scraper.social.opencli import OpenCLIStatus, probe_opencli
from supersocks_url_scraper.social.routing import try_social_read
from supersocks_url_scraper.social.twitter_x import classify_x_url, extract_x


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/user/status/1234567890", "x"),
        ("https://twitter.com/user/status/1234567890", "x"),
        ("https://www.instagram.com/nasa/", "instagram"),
        ("https://www.facebook.com/zuck", "facebook"),
        ("https://fb.com/zuck", "facebook"),
        ("https://notx.com/user/status/1", None),
        ("https://user:pass@x.com/user/status/1", None),
    ],
)
def test_detect_new_social_platforms(url: str, expected: str | None) -> None:
    assert detect_platform(url) == expected


def test_redact_secrets_never_echoes_tokens() -> None:
    raw = "auth_token=SUPERSECRETVALUE ct0=ALSOSECRET TWITTER_AUTH_TOKEN=leak"
    redacted = redact_secrets(raw)
    assert "SUPERSECRETVALUE" not in redacted
    assert "ALSOSECRET" not in redacted
    assert "leak" not in redacted
    assert "[REDACTED]" in redacted


def test_classify_x_url_shapes() -> None:
    assert classify_x_url("https://x.com/a/status/42") == ("status", "42")
    assert classify_x_url("https://x.com/i/article/99") == ("article", "99")
    assert classify_x_url("https://x.com/nasa") == ("user", "nasa")


def test_x_missing_twitter_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    result = extract_x("https://x.com/nasa/status/123")
    assert result is not None
    assert result["platform"] == "x"
    assert result["status"] == "error"
    assert result["fetch_method"] == "twitter-cli"
    assert any("twitter-cli" in w and "not available" in w for w in result["warnings"])


def test_x_does_not_invoke_cli_without_explicit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: True)
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    called = {"n": 0}

    def runner(argv, timeout=30, env=None):
        called["n"] += 1
        return CommandResult(0, "{}", "")

    result = extract_x("https://x.com/nasa/status/123", runner=runner)
    assert called["n"] == 0
    assert result["status"] == "error"
    assert any("TWITTER_AUTH_TOKEN" in w and "never auto-reads" in w for w in result["warnings"])


def test_x_tweet_parsing_with_mock_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: True)
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "token-value")
    monkeypatch.setenv("TWITTER_CT0", "ct0-value")

    payload = {
        "ok": True,
        "schema_version": "1",
        "data": {
            "id": "123",
            "text": "Hello from a mocked tweet with enough text for a summary body.",
            "created_at": "2026-08-01T12:00:00Z",
            "author": {"name": "NASA", "screen_name": "nasa"},
        },
    }

    def runner(argv, timeout=30, env=None):
        assert argv[:2] == ["twitter", "tweet"]
        assert "--json" in argv
        assert env is not None
        assert env.get("TWITTER_AUTH_TOKEN") == "token-value"
        assert "TWITTER_BROWSER" not in env
        # Ensure secrets are present for upstream but never returned by our payload.
        return CommandResult(0, json.dumps(payload), "")

    result = extract_x("https://x.com/nasa/status/123", include_content=True, runner=runner)
    assert result["status"] == "ok"
    assert result["platform"] == "x"
    assert result["fetch_method"] == "twitter-cli"
    assert result["author"] == "NASA"
    assert "mocked tweet" in result["summary"]
    assert "token-value" not in json.dumps(result)
    assert "ct0-value" not in json.dumps(result)


def test_read_url_routes_x_without_falling_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    result = read_url("https://x.com/nasa/status/123")
    assert result["platform"] == "x"
    assert result["fetch_method"] == "twitter-cli"
    assert result["status"] == "error"


def test_opencli_probe_missing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.opencli.opencli_available", lambda: False)
    status = probe_opencli()
    assert status.installed is False
    assert "npm install -g" in status.hint
    assert "ZIP" in status.hint or "never auto-installed" in status.hint


def test_opencli_probe_extension_disconnected() -> None:
    def runner(argv, timeout=30, env=None):
        assert argv == ["opencli", "--version"]
        return CommandResult(0, "1.8.5\n", "")

    status = probe_opencli(
        runner=runner,
        daemon_fetcher=lambda timeout=2: {"ok": True, "extensionConnected": False},
    )
    assert status.installed is True
    assert status.extension_connected is False
    assert "extension" in status.hint.lower()


def test_instagram_missing_opencli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supersocks_url_scraper.social.meta_opencli.probe_opencli",
        lambda **kwargs: OpenCLIStatus(installed=False, hint="OpenCLI not available on PATH; install hint"),
    )
    result = extract_instagram("https://www.instagram.com/nasa/")
    assert result["platform"] == "instagram"
    assert result["status"] == "error"
    assert result["fetch_method"] == "opencli"
    assert any("OpenCLI" in w for w in result["warnings"])


def test_facebook_profile_via_opencli_mock() -> None:
    ready = OpenCLIStatus(installed=True, extension_connected=True, version="1.8.5")
    payload = {"name": "Mark", "username": "zuck", "bio": "Building things across Meta platforms every day."}

    def runner(argv, timeout=30, env=None):
        # run_command receives full argv including opencli when using run_opencli
        assert "facebook" in argv and "profile" in argv and "zuck" in argv
        return CommandResult(0, json.dumps(payload), "")

    result = extract_facebook(
        "https://www.facebook.com/zuck",
        include_content=True,
        runner=runner,
        status_override=ready,
    )
    assert result["status"] == "ok"
    assert result["platform"] == "facebook"
    assert result["fetch_method"] == "opencli"
    assert result["author"] in {"zuck", "Mark"}
    assert "Building things" in result["summary"]


def test_instagram_post_uses_web_read_stdout_markdown() -> None:
    ready = OpenCLIStatus(installed=True, extension_connected=True, version="1.8.6")
    post_url = "https://www.instagram.com/p/AbCdEfGhIjK/"
    markdown = (
        "# NASA Post\n\n"
        "A long enough caption from a mocked Instagram post body for summary."
    )

    def runner(argv, timeout=30, env=None):
        assert argv == [
            "opencli",
            "web",
            "read",
            "--url",
            post_url,
            "--download-images",
            "false",
            "--stdout",
        ]
        assert "-f" not in argv
        assert "json" not in argv
        return CommandResult(0, markdown, "")

    result = extract_instagram(
        post_url,
        include_content=True,
        runner=runner,
        status_override=ready,
    )
    assert result["status"] == "ok"
    assert result["title"] == "NASA Post"
    assert "caption" in result["summary"]
    assert result["content"] == markdown


def test_facebook_post_uses_web_read_stdout_markdown() -> None:
    ready = OpenCLIStatus(installed=True, extension_connected=True, version="1.8.6")
    post_url = "https://www.facebook.com/watch/?v=1234567890"
    markdown = "# Demo\n\nFacebook watch page body with enough text for a readable summary."

    def runner(argv, timeout=30, env=None):
        assert argv == [
            "opencli",
            "web",
            "read",
            "--url",
            post_url,
            "--download-images",
            "false",
            "--stdout",
        ]
        return CommandResult(0, markdown, "")

    result = extract_facebook(
        post_url,
        runner=runner,
        status_override=ready,
    )
    assert result["status"] == "ok"
    assert result["title"] == "Demo"
    assert "Facebook watch page" in result["summary"]


def test_try_social_read_routes_all_new_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    x = try_social_read("https://x.com/a/status/1")
    assert x and x["platform"] == "x"

    @dataclass
    class FakePage:
        final_url: str
        status_code: int
        html: str
        title: str | None = None
        method: str = "cloak"
        consent_action: str | None = None

    def cloak_fetcher(url, **kwargs):
        html = (
            "<html><head><meta property='og:title' content='Demo' />"
            "<meta property='og:description' content='A long enough mocked social body for cloak-first routing coverage.' />"
            "</head><body><article><p>A long enough mocked social body for cloak-first routing coverage.</p></article></body></html>"
        )
        return FakePage(url, 200, html, title="Demo", method="cloak")

    ig = try_social_read("https://www.instagram.com/nasa/", cloak_fetcher=cloak_fetcher)
    assert ig and ig["platform"] == "instagram"
    assert ig["fetch_method"] == "cloak"
    fb = try_social_read("https://www.facebook.com/zuck", cloak_fetcher=cloak_fetcher)
    assert fb and fb["platform"] == "facebook"
    assert fb["fetch_method"] == "cloak"


def test_health_lists_extended_social_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.twitter_cli_available", lambda: False)
    monkeypatch.setattr("supersocks_url_scraper.social.twitter_x.explicit_twitter_credentials_present", lambda: False)
    monkeypatch.setattr(
        "supersocks_url_scraper.cli.probe_opencli",
        lambda timeout=3: OpenCLIStatus(installed=False),
        raising=False,
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.social.opencli.probe_opencli",
        lambda timeout=3, **kwargs: OpenCLIStatus(installed=False),
    )
    body = cli.health_payload()
    assert body["social"]["platforms"] == ["youtube", "linkedin", "x", "instagram", "facebook", "reddit"]
    assert body["social"]["twitter_cli_available"] is False
    assert body["social"]["twitter_explicit_credentials"] is False
    assert body["social"]["opencli_available"] is False
    assert body["social"]["opencli_fallback_default"] is False
    assert "TWITTER_AUTH_TOKEN" not in json.dumps(body)
