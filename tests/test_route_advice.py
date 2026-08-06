"""Tests for offline agent route_advice / recurrent_need guidance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from supersocks_url_scraper.api_recipes import (
    build_route_advice,
    find_matching_recipes,
    load_builtin_recipes,
    load_recipe_file,
    recipe_advice_meta,
    recipe_from_dict,
    try_api_recipe,
    unsuitable_for_api_discovery,
)
from supersocks_url_scraper.api_recipes.security import SafeGetResult
from supersocks_url_scraper.cli import health_payload, openapi_payload
from supersocks_url_scraper.reader import FetchedResource, read_url, to_markdown

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_RECIPE = ROOT / "examples" / "recipes" / "flashscore_odds.v1.json"
DEMO_URL = "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"


def _html_resource(url: str, body: str = "<html><head><title>Demo</title></head><body><article><p>Hello recurrent page content for agents.</p></article></body></html>") -> FetchedResource:
    return FetchedResource(
        url=url,
        final_url=url,
        status_code=200,
        content=body.encode("utf-8"),
        content_type="text/html; charset=utf-8",
        headers={"x-fetch-method": "http"},
    )


def _fixture_fetcher():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    samples = payload.get("bookmakers") or {}

    def fetcher(url: str, **kwargs: Any) -> SafeGetResult:
        bookmaker_id = "141"
        if "bookmakerId=" in url:
            bookmaker_id = url.split("bookmakerId=", 1)[1].split("&", 1)[0]
        body = samples.get(str(bookmaker_id)) or {"data": {"findPrematchOddsForBookmaker": {}}}
        return SafeGetResult(
            url=url,
            final_url=url,
            status_code=200,
            content=json.dumps(body).encode("utf-8"),
            content_type="application/json",
            headers={"content-type": "application/json"},
            elapsed_ms=1,
        )

    return fetcher


def _example_recipe():
    return load_recipe_file(EXAMPLE_RECIPE)


def test_default_read_has_no_route_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://news.example/article"

    def fake_pipeline(fetch_url: str, **kwargs: Any) -> FetchedResource:
        return _html_resource(fetch_url)

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(url, skip_social_routing=True, seo_fallback=False, browser_fallback=False, archive_fallback=False)
    assert "route_advice" not in result
    assert result.get("status") in {"ok", "partial"}


def test_flashscore_url_without_explicit_recipe_suggests_discovery_when_recurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No builtin Flashscore recipe — standard scrape; recurrent_need → generic discovery advice."""

    def fake_pipeline(fetch_url: str, **kwargs: Any) -> FetchedResource:
        return _html_resource(fetch_url, "<html><body><article><p>Match page prose.</p></article></body></html>")

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(
        DEMO_URL,
        api_recipes=False,
        recurrent_need=True,
        skip_social_routing=True,
        seo_fallback=False,
        browser_fallback=False,
        archive_fallback=False,
    )
    advice = result.get("route_advice")
    assert isinstance(advice, dict)
    assert advice["state"] == "suggested"
    assert advice["recommended"] == "api_discovery"
    assert advice["network_attempted"] is False
    blob = json.dumps(advice).lower()
    assert "flashscore" not in blob
    assert "discover-har" in (advice.get("next_command") or "")


def test_explicit_example_recipe_available_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = _example_recipe()

    def fake_pipeline(fetch_url: str, **kwargs: Any) -> FetchedResource:
        return _html_resource(fetch_url)

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(
        DEMO_URL,
        api_recipes=False,
        api_recipe_paths=[str(EXAMPLE_RECIPE)],
        skip_social_routing=True,
        seo_fallback=False,
        browser_fallback=False,
        archive_fallback=False,
    )
    advice = result.get("route_advice")
    assert isinstance(advice, dict)
    assert advice["state"] == "available_disabled"
    assert advice["recommended"] == "api_recipe"
    assert advice["recipe"]["id"] == recipe.id
    assert advice["recipe"]["network_mode"] == "open"
    assert "--api-recipes" in (advice.get("next_command") or "")


