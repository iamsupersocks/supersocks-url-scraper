"""Unit, security, consent, and fallback tests for optional API recipes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from supersocks_url_scraper.api_recipes import (
    DEFAULT_CONSENT_PHRASE,
    execute_recipe,
    live_network_permitted,
    load_builtin_recipes,
    load_recipe_file,
    load_recipes,
    recipe_from_dict,
    scrub_headers,
    try_api_recipe,
    validate_recipe_dict,
)
from supersocks_url_scraper.api_recipes.consent import live_network_permitted as consent_check
from supersocks_url_scraper.api_recipes.security import (
    ApiRecipeSecurityError,
    SafeGetResult,
    assert_safe_https_url,
    host_is_blocked,
    safe_get,
    sanitize_request_headers,
)
from supersocks_url_scraper.reader import read_url, to_markdown

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_RECIPE = ROOT / "examples" / "recipes" / "flashscore_odds.v1.json"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
DEMO_URL = "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34"
CORE_MODULES = [
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "engine.py",
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "models.py",
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "consent.py",
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "route_advice.py",
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "discovery.py",
    ROOT / "src" / "supersocks_url_scraper" / "api_recipes" / "__init__.py",
    ROOT / "src" / "supersocks_url_scraper" / "reader.py",
    ROOT / "src" / "supersocks_url_scraper" / "cli.py",
]


def _fixture_fetcher(fixture_path: Path = FIXTURE):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
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


def test_no_flashscore_builtin() -> None:
    builtins = load_builtin_recipes()
    assert builtins == []
    assert not any(r.id == "flashscore-odds" for r in builtins)
    matches = try_api_recipe(DEMO_URL, enabled=True)
    assert matches is None


def test_core_modules_have_no_flashscore_strings() -> None:
    pattern = re.compile(r"flashscore|Flashscore|FLASHSCORE|lsapp\.eu|bookmaker|event_id", re.I)
    offenders: list[str] = []
    for path in CORE_MODULES:
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_example_recipe_explicit_load_and_fixture_normalization() -> None:
    recipe = load_recipe_file(EXAMPLE_RECIPE)
    assert recipe.id == "flashscore-odds"
    assert recipe.network.mode == "open"
    assert "2.ds.lsapp.eu" in recipe.endpoint.allowed_hosts
    assert "_hash=ole2" in recipe.endpoint.url_template
    assert recipe.endpoint.method == "GET"
    assert "Authorization" not in recipe.endpoint.headers
    assert "Cookie" not in recipe.endpoint.headers

    catalog = load_recipes(extra_paths=[str(EXAMPLE_RECIPE)])
    assert any(r.id == "flashscore-odds" for r in catalog)
    run = execute_recipe(DEMO_URL, recipe, fetcher=_fixture_fetcher(), resolve_dns=False)
    assert run.status == "ok"
    assert run.structured_data["kind"] == "api_recipe_fanout"
    assert run.structured_data["bindings"]["event_id"] == "Ab12Cd34"
    assert len(run.structured_data["items"]) >= 4
    assert any(i.get("ok") for i in run.structured_data["items"])

    # Example-side normalization (not core).
    import sys

    sys.path.insert(0, str(ROOT / "examples"))
    from flashscore.odds_normalize import normalize_fanout_result

    structured = normalize_fanout_result(match_url=DEMO_URL, fanout_structured=run.structured_data)
    assert structured["kind"] == "flashscore_odds_1x2"
    assert len(structured["bookmakers"]) >= 4
    md = to_markdown({**run.as_reader_dict(), "structured_data": structured, "summary": "odds"})
    assert "Structured data" in md
    assert "flashscore_odds_1x2" in md


def test_live_network_policies() -> None:
    assert live_network_permitted("demo", network_mode="fixture_only", env={}) is False
    assert live_network_permitted("demo", network_mode="open", env={}) is True
    assert (
        live_network_permitted(
            "demo",
            network_mode="consent_required",
            env={"API_RECIPE_LIVE_ALLOWLIST": "demo"},
        )
        is False
    )
    assert (
        consent_check(
            "demo",
            network_mode="consent_required",
            env={
                "API_RECIPE_LIVE_ALLOWLIST": "demo,other",
                "API_RECIPE_LIVE_CONSENT": DEFAULT_CONSENT_PHRASE,
            },
        )
        is True
    )


def test_open_recipe_executes_on_global_opt_in_with_mocked_safe_get(monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = load_recipe_file(EXAMPLE_RECIPE)
    assert recipe.network.mode == "open"
    monkeypatch.setattr(
        "supersocks_url_scraper.api_recipes.engine.safe_get",
        _fixture_fetcher(),
    )
    payload = try_api_recipe(
        DEMO_URL,
        enabled=True,
        recipes=[recipe],
        resolve_dns=False,
    )
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is not True
    assert payload.get("status") == "ok"
    assert payload.get("_api_recipe_live_network") is True
    assert "2.ds.lsapp.eu" in (payload.get("structured_data") or {}).get("items", [{}])[0].get("endpoint_url", "")


def test_disabled_default_no_api_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def boom(url: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(url)
        raise AssertionError("safe_get must not run when api recipes disabled")

    monkeypatch.setattr("supersocks_url_scraper.api_recipes.engine.safe_get", boom)
    assert try_api_recipe(DEMO_URL, enabled=False, recipes=[load_recipe_file(EXAMPLE_RECIPE)]) is None
    assert calls == []


def test_review_required_candidate_blocked() -> None:
    recipe = recipe_from_dict(
        {
            "id": "candidate-demo",
            "version": "1",
            "status": "review_required",
            "network": {"mode": "fixture_only"},
            "match": {"host_roots": ["example.com"], "path_regex": "/x"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/x",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    payload = try_api_recipe("https://www.example.com/x", enabled=True, recipes=[recipe])
    assert payload is not None
    assert payload.get("_api_recipe_review_blocked") is True
    assert payload.get("_api_recipe_fallback") is True
    assert (payload.get("structured_data") or {}).get("execution_blocked") is True


def test_read_url_falls_back_when_no_recipe_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from supersocks_url_scraper.reader import FetchedResource

    def fake_pipeline(url: str, **kwargs: Any) -> FetchedResource:
        html = (
            "<html><head><title>Demo match</title></head>"
            "<body><article><p>Synthetic match page used after no recipe match.</p></article></body></html>"
        )
        return FetchedResource(
            url=url,
            final_url=url,
            status_code=200,
            content=html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
            headers={"x-fetch-method": "http"},
        )

    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", fake_pipeline)
    result = read_url(
        DEMO_URL,
        api_recipes=True,
        seo_fallback=False,
        browser_fallback=False,
        archive_fallback=False,
        skip_social_routing=True,
        include_content=False,
        length=200,
    )
    assert result.get("fetch_method") != "api-recipe"
    assert result.get("status") in {"ok", "partial"}


class _FakeHttpResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self.status = status
        self.code = status
        self.headers = headers or {"content-type": "application/json"}
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _CountingOpener:
    def __init__(self, responses: list[_FakeHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, request: Any, timeout: int = 8) -> _FakeHttpResponse:
        self.calls.append(request.full_url)
        if not self.responses:
            raise AssertionError("unexpected extra network call")
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "redirect_location",
    [
        "https://127.0.0.1/secret",
        "https://evil.example.com/secret",
    ],
)
def test_safe_get_blocks_redirect_before_second_network_call(redirect_location: str) -> None:
    opener = _CountingOpener(
        [
            _FakeHttpResponse(
                url="https://api.example.com/start",
                status=302,
                headers={"Location": redirect_location},
            )
        ]
    )
    with pytest.raises(ApiRecipeSecurityError):
        safe_get(
            "https://api.example.com/start",
            allowed_hosts={"api.example.com"},
            opener=opener,
            resolve_dns=False,
        )
    assert len(opener.calls) == 1


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_safe_get_auth_and_rate_limit_statuses_raise_without_retry(status_code: int) -> None:
    opener = _CountingOpener(
        [
            _FakeHttpResponse(
                url="https://api.example.com/item",
                status=status_code,
                headers={"content-type": "application/json"},
            )
        ]
    )
    with pytest.raises(ApiRecipeSecurityError, match=str(status_code)):
        safe_get(
            "https://api.example.com/item",
            allowed_hosts={"api.example.com"},
            opener=opener,
            resolve_dns=False,
        )
    assert len(opener.calls) == 1


def test_security_blocks_private_hosts_and_auth_headers() -> None:
    assert host_is_blocked("127.0.0.1", resolve_dns=False) is True
    assert host_is_blocked("localhost", resolve_dns=False) is True
    with pytest.raises(ApiRecipeSecurityError):
        assert_safe_https_url("http://example.com/x", resolve_dns=False)
    with pytest.raises(ApiRecipeSecurityError):
        assert_safe_https_url("https://127.0.0.1/x", resolve_dns=False)
    with pytest.raises(ApiRecipeSecurityError):
        sanitize_request_headers({"Authorization": "Bearer secret"})
    with pytest.raises(ApiRecipeSecurityError):
        sanitize_request_headers({"Cookie": "a=b"})
    scrubbed = scrub_headers({"Authorization": "Bearer x", "content-type": "application/json"})
    assert scrubbed["authorization"] == "[REDACTED]"
    assert scrubbed["content-type"] == "application/json"


def test_recipe_schema_rejects_auth_headers_and_http() -> None:
    bad = {
        "id": "bad",
        "version": "1",
        "match": {"host_roots": ["example.com"]},
        "endpoint": {
            "method": "POST",
            "url_template": "http://example.com/{id}",
            "allowed_hosts": ["example.com"],
            "headers": {"Authorization": "Bearer x"},
        },
    }
    errors = validate_recipe_dict(bad)
    assert any("GET" in e for e in errors)
    assert any("https" in e for e in errors)
    assert any("Authorization" in e or "authorization" in e.lower() for e in errors)


def test_generic_open_recipe_with_injected_fetcher() -> None:
    recipe = recipe_from_dict(
        {
            "id": "demo-json",
            "version": "1",
            "title": "Demo",
            "network": {"mode": "open"},
            "match": {"host_roots": ["example.com"], "path_regex": "/item"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/item",
                "allowed_hosts": ["api.example.com"],
                "timeout_seconds": 3,
                "max_bytes": 4096,
                "max_fanout": 1,
            },
            "warnings": ["demo only"],
        }
    )

    def fetcher(url: str, **kwargs: Any) -> SafeGetResult:
        return SafeGetResult(
            url=url,
            final_url=url,
            status_code=200,
            content=b'{"ok": true, "n": 1}',
            content_type="application/json",
            headers={"content-type": "application/json"},
            elapsed_ms=1,
        )

    run = execute_recipe("https://www.example.com/item/1", recipe, fetcher=fetcher, resolve_dns=False)
    assert run.status == "ok"
    assert run.structured_data["payload"]["ok"] is True


def test_try_api_recipe_disabled_returns_none() -> None:
    assert try_api_recipe(DEMO_URL, enabled=False) is None


def test_generic_recipe_source_id_and_region_fanout() -> None:
    recipe = recipe_from_dict(
        {
            "id": "demo-regions",
            "version": "1",
            "network": {"mode": "open"},
            "match": {"host_roots": ["example.com"], "query_keys": ["source_id"]},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/prices?source_id={source_id}&region={region}",
                "allowed_hosts": ["api.example.com"],
                "max_fanout": 3,
            },
            "params": {
                "bindings": {
                    "source_id": {"query": "source_id", "pattern": "^[A-Za-z0-9]+$"},
                },
                "fanout": {
                    "template_var": "region",
                    "items": ["eu", "us", "apac"],
                },
            },
        }
    )
    calls: list[str] = []

    def fetcher(url: str, **kwargs: Any) -> SafeGetResult:
        calls.append(url)
        return SafeGetResult(
            url=url,
            final_url=url,
            status_code=200,
            content=b'{"ok": true}',
            content_type="application/json",
            headers={"content-type": "application/json"},
            elapsed_ms=1,
        )

    run = execute_recipe(
        "https://www.example.com/app?source_id=SKU42",
        recipe,
        fetcher=fetcher,
        resolve_dns=False,
    )
    assert run.status == "ok"
    assert run.structured_data["bindings"]["source_id"] == "SKU42"
    assert len(run.structured_data["items"]) == 3
    assert all("region=" in url for url in calls)
    assert all("source_id=SKU42" in url for url in calls)


def test_unresolved_placeholder_blocks_with_fallback() -> None:
    recipe = recipe_from_dict(
        {
            "id": "missing-binding",
            "version": "1",
            "network": {"mode": "open"},
            "match": {"host_roots": ["example.com"]},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/v1/item/{missing}",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    run = execute_recipe("https://www.example.com/page", recipe, resolve_dns=False)
    assert run.status == "error"
    assert any(
        "unresolved template placeholders" in w or "bindings could not be resolved" in w
        for w in run.warnings
    )
    payload = try_api_recipe("https://www.example.com/page", enabled=True, recipes=[recipe], resolve_dns=False)
    assert payload is None


def test_fixture_only_blocks_live_and_falls_back() -> None:
    recipe = recipe_from_dict(
        {
            "id": "fixture-demo",
            "version": "1",
            "network": {"mode": "fixture_only"},
            "match": {"host_roots": ["example.com"], "path_regex": "/x"},
            "endpoint": {
                "method": "GET",
                "url_template": "https://api.example.com/x",
                "allowed_hosts": ["api.example.com"],
            },
        }
    )
    payload = try_api_recipe("https://www.example.com/x", enabled=True, recipes=[recipe])
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is True
    assert payload.get("status") == "error"
