"""Unit, security, consent, and fallback tests for optional API recipes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from supersocks_url_scraper.api_recipes import (
    DEFAULT_CONSENT_PHRASE,
    FLASHSCORE_TOS_WARNING,
    execute_recipe,
    extract_event_id,
    live_network_permitted,
    load_builtin_recipes,
    normalize_prematch_1x2,
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

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
DEMO_URL = "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34"


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


def test_builtin_flashscore_recipe_is_consent_required() -> None:
    recipes = [r for r in load_builtin_recipes() if r.id == "flashscore-odds"]
    assert len(recipes) == 1
    assert recipes[0].network.mode == "consent_required"
    assert recipes[0].endpoint.method == "GET"
    assert "Authorization" not in recipes[0].endpoint.headers
    assert "Cookie" not in recipes[0].endpoint.headers
    assert "Referer" not in recipes[0].endpoint.headers


def test_extract_event_id_from_mid_query() -> None:
    assert extract_event_id(DEMO_URL) == "Ab12Cd34"
    assert extract_event_id("https://www.flashscore.fr/match/xyz/?mid=Zz99Aa11") == "Zz99Aa11"
    # extract_event_id is host-agnostic; matching gates hosts separately.
    assert extract_event_id("https://example.com/match/?mid=Ab12Cd34") == "Ab12Cd34"
    assert extract_event_id("https://www.flashscore.com/football/") is None

def test_normalize_prematch_1x2_from_fixture() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = normalize_prematch_1x2(raw["bookmakers"]["141"], bookmaker_id=141, bookmaker_name="Betclic")
    assert row is not None
    assert row["home"] == 2.1
    assert row["draw"] == 3.25
    assert row["away"] == 3.4
    assert row["opening"]["home"] == 2.05
    empty = normalize_prematch_1x2(raw["bookmakers"]["484"], bookmaker_id=484, bookmaker_name="ParionsSport")
    assert empty is None


def test_execute_flashscore_with_fixture_fetcher() -> None:
    recipe = next(r for r in load_builtin_recipes() if r.id == "flashscore-odds")
    run = execute_recipe(DEMO_URL, recipe, fetcher=_fixture_fetcher(), resolve_dns=False)
    assert run.status == "ok"
    assert run.fetch_method == "api-recipe"
    assert len(run.structured_data["bookmakers"]) >= 4
    assert "not betting advice" in run.summary.lower() or "not betting advice" in run.structured_data["disclaimer"].lower()
    md = to_markdown(run.as_reader_dict())
    assert "Structured odds" in md
    assert "not betting advice" in md.lower()


def test_live_network_blocked_without_consent() -> None:
    assert live_network_permitted("flashscore-odds", network_mode="fixture_only", env={}) is False
    assert (
        live_network_permitted(
            "flashscore-odds",
            network_mode="consent_required",
            env={"API_RECIPE_LIVE_ALLOWLIST": "flashscore-odds"},
        )
        is False
    )
    assert (
        consent_check(
            "flashscore-odds",
            network_mode="consent_required",
            env={
                "API_RECIPE_LIVE_ALLOWLIST": "flashscore-odds,other",
                "API_RECIPE_LIVE_CONSENT": DEFAULT_CONSENT_PHRASE,
            },
        )
        is True
    )


def test_try_api_recipe_flashscore_live_with_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorized consent path uses mocked safe_get — never contacts Flashscore live."""
    monkeypatch.setenv("API_RECIPE_LIVE_ALLOWLIST", "flashscore-odds")
    monkeypatch.setenv("API_RECIPE_LIVE_CONSENT", DEFAULT_CONSENT_PHRASE)
    monkeypatch.setattr(
        "supersocks_url_scraper.api_recipes.engine.safe_get",
        _fixture_fetcher(),
    )
    payload = try_api_recipe(DEMO_URL, enabled=True, resolve_dns=False)
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is not True
    assert payload.get("status") == "ok"
    assert payload.get("_api_recipe_live_network") is True


def test_try_api_recipe_flashscore_forces_fallback_when_live_blocked() -> None:
    payload = try_api_recipe(DEMO_URL, enabled=True)
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is True
    assert payload.get("status") == "error"
    joined = " ".join(payload.get("warnings") or [])
    assert "express consent" in joined.lower() or "Terms of Use" in joined


def test_read_url_falls_back_when_flashscore_live_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from supersocks_url_scraper.reader import FetchedResource

    safe_get_calls: list[str] = []

    def fake_safe_get(url: str, *args: Any, **kwargs: Any) -> Any:
        safe_get_calls.append(url)
        raise AssertionError("live safe_get must not be called without consent for flashscore")

    monkeypatch.setattr("supersocks_url_scraper.api_recipes.engine.safe_get", fake_safe_get)

    def fake_pipeline(url: str, **kwargs: Any) -> FetchedResource:
        html = (
            "<html><head><title>Demo match</title></head>"
            "<body><article><p>Synthetic match page used after API recipe fallback.</p></article></body></html>"
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
    assert safe_get_calls == []
    assert result.get("fetch_method") != "api-recipe"
    warnings = " ".join(result.get("warnings") or [])
    assert "api recipe degraded" in warnings.lower()
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


def test_generic_recipe_with_injected_fetcher() -> None:
    recipe = recipe_from_dict(
        {
            "id": "demo-json",
            "version": "1",
            "title": "Demo",
            "network": {"mode": "consent_required"},
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


def test_flashscore_tos_warning_constant() -> None:
    assert "flashscore.com/terms-of-use" in FLASHSCORE_TOS_WARNING