def test_review_required_candidate_blocks_execution() -> None:
    recipe = recipe_from_dict(
        {
            "id": "har-example-com-api",
            "version": "1",
            "title": "Candidate",
            "status": "review_required",
            "review_required": True,
            "network": {"mode": "fixture_only"},
            "match": {"host_roots": ["example.com"], "path_regex": "/dashboard"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/data",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    assert recipe.needs_review is True
    payload = try_api_recipe(
        "https://www.example.com/dashboard",
        enabled=True,
        recipes=[recipe],
        fetcher=_fixture_fetcher(),
        resolve_dns=False,
    )
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is True
    assert payload.get("_api_recipe_review_blocked") is True
    advice = build_route_advice(
        "https://www.example.com/dashboard",
        matched=recipe,
        api_recipes_enabled=True,
        recipe_blocked_fallback=True,
    )
    assert advice is not None
    assert advice["state"] == "review_required"
    assert advice["recommended"] == "review_recipe"
    assert advice["network_attempted"] is False


def test_recipe_advice_meta_exposes_review_required_for_candidate() -> None:
    recipe = recipe_from_dict(
        {
            "id": "har-example-com-api",
            "version": "1",
            "title": "Candidate",
            "status": "review_required",
            "review_required": True,
            "network": {"mode": "fixture_only"},
            "match": {"host_roots": ["example.com"], "path_regex": "/dashboard"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/data",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    assert recipe.needs_review is True
    meta = recipe_advice_meta(recipe)
    assert meta["status"] == "review_required"
    assert meta["review_required"] is True

    advice = build_route_advice(
        "https://www.example.com/dashboard",
        matched=recipe,
        api_recipes_enabled=True,
        recipe_used=True,
    )
    assert advice is not None
    assert advice["recipe"]["review_required"] is True


def test_recipe_advice_meta_review_required_false_for_open_recipe() -> None:
    recipe = _example_recipe()
    assert recipe.needs_review is False
    meta = recipe_advice_meta(recipe)
    assert meta["status"] == "active"
    assert meta["review_required"] is False

    advice = build_route_advice(
        DEMO_URL,
        matched=recipe,
        api_recipes_enabled=True,
        recipe_used=True,
    )
    assert advice is not None
    assert advice["recipe"]["review_required"] is False


def test_discovery_command_never_embeds_caller_url() -> None:
    """A hostile URL with newline / shell text must not leak into next_command."""
    hostile = "https://evil.example/path\nrm -rf /tmp/x && echo PWNED"
    advice = build_route_advice(
        hostile,
        recurrent_need=True,
        result={"content_type": "article", "status": "ok", "fetch_method": "archive"},
    )
    assert advice is not None
    assert advice["recommended"] == "api_discovery"
    cmd = advice["next_command"]
    assert isinstance(cmd, str)
    assert "evil.example" not in cmd
    assert "rm -rf" not in cmd
    assert "PWNED" not in cmd
    for line in cmd.splitlines():
        stripped = line.strip()
        assert stripped.startswith("#") or stripped.startswith("supersocks-url-scraper"), stripped


def test_used_state_after_successful_recipe() -> None:
    recipe = _example_recipe()
    payload = try_api_recipe(
        DEMO_URL,
        enabled=True,
        recipes=[recipe],
        fetcher=_fixture_fetcher(),
        resolve_dns=False,
    )
    assert payload is not None
    assert payload.get("status") == "ok"
    assert payload.get("_api_recipe_fallback") is not True
    advice = build_route_advice(
        DEMO_URL,
        matched=recipe,
        api_recipes_enabled=True,
        recipe_used=True,
        result=payload,
        network_attempted=False,
    )
    assert advice is not None
    assert advice["state"] == "used"
    assert advice["recommended"] == "api_recipe"
    assert advice["recipe"]["id"] == "flashscore-odds"
    assert advice["network_attempted"] is False
    md = to_markdown({**payload, "route_advice": advice})
    assert "## Route advice" in md
    assert "used" in md


def test_consent_required_recipe_disabled_available_disabled() -> None:
    recipe = recipe_from_dict(
        {
            "id": "demo-live",
            "version": "1",
            "title": "Demo live",
            "status": "active",
            "network": {"mode": "consent_required"},
            "match": {"host_roots": ["example.com"], "path_regex": "/item"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/item",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    advice = build_route_advice(
        "https://www.example.com/item/1",
        matched=recipe,
        api_recipes_enabled=False,
    )
    assert advice is not None
    assert advice["state"] == "available_disabled"
    assert advice["recommended"] == "api_recipe"
    assert "--api-recipes" in (advice.get("next_command") or "")


def test_archive_fetch_suggests_api_discovery() -> None:
    advice = build_route_advice(
        "https://shop.example.com/catalog",
        recurrent_need=False,
        result={"content_type": "article", "status": "ok", "fetch_method": "archive"},
    )
    assert advice is not None
    assert advice["state"] == "suggested"
    assert advice["recommended"] == "api_discovery"
    assert "discover-har" in (advice.get("next_command") or "")
    assert "flashscore" not in json.dumps(advice).lower()


def test_blocked_fallback_consent_required() -> None:
    recipe = recipe_from_dict(
        {
            "id": "demo-live",
            "version": "1",
            "title": "Demo live",
            "status": "active",
            "network": {"mode": "consent_required"},
            "match": {"host_roots": ["example.com"], "path_regex": "/item"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/item",
                "allowed_hosts": ["api.example.com"],
            },
            "fallback": "http_seo_cloak_archive",
        }
    )
    payload = try_api_recipe(
        "https://www.example.com/item/1",
        enabled=True,
        recipes=[recipe],
        resolve_dns=False,
    )
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is True
    advice = build_route_advice(
        "https://www.example.com/item/1",
        matched=recipe,
        api_recipes_enabled=True,
        recipe_blocked_fallback=True,
        block_reason="Live network blocked; fell back to http_seo_cloak_archive.",
    )
    assert advice is not None
    assert advice["state"] == "blocked"
    assert advice["recommended"] == "standard_pipeline"
    assert advice["network_attempted"] is False
    requires = advice.get("requires") or []
    assert any("API_RECIPE_LIVE_ALLOWLIST" in r for r in requires)
    assert any("API_RECIPE_LIVE_CONSENT" in r for r in requires)
    assert "<url>" in (advice.get("next_command") or "")


def test_recurrent_need_suggests_api_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://prices.example.com/dashboard"

    def fake_pipeline(fetch_url: str, **kwargs: Any) -> FetchedResource:
        return _html_resource(fetch_url)

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(
        url,
        recurrent_need=True,
        skip_social_routing=True,
        seo_fallback=False,
        browser_fallback=False,
        archive_fallback=False,
    )
    advice = result.get("route_advice")
    assert isinstance(advice, dict)
    assert advice["state"] == "suggested"
    assert advice["recommended"] == "api_discovery"
    assert advice["network_attempted"] is False
    assert "discover-har" in (advice.get("next_command") or "")
    assert "manual" in " ".join(advice.get("requires") or []).lower() or "har" in " ".join(advice.get("requires") or []).lower()


def test_pdf_and_social_excluded_from_discovery() -> None:
    assert unsuitable_for_api_discovery("https://cdn.example.com/report.pdf", content_type="pdf") is True
    assert unsuitable_for_api_discovery("https://cdn.example.com/pic.png", content_type="image") is True
    assert unsuitable_for_api_discovery("https://www.reddit.com/r/announcements/", platform="reddit") is True
    assert unsuitable_for_api_discovery("https://www.instagram.com/nasa/", content_type="article") is True
    assert unsuitable_for_api_discovery("https://prices.example.com/app", content_type="article") is False

    advice = build_route_advice(
        "https://cdn.example.com/report.pdf",
        recurrent_need=True,
        result={"content_type": "pdf", "status": "ok", "fetch_method": "http"},
    )
    assert advice is None
    social = build_route_advice(
        "https://www.reddit.com/r/announcements/",
        recurrent_need=True,
        result={"content_type": "article", "status": "ok", "fetch_method": "cloak", "platform": "reddit"},
    )
    assert social is None


def test_markdown_http_openapi_serialization() -> None:
    advice = {
        "recommended": "api_discovery",
        "state": "suggested",
        "reason": "Recurrent need; capture HAR manually then discover offline.",
        "requires": ["manual_har_capture", "offline_discover_har"],
        "next_command": "supersocks-url-scraper --discover-har capture.har",
        "network_attempted": False,
    }
    md = to_markdown(
        {
            "url": "https://example.com/app",
            "status": "ok",
            "content_type": "article",
            "fetch_method": "http",
            "title": "App",
            "summary": "hi",
            "route_advice": advice,
        }
    )
    assert "## Route advice" in md
    assert "api_discovery" in md
    assert "suggested" in md

    spec = openapi_payload()
    props = spec["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert "recurrent_need" in props
    assert props["recurrent_need"]["default"] is False
    result_props = spec["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert "route_advice" in result_props
    assert "available_disabled" in result_props["route_advice"]["properties"]["state"]["enum"]
    assert "flashscore" not in json.dumps(spec).lower()

    health = health_payload()
    assert health["api_recipes"]["route_advice"] is True
    assert health["api_recipes"]["recurrent_need_default"] is False
    assert health["api_recipes"]["builtin"] == []
    assert "flashscore" not in json.dumps(health["api_recipes"]).lower()


def test_route_advice_matching_makes_zero_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected network: {args!r} {kwargs!r}")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("socket.create_connection", boom)

    assert find_matching_recipes(DEMO_URL, load_builtin_recipes()) == []
    matches = find_matching_recipes(DEMO_URL, [_example_recipe()])
    assert matches and matches[0].id == "flashscore-odds"
    advice = build_route_advice(
        DEMO_URL,
        matched=matches[0],
        api_recipes_enabled=False,
    )
    assert advice is not None
    assert advice["state"] == "available_disabled"
    assert advice["network_attempted"] is False

    discovery = build_route_advice(
        "https://shop.example.com/catalog",
        recurrent_need=True,
        result={"content_type": "article", "status": "partial", "fetch_method": "cloak"},
    )
    assert discovery is not None
    assert discovery["recommended"] == "api_discovery"


def test_cli_recurrent_flag_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    import supersocks_url_scraper.cli as cli

    captured: dict[str, Any] = {}

    def fake_read_url(url: str, **kwargs: Any) -> dict[str, Any]:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"url": url, "status": "ok", "content_type": "article", "title": "t", "summary": "s", "fetch_method": "http", "warnings": []}

    monkeypatch.setattr(cli, "read_url", fake_read_url)
    monkeypatch.setattr(
        "sys.argv",
        ["supersocks-url-scraper", "--recurrent", "https://example.com/app"],
    )
    assert cli.main() == 0
    assert captured["kwargs"].get("recurrent_need") is True


def test_read_url_used_path_attaches_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    """When try_api_recipe succeeds, read_url attaches state=used without pipeline fetch."""
    recipe = _example_recipe()

    def fake_try(url: str, **kwargs: Any) -> dict[str, Any]:
        run = try_api_recipe(
            url,
            enabled=True,
            recipes=[recipe],
            fetcher=_fixture_fetcher(),
            resolve_dns=False,
        )
        assert run is not None
        return run

    def boom_pipeline(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("pipeline must not run when recipe succeeds")

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", boom_pipeline)
    import supersocks_url_scraper.api_recipes as api_recipes_mod

    monkeypatch.setattr(api_recipes_mod, "try_api_recipe", fake_try)

    result = read_url(
        DEMO_URL,
        api_recipes=True,
        api_recipe_paths=[str(EXAMPLE_RECIPE)],
        skip_social_routing=True,
    )
    assert result.get("fetch_method") == "api-recipe"
    assert result["route_advice"]["state"] == "used"
    assert result["route_advice"]["network_attempted"] is False


def test_socket_not_opened_for_advice_only_explicit_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching + advice for an explicitly loaded recipe must not call urlopen before the mocked pipeline."""
    calls: list[str] = []

    def fake_urlopen(*args: Any, **kwargs: Any) -> Any:
        calls.append("urlopen")
        raise URLError("blocked")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    def fake_pipeline(fetch_url: str, **kwargs: Any) -> FetchedResource:
        calls.append("pipeline")
        return _html_resource(fetch_url)

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(
        DEMO_URL,
        api_recipes=False,
        api_recipe_paths=[str(EXAMPLE_RECIPE)],
        skip_social_routing=True,
        seo_fallback=False,
        browser_fallback=False,
        archive_fallback=False,
    )
    assert "urlopen" not in calls
    assert "pipeline" in calls
    assert result["route_advice"]["state"] == "available_disabled"
