from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from supersocks_url_scraper.reader import FetchedResource


def load_example() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples" / "generic_cardmarket_blue_eyes.py"
    spec = importlib.util.spec_from_file_location("generic_cardmarket_blue_eyes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cm_example() -> ModuleType:
    return load_example()


SYNTHETIC_OFFER_HTML = """
<html><body>
<div id="articleRow1001" class="row g-0 article-row">
  <div class="col-sellerProductInfo col">
    <div class="col-seller"><a href="/en/YuGiOh/Users/seller-one">seller-one</a></div>
    <div class="col-product col-12 col-lg">
      <div class="product-attributes col">
        <a class="expansion-symbol is-yugioh is-text yugiohExpansionIcon" data-bs-original-title="Rarity Collection 5"><span>RA05</span></a>
        <svg aria-label="Ultra Rare" data-bs-original-title="Ultra Rare"></svg>
        <a class="article-condition condition-nm" data-bs-original-title="Near Mint"><span>NM</span></a>
        <span class="icon" data-bs-original-title="English" aria-label="English"></span>
        <span class="icon" data-bs-original-title="First Edition" aria-label="First Edition"></span>
      </div>
      <div class="price-container d-none d-md-flex"><span>1,99 €</span></div>
    </div>
  </div>
</div>
<div id="articleRow1002" class="row g-0 article-row">
  <div class="col-sellerProductInfo col">
    <div class="col-seller"><a href="/en/YuGiOh/Users/seller-two">seller-two</a></div>
    <div class="col-product col-12 col-lg">
      <div class="product-attributes col">
        <a class="expansion-symbol is-yugioh is-text yugiohExpansionIcon" data-bs-original-title="Rarity Collection 5"><span>RA05</span></a>
        <svg aria-label="Ultra Rare" data-bs-original-title="Ultra Rare"></svg>
        <a class="article-condition condition-ex" data-bs-original-title="Excellent"><span>EX</span></a>
        <span class="icon" data-bs-original-title="French" aria-label="French"></span>
        <span class="icon" data-bs-original-title="First Edition" aria-label="First Edition"></span>
      </div>
      <div class="price-container d-none d-md-flex"><span>2,50 €</span></div>
    </div>
  </div>
</div>
</body></html>
"""

SYNTHETIC_VERSIONS_HTML = """
<html><body>
<a href="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare">
  <p class="card-text text-muted">Version 1 100 Available</p>
  <p class="card-text text-muted">From <b>0,99 €</b></p>
</a>
<a href="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V2-Starlight-Rare">
  <p class="card-text text-muted">Version 2 12 Available</p>
  <p class="card-text text-muted">From <b>69,99 €</b></p>
</a>
<a href="/en/YuGiOh/Products/Singles/Other-Set/Dark-Magician">
  <p class="card-text text-muted">From <b>1,00 €</b></p>
</a>
</body></html>
"""


def test_parse_euro_to_cents_handles_common_formats(cm_example: ModuleType) -> None:
    assert cm_example.parse_euro_to_cents("1,99 €") == 199
    assert cm_example.parse_euro_to_cents("1.299,00 €") == 129900
    assert cm_example.parse_euro_to_cents("EUR 12.50") == 1250
    assert cm_example.parse_euro_to_cents("12,50") == 1250
    assert cm_example.parse_euro_to_cents("not-a-price") is None


def test_parse_offer_rows_redacts_sellers_and_keeps_attributes(cm_example: ModuleType) -> None:
    rows = cm_example.parse_offer_rows(
        SYNTHETIC_OFFER_HTML,
        source_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare",
    )
    assert len(rows) == 2
    assert {row["price_cents"] for row in rows} == {199, 250}
    first = next(row for row in rows if row["price_cents"] == 199)
    assert first["condition"] == "Near Mint"
    assert first["language"] == "English"
    assert first["rarity"] == "Ultra Rare"
    assert first["edition"] == "First Edition"
    assert first["expansion_code"] == "RA05"
    assert first["graded"] is False
    blob = json_dumps(rows)
    assert "seller-one" not in blob
    assert "seller-two" not in blob


def json_dumps(rows: list[dict]) -> str:
    import json

    return json.dumps(rows)


def test_parse_version_floors_filters_blue_eyes_only(cm_example: ModuleType) -> None:
    rows = cm_example.parse_version_floor_cards(
        SYNTHETIC_VERSIONS_HTML,
        source_url="https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
    )
    assert len(rows) == 2
    assert rows[0]["record_kind"] == "version_floor"
    assert rows[0]["rarity"] == "Ultra Rare"
    assert rows[1]["rarity"] == "Starlight Rare"
    assert rows[0]["price_eur"] == 0.99
    assert rows[1]["available_count"] == 12


def test_populations_are_not_silently_merged(cm_example: ModuleType) -> None:
    floors = cm_example.parse_version_floor_cards(
        SYNTHETIC_VERSIONS_HTML,
        source_url="https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
    )
    source_a = "https://www.cardmarket.com/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon"
    source_b = "https://www.cardmarket.com/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common"
    offers_a = cm_example.parse_offer_rows(SYNTHETIC_OFFER_HTML, source_url=source_a)
    cheap_html = (
        SYNTHETIC_OFFER_HTML.replace("articleRow1001", "articleRow2001")
        .replace("articleRow1002", "articleRow2002")
        .replace("1,99 €", "0,02 €")
        .replace("2,50 €", "0,05 €")
    )
    offers_b = cm_example.parse_offer_rows(cheap_html, source_url=source_b)
    assert len(offers_a) == 2
    assert len(offers_b) == 2
    summary = cm_example.summarize_populations(floors, offers_a + offers_b)
    assert summary["version_floors"]["n"] == 2
    assert summary["version_floors"]["stats"]["median"] == 35.49
    assert set(summary["offers_by_source"]) == {source_a, source_b}
    assert summary["offers_by_source"][source_a]["page_level_stats"]["median"] == 2.245
    assert summary["offers_by_source"][source_b]["page_level_stats"]["median"] == 0.035
    # Mixing all offer prices would hide the common-vs-ultra gap; keep sources apart.
    mixed_median = cm_example.price_quartiles(
        [row["price_eur"] for row in offers_a + offers_b]
    )["median"]
    assert mixed_median != summary["offers_by_source"][source_a]["page_level_stats"]["median"]


def test_price_quartiles(cm_example: ModuleType) -> None:
    stats = cm_example.price_quartiles([1.0, 2.0, 3.0, 4.0])
    assert stats["n"] == 4
    assert stats["min"] == 1.0
    assert stats["median"] == 2.5
    assert stats["max"] == 4.0
    assert cm_example.price_quartiles([]) == {"n": 0}


def test_is_access_challenge_ignores_passive_cf_jsd_when_offers_present(cm_example: ModuleType) -> None:
    ordinary = (
        '<html><body><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
        + SYNTHETIC_OFFER_HTML
        + "</body></html>"
    )
    assert cm_example.is_access_challenge(ordinary) is False
    interstitial = (
        '<html><head><title>Attention Required</title></head>'
        '<body>Please verify you are a human captcha</body></html>'
    )
    assert cm_example.is_access_challenge(interstitial) is True


def test_collect_stops_on_access_challenge(monkeypatch: pytest.MonkeyPatch, cm_example: ModuleType) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, **kwargs: object) -> tuple[FetchedResource | None, list[str]]:
        calls.append(url)
        if "Versions" in url:
            html = SYNTHETIC_VERSIONS_HTML.encode()
        else:
            html = b"<html><body>Please verify you are a human captcha</body></html>"
        return FetchedResource(url, url, 200, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}), []

    monkeypatch.setattr(cm_example, "fetch_listing_markup", fake_fetch)
    monkeypatch.setattr(cm_example.time, "sleep", lambda _seconds: None)

    result = cm_example.collect_cardmarket_blue_eyes(
        [
            "https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
            "https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon",
            "https://www.cardmarket.com/en/YuGiOh/Products/Singles/X/Blue-Eyes-White-Dragon",
        ],
        delay_seconds=0,
    )

    assert result["status"] == "partial"
    assert result["pages"][-1]["status"] == "blocked"
    assert len(calls) == 2
    assert result["count_net"] == 2
    assert any("access challenge detected" in warning for warning in result["warnings"])


def test_collect_bounds_url_count(cm_example: ModuleType) -> None:
    with pytest.raises(ValueError, match="url count"):
        cm_example.collect_cardmarket_blue_eyes(["https://example.invalid"] * 21)
