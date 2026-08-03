from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from supersocks_url_scraper.reader import FetchedResource


def load_example() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples" / "generic_rtx_prices.py"
    spec = importlib.util.spec_from_file_location("generic_rtx_prices", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def rtx_example() -> ModuleType:
    return load_example()


def test_extract_embedded_json_from_configured_script_only(rtx_example: ModuleType) -> None:
    html = """
    <script id="UNRELATED">{"items":[{"id":"skip"}]}</script>
    <script type="application/json" id="RTX_PRICE_DATA">
      {"items":[{"id":"item-1","title":"Generic RTX 4070","price":"599 €","currency":"EUR"}]}
    </script>
    """

    data = rtx_example.extract_embedded_json(html)

    assert data["items"][0]["id"] == "item-1"


def test_parse_euro_price_handles_common_euro_formats(rtx_example: ModuleType) -> None:
    assert rtx_example.parse_euro_price("1 234,56 €") == 123456
    assert rtx_example.parse_euro_price("EUR 1,299.00") == 129900
    assert rtx_example.parse_euro_price("1.299 €") == 129900
    assert rtx_example.parse_euro_price("1299", "EUR") == 129900
    assert rtx_example.parse_euro_price([36000], price_unit="cents") == 36000
    assert rtx_example.parse_euro_price("1299", "USD") is None
    assert rtx_example.parse_euro_price("1299") is None


def test_filter_and_normalize_keeps_only_rtx_euro_listings(rtx_example: ModuleType) -> None:
    config = rtx_example.JsonConfig()
    kept = rtx_example.normalize_listing(
        {"id": "a-1", "title": "Synthetic GPU RTX 4080", "price": "1 099,90 €", "currency": "EUR", "url": "/listing/a-1"},
        config,
    )

    assert kept == {"id": "a-1", "title": "Synthetic GPU RTX 4080", "price_eur": 1099.9, "price_cents": 109990, "relative_url": "/listing/a-1"}
    assert rtx_example.normalize_listing({"id": "b-1", "title": "Synthetic GPU GTX 1080", "price": "99 €", "currency": "EUR"}, config) is None
    assert rtx_example.normalize_listing({"id": "c-1", "title": "Synthetic GPU RTX 4090", "price": "1200 USD", "currency": "USD"}, config) is None


def test_normalize_listing_supports_embedded_cents_shape(rtx_example: ModuleType) -> None:
    config = rtx_example.JsonConfig.from_mapping(
        {
            "script_id": "__NEXT_DATA__",
            "items_path": ["props", "pageProps", "searchData", "ads"],
            "id_field": "list_id",
            "title_field": "subject",
            "price_field": "price_cents",
            "price_unit": "cents",
            "url_field": "url",
        }
    )

    kept = rtx_example.normalize_listing(
        {"list_id": 42, "subject": "Carte graphique RTX 6000", "price_cents": 125000, "url": "https://marketplace.invalid/listing/42"},
        config,
    )

    assert kept == {"id": "42", "title": "Carte graphique RTX 6000", "price_eur": 1250.0, "price_cents": 125000}


def test_collect_rtx_prices_paginates_deduplicates_and_reports_partial(monkeypatch: pytest.MonkeyPatch, rtx_example: ModuleType) -> None:
    pages = {
        "https://marketplace.invalid/search?q=rtx&page=1": {
            "items": [
                {"id": "same", "title": "Generic RTX 4070", "price": "599 €", "currency": "EUR"},
                {"id": "gtx", "title": "Generic GTX 1080", "price": "99 €", "currency": "EUR"},
            ]
        },
        "https://marketplace.invalid/search?q=rtx&page=2": {
            "items": [
                {"id": "same", "title": "Generic RTX 4070 duplicate", "price": "589 €", "currency": "EUR"},
                {"id": "new", "title": "Generic RTX 4090", "price": "1.899,99 €", "currency": "EUR"},
            ]
        },
    }
    calls: list[dict[str, object]] = []

    def fake_fetch_listing_markup(url: str, **kwargs: object) -> tuple[FetchedResource | None, list[str]]:
        calls.append({"url": url, **kwargs})
        if url.endswith("page=3"):
            return FetchedResource(url, url, 200, b"<html></html>", "text/html; charset=utf-8", {"x-fetch-method": "cloak"}), ["synthetic missing embedded JSON"]
        data = json.dumps(pages[url])
        html = f'<script id="RTX_PRICE_DATA" type="application/json">{data}</script>'.encode("utf-8")
        return FetchedResource(url, url, 200, html, "text/html; charset=utf-8", {"x-fetch-method": "http"}), []

    monkeypatch.setattr(rtx_example, "fetch_listing_markup", fake_fetch_listing_markup)
    monkeypatch.setattr(rtx_example.time, "sleep", lambda _seconds: None)

    result = rtx_example.collect_rtx_prices("https://marketplace.invalid/search?q=rtx&page={page}", max_pages=3, delay_seconds=0.01)

    assert result["status"] == "partial"
    assert result["count"] == 2
    assert [item["id"] for item in result["listings"]] == ["same", "new"]
    assert [call["url"] for call in calls] == [
        "https://marketplace.invalid/search?q=rtx&page=1",
        "https://marketplace.invalid/search?q=rtx&page=2",
        "https://marketplace.invalid/search?q=rtx&page=3",
    ]
    assert all(call["browser_fallback"] is True for call in calls)
    assert any("embedded JSON extraction failed" in warning for warning in result["warnings"])


def test_collect_rtx_prices_bounds_pagination(rtx_example: ModuleType) -> None:
    with pytest.raises(ValueError, match="max_pages"):
        rtx_example.collect_rtx_prices("https://marketplace.invalid/search?page={page}", max_pages=101)


def test_is_access_challenge_ignores_embedded_forbidden_key(rtx_example: ModuleType) -> None:
    # Ordinary search markup can include i18n keys like deletion-forbidden.
    ordinary = (
        '<html><body><script id="RTX_PRICE_DATA">'
        '{"global":{"deletion-forbidden":{"error":"not allowed"}},'
        '"items":[{"id":"1","title":"Generic RTX 4070","price":"599 €","currency":"EUR"}]}'
        "</script></body></html>"
    )
    assert rtx_example.is_access_challenge(ordinary) is False
    interstitial = (
        '<html><head><title>Access challenge</title></head>'
        '<body><script src="https://geo.captcha-delivery.com/i.js"></script>captcha</body></html>'
    )
    assert rtx_example.is_access_challenge(interstitial) is True


def test_collect_stops_on_access_challenge(monkeypatch: pytest.MonkeyPatch, rtx_example: ModuleType) -> None:
    calls: list[str] = []

    def fake_fetch_listing_markup(url: str, **kwargs: object) -> tuple[FetchedResource | None, list[str]]:
        calls.append(url)
        if url.endswith("page=2"):
            html = b"<html><title>Access challenge</title><body>captcha</body></html>"
        else:
            html = b'<script id="RTX_PRICE_DATA">{"items":[{"id":"one","title":"Generic RTX 4070","price":"599 EUR"}]}</script>'
        return FetchedResource(url, url, 200, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}), []

    monkeypatch.setattr(rtx_example, "fetch_listing_markup", fake_fetch_listing_markup)
    monkeypatch.setattr(rtx_example.time, "sleep", lambda _seconds: None)

    result = rtx_example.collect_rtx_prices("https://marketplace.invalid/search?page={page}", max_pages=3, delay_seconds=0)

    assert result["status"] == "partial"
    assert result["count"] == 1
    assert result["pages"][-1]["status"] == "blocked"
    assert len(calls) == 2
    assert any("access challenge detected" in warning for warning in result["warnings"])
