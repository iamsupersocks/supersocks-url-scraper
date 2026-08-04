from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

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


def test_price_quartiles_cents_median_rounds_half_up_between_zero_and_one(
    cm_example: ModuleType,
) -> None:
    stats = cm_example.price_quartiles_cents([0, 1])
    assert stats["median_cents"] == 1
    assert stats["median"] == 0.01


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


def _synthetic_floors(cm_example: ModuleType) -> list[dict]:
    return cm_example.parse_version_floor_cards(
        SYNTHETIC_VERSIONS_HTML
        + """
<a href="/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon-V1-Ultra-Rare">
  <p class="card-text text-muted">Version 1 8 Available</p>
  <p class="card-text text-muted">From <b>4,56 €</b></p>
</a>
<a href="/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon-V2-Secret-Rare">
  <p class="card-text text-muted">Version 2 3 Available</p>
  <p class="card-text text-muted">From <b>50,00 €</b></p>
</a>
""",
        source_url="https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
    )


def test_aggregate_version_floors_by_expansion_groups_sets(cm_example: ModuleType) -> None:
    floors = _synthetic_floors(cm_example)
    assert len(floors) == 4
    groups = cm_example.aggregate_version_floors_by_expansion(floors)
    by_name = {row["expansion"]: row for row in groups}
    assert by_name["Rarity Collection 5"]["n"] == 2
    assert by_name["Rarity Collection 5"]["min"] == 0.99
    assert by_name["Rarity Collection 5"]["max"] == 69.99
    assert by_name["Legend of Blue Eyes White Dragon"]["n"] == 2
    assert "Ultra Rare" in by_name["Legend of Blue Eyes White Dragon"]["rarities"]
    assert "Secret Rare" in by_name["Legend of Blue Eyes White Dragon"]["rarities"]
    # Stable sort: higher n first, then higher median, then name.
    assert [row["expansion"] for row in groups] == sorted(
        [row["expansion"] for row in groups],
        key=lambda name: (
            -by_name[name]["n"],
            -int(by_name[name]["median_cents"]),
            name.lower(),
        ),
    )


def test_export_version_floor_references_csv_is_deterministic_and_sanitized(
    cm_example: ModuleType, tmp_path: Path
) -> None:
    floors = _synthetic_floors(cm_example)
    shuffled = list(reversed(floors))
    csv_a = cm_example.export_version_floor_references_csv(floors, source_date="2026-08-04")
    csv_b = cm_example.export_version_floor_references_csv(shuffled, source_date="2026-08-04")
    assert csv_a == csv_b
    lines = csv_a.strip().splitlines()
    assert lines[0] == ",".join(cm_example.VERSION_FLOOR_CSV_COLUMNS)
    assert len(lines) == 1 + len(floors)
    assert "seller" not in csv_a.lower()
    assert "article_id" not in csv_a.lower()
    assert "cookie" not in csv_a.lower()
    assert "<html" not in csv_a.lower()
    # No offer population mixed into the reference export.
    assert all(row["record_kind"] == "version_floor" for row in floors)
    path = tmp_path / "refs.csv"
    cm_example.export_version_floor_references_csv(
        floors, source_date="2026-08-04", destination=path
    )
    assert path.read_text(encoding="utf-8") == csv_a
    # EUR comes from integer cents via Decimal, not fragile float formatting.
    data_line = lines[1]
    assert ",0.99,99," in data_line or data_line.endswith(",0.99,99,") or ",0.99,99," in csv_a


def test_export_does_not_include_forbidden_columns(cm_example: ModuleType) -> None:
    for column in cm_example.VERSION_FLOOR_CSV_COLUMNS:
        assert column not in cm_example.FORBIDDEN_REFERENCE_CSV_COLUMNS
    floors = _synthetic_floors(cm_example)
    text = cm_example.export_version_floor_references_csv(floors, source_date="2026-08-04")
    header = text.splitlines()[0].split(",")
    assert header == list(cm_example.VERSION_FLOOR_CSV_COLUMNS)
    assert "id" not in header
    assert "article_id_hash" not in header


def test_from_json_cli_exports_csv_without_live_fetch(
    monkeypatch: pytest.MonkeyPatch, cm_example: ModuleType, tmp_path: Path
) -> None:
    floors = _synthetic_floors(cm_example)
    payload_path = tmp_path / "payload.json"
    out_csv = tmp_path / "out.csv"
    payload_path.write_text(
        json_dumps({"status": "ok", "version_floors": floors, "offers": []}),
        encoding="utf-8",
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live collect must not run in --from-json mode")

    monkeypatch.setattr(cm_example, "collect_cardmarket_blue_eyes", boom)
    code = cm_example.main(
        [
            "--from-json",
            str(payload_path),
            "--export-references-csv",
            str(out_csv),
            "--source-date",
            "2026-08-04",
            "--quiet-json",
        ]
    )
    assert code == 0
    lines = out_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 + len(floors)


SYNTHETIC_NA_VERSIONS_HTML = """
<html><body>
<a href="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare">
  <p class="card-text text-muted">Version 1 100 Available</p>
  <p class="card-text text-muted">From <b>0,99 €</b></p>
</a>
<a href="/en/YuGiOh/Products/Singles/Promos-OCG/Blue-Eyes-White-Dragon">
  <p class="card-text text-muted">0 Available</p>
  <p class="card-text text-muted">From N/A</p>
</a>
<a href="/en/YuGiOh/Products/Singles/Other/Blue-Eyes-Alternative-White-Dragon">
  <p class="card-text text-muted">From <b>1,00 €</b></p>
</a>
</body></html>
"""

SYNTHETIC_SEARCH_PAGE_HTML = """
<html><body>
<p>Page 1 of 2</p>
<a href="/en/YuGiOh/Products/Search?searchString=Blue-Eyes+White+Dragon&amp;site=2">next</a>
<a href="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare">BEWD</a>
<a href="/en/YuGiOh/Products/Singles/Promos-OCG/Blue-Eyes-White-Dragon">BEWD promo</a>
<a href="/en/YuGiOh/Products/Singles/Other/Blue-Eyes-Alternative-White-Dragon">alt</a>
</body></html>
"""

SYNTHETIC_SEARCH_PAGE2_HTML = """
<html><body>
<p>Page 2 of 2</p>
<a href="/en/YuGiOh/Products/Singles/Structure-Deck-Blue-Eyes-White-Destiny/Blue-Eyes-White-Dragon-V1-Common">common</a>
</body></html>
"""

SYNTHETIC_PRODUCT_DETAIL_HTML = """
<html><body>
<h1>Blue-Eyes White Dragon (V.1 - Ultra Rare) Rarity Collection 5 - Singles</h1>
<div class="product-attributes">meta zone</div>
<div id="articleRow1" class="row g-0 article-row">
  <div class="col-seller"><a href="/en/YuGiOh/Users/seller-hidden">seller-hidden</a></div>
  <div class="col-product">
    <span data-bs-original-title="English" aria-label="English"></span>
    <span data-bs-original-title="First Edition" aria-label="First Edition"></span>
    <div class="price-container"><span>1,99 €</span></div>
  </div>
</div>
</body></html>
"""


def test_version_refs_include_na_and_exclude_related_cards(cm_example: ModuleType) -> None:
    refs = cm_example.parse_version_product_refs(
        SYNTHETIC_NA_VERSIONS_HTML,
        source_url="https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
    )
    assert len(refs) == 2
    by_path = {row["product_path"]: row for row in refs}
    assert by_path["/en/YuGiOh/Products/Singles/Promos-OCG/Blue-Eyes-White-Dragon"]["from_status"] == "na"
    assert by_path["/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare"]["from_status"] == "priced"
    assert cm_example.is_exact_blue_eyes_white_dragon_path(
        "/en/YuGiOh/Products/Singles/Other/Blue-Eyes-Alternative-White-Dragon"
    ) is False
    assert cm_example.is_exact_blue_eyes_white_dragon_path(
        "/en/YuGiOh/Products/Singles/Limit-Over-Collection-The-Rivals/"
        "Blue-Eyes-White-Dragon-the-White-Phantom-Beast-V1-Ultra-Rare"
    ) is False
    assert cm_example.is_exact_blue_eyes_white_dragon_path(
        "/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare"
    ) is True


def test_search_pagination_helpers_and_stop_conditions(cm_example: ModuleType) -> None:
    url = cm_example.build_search_page_url("Blue-Eyes White Dragon", 3)
    assert "site=3" in url
    assert "searchString=Blue-Eyes" in url
    page1 = cm_example.parse_search_results_page(
        SYNTHETIC_SEARCH_PAGE_HTML,
        source_url=cm_example.build_search_page_url("Blue-Eyes White Dragon", 1),
    )
    assert page1["page_of"] == {"current": 1, "total": 2}
    assert len(page1["product_paths_exact"]) == 2
    assert page1["related_non_exact"]
    assert cm_example.should_stop_search_pagination(
        parsed=page1, previous_paths=None, site=1
    ) is None
    page1_repeat = dict(page1)
    assert (
        cm_example.should_stop_search_pagination(
            parsed=page1_repeat,
            previous_paths=set(page1["product_paths_all"]),
            site=1,
        )
        == "search_page_repeated"
    )
    empty = cm_example.parse_search_results_page(
        "<html><body>Page 3 of 2</body></html>",
        source_url=cm_example.build_search_page_url("Blue-Eyes White Dragon", 3),
    )
    assert empty["empty"] is True
    assert (
        cm_example.should_stop_search_pagination(parsed=empty, previous_paths=set(), site=3)
        == "search_page_empty"
    )


def test_dedupe_by_product_path_and_compare_prior_corpus(cm_example: ModuleType) -> None:
    rows = [
        {"product_path": "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon", "id": "1"},
        {"product_path": "/fr/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon", "id": "2"},
        {"product_path": "/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common", "id": "3"},
    ]
    deduped = cm_example.dedupe_by_product_path(rows)
    assert len(deduped) == 2
    unique, dupes = cm_example.count_path_duplicates(
        [
            "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon",
            "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon",
            "/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common",
        ]
    )
    assert unique == [
        "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon",
        "/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common",
    ]
    assert dupes == 1
    comparison = cm_example.compare_coverage_to_prior_corpus(
        unique,
        prior_paths=["/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon"],
        prior_expansion_count=1,
    )
    assert comparison["prior_unique_paths"] == 1
    assert comparison["current_unique_paths"] == 2
    assert comparison["overlap"] == 1
    assert comparison["new_vs_prior"] == [
        "/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common"
    ]


def test_product_public_details_ignore_seller_and_offer_language(
    cm_example: ModuleType,
) -> None:
    detail = cm_example.parse_product_public_details(
        SYNTHETIC_PRODUCT_DETAIL_HTML,
        product_path="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare",
        source_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare",
    )
    assert detail["version"] == "V1"
    assert detail["rarity"] == "Ultra Rare"
    assert detail["expansion"] == "Rarity Collection 5"
    assert detail["printed_code"] is None
    # Offer-row English must not become a product-level language claim.
    assert detail["language"] is None
    blob = json_dumps(detail)
    assert "seller-hidden" not in blob


def test_coverage_csv_sanitization_and_manifest(cm_example: ModuleType, tmp_path: Path) -> None:
    refs = cm_example.parse_version_product_refs(
        SYNTHETIC_NA_VERSIONS_HTML,
        source_url="https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions",
    )
    details = [
        cm_example.parse_product_public_details(
            SYNTHETIC_PRODUCT_DETAIL_HTML,
            product_path="/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare",
            source_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare",
        )
    ]
    rows = cm_example.build_coverage_rows(
        version_refs=refs,
        search_paths=["/en/YuGiOh/Products/Singles/Promos-OCG/Blue-Eyes-White-Dragon"],
        product_details=details,
        source_date="2026-08-04",
    )
    csv_text = cm_example.export_coverage_csv(rows, destination=tmp_path / "coverage.csv")
    assert "seller" not in csv_text.lower()
    assert "cookie" not in csv_text.lower()
    assert "article_id" not in csv_text.lower()
    assert csv_text.splitlines()[0] == ",".join(cm_example.COVERAGE_CSV_COLUMNS)
    comparison = cm_example.compare_coverage_to_prior_corpus(
        [row["public_product_path"] for row in rows],
        prior_paths=["/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare"],
        prior_expansion_count=1,
    )
    manifest = cm_example.build_coverage_manifest(
        ledger={
            "started_at": "2026-08-04T00:00:00+00:00",
            "timezone": "UTC",
            "pages": [{"url": "u", "status": "ok"}],
            "stop_reason": "search_announced_total_reached",
            "announced_versions": 2,
            "errors": [],
        },
        coverage_rows=rows,
        comparison=comparison,
    )
    assert manifest["unique_public_product_paths"] == 2
    manifest_blob = json_dumps(manifest).lower()
    assert "seller-hidden" not in manifest_blob
    assert "username" not in manifest_blob
    assert manifest["privacy"]["seller_fields"] == "never collected/published"
    indicators = manifest["completion_indicators"]
    assert indicators["versions_counter_reached"] is True
    assert indicators["search_pagination_complete"] is False
    assert indicators["product_details_complete"] is False
    scope = manifest["proven_coverage_scope"]
    assert "177/177" in scope["versions_panel"] or "2/2" in scope["versions_panel"]
    md = cm_example.render_coverage_manifest_markdown(manifest)
    assert "Stop reason" in md
    assert "Completion indicators" in md
    assert "not Versions + Search exhaustive" in md
    assert "publicly observable Cardmarket Versions + Search product paths" not in md
    assert "claim exhaustivity of" not in manifest_blob


def test_published_coverage_manifest_qualifies_exhaustivity(
    cm_example: ModuleType,
) -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "docs/data/cardmarket-blue-eyes-coverage-manifest-2026-08-04.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    indicators = manifest["completion_indicators"]
    assert indicators == {
        "versions_counter_reached": True,
        "search_pagination_complete": False,
        "product_details_complete": False,
    }
    scope = manifest["proven_coverage_scope"]
    assert scope["versions_panel"] == "public Versions panel complete at observed counter 177/177"
    assert "site=1–7" in scope["search_pagination"]
    assert "hard challenges" in scope["search_pagination"]
    assert scope["product_details"] == "product details and printed codes incomplete"
    md = cm_example.render_coverage_manifest_markdown(manifest)
    assert "not Versions + Search exhaustive" in md
    blob = json_dumps(manifest).lower()
    assert "publicly observable cardmarket versions + search product paths" not in blob


def test_compute_coverage_completion_indicators(cm_example: ModuleType) -> None:
    rows = [
        {"detail_attempted": "yes", "detail_ok": "yes", "printed_code": "LOB-001"},
        {"detail_attempted": "no", "detail_ok": "", "printed_code": ""},
    ]
    partial = cm_example.compute_coverage_completion_indicators(
        ledger={"announced_versions": 177, "search_pagination_complete": False},
        coverage_rows=rows,
    )
    assert partial["versions_counter_reached"] is False
    assert partial["search_pagination_complete"] is False
    assert partial["product_details_complete"] is False

    over_rows = [
        {"detail_attempted": "yes", "detail_ok": "yes", "printed_code": "LOB-001"}
        for _ in range(178)
    ]
    over = cm_example.compute_coverage_completion_indicators(
        ledger={"announced_versions": 177, "search_pagination_complete": False},
        coverage_rows=over_rows,
    )
    assert over["versions_counter_reached"] is False

    complete_rows = [
        {"detail_attempted": "yes", "detail_ok": "yes", "printed_code": "LOB-001"}
        for _ in range(177)
    ]
    complete = cm_example.compute_coverage_completion_indicators(
        ledger={"announced_versions": 177, "search_pagination_complete": True},
        coverage_rows=complete_rows,
    )
    assert complete["versions_counter_reached"] is True
    assert complete["search_pagination_complete"] is True
    assert complete["product_details_complete"] is True


def test_coverage_crawl_pagination_stop_resume_and_challenge(
    monkeypatch: pytest.MonkeyPatch, cm_example: ModuleType
) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, **kwargs: object) -> tuple[FetchedResource | None, list[str]]:
        calls.append(url)
        if "/Versions" in url and "/fr/" not in url:
            html = (
                "<html><body><h1>177 Versions</h1>" + SYNTHETIC_NA_VERSIONS_HTML + "</body></html>"
            ).encode()
        elif "/fr/" in url and "/Versions" in url:
            html = (
                "<html><body><h1>177 Versions</h1>" + SYNTHETIC_NA_VERSIONS_HTML + "</body></html>"
            ).encode()
        elif "site=1" in url:
            html = SYNTHETIC_SEARCH_PAGE_HTML.encode()
        elif "site=2" in url:
            html = SYNTHETIC_SEARCH_PAGE2_HTML.encode()
        elif "Products/Singles" in url:
            html = SYNTHETIC_PRODUCT_DETAIL_HTML.encode()
        else:
            html = b"<html><body>empty</body></html>"
        return (
            FetchedResource(url, url, 200, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}),
            [],
        )

    monkeypatch.setattr(cm_example, "fetch_listing_markup", fake_fetch)
    monkeypatch.setattr(cm_example.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cm_example.random, "uniform", lambda _a, _b: 0.0)

    result = cm_example.crawl_public_blue_eyes_coverage(
        delay_seconds=2.0,
        max_navigations=20,
        include_fr_versions=False,
        fetch_product_details=True,
    )
    assert result["announced_versions"] == 177
    assert len(result["version_refs"]) == 2
    assert "search_announced_total_reached" in (result["stop_reason"] or "") or result[
        "stop_reason"
    ].startswith("announced_versions") or "search_pagination_complete" in (result["stop_reason"] or "")
    assert result["unique_product_paths"]
    # Resume should skip already attempted URLs.
    resumed_calls_before = len(calls)
    resumed = cm_example.crawl_public_blue_eyes_coverage(
        delay_seconds=2.0,
        max_navigations=20,
        include_fr_versions=False,
        fetch_product_details=False,
        resume_from=result,
    )
    assert "resuming from prior coverage ledger" in resumed["warnings"]
    assert len(calls) == resumed_calls_before  # no new navigations when details disabled and routes done

    # Hard challenge stop after 3 consecutive blocks.
    challenge_calls: list[str] = []

    def always_challenge(url: str, **kwargs: object) -> tuple[FetchedResource | None, list[str]]:
        challenge_calls.append(url)
        html = b"<html><body>Please verify you are a human captcha</body></html>"
        return FetchedResource(url, url, 403, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}), []

    monkeypatch.setattr(cm_example, "fetch_listing_markup", always_challenge)
    blocked = cm_example.crawl_public_blue_eyes_coverage(
        delay_seconds=2.0,
        max_navigations=10,
        include_fr_versions=True,
        fetch_product_details=False,
    )
    assert blocked["stop_reason"] == "hard_challenges_x3"
    assert len(challenge_calls) == 3


def test_coverage_budget_and_min_delay_guards(cm_example: ModuleType) -> None:
    with pytest.raises(ValueError, match="delay_seconds"):
        cm_example.crawl_public_blue_eyes_coverage(delay_seconds=1.0, max_navigations=5)
    with pytest.raises(ValueError, match="max_navigations"):
        cm_example.crawl_public_blue_eyes_coverage(delay_seconds=2.0, max_navigations=999)


SYNTHETIC_PRODUCT_METRICS_HTML = """
<html><body>
<h1>Blue-Eyes White Dragon (V.1 - Ultra Rare) Legend of Blue Eyes White Dragon - Singles</h1>
<div class="info">
  Number LOB-001
  Available items 42
  From 1,99 €
  Price Trend 2,50 €
  30-days average price 2,40 €
  7-days average price 2,55 €
  1-day average price 2,60 €
</div>
<div id="articleRow1" class="row g-0 article-row">
  <div class="col-seller"><a href="/en/YuGiOh/Users/seller-hidden">seller-hidden</a></div>
  <div class="col-product">
    <span data-bs-original-title="English" aria-label="English"></span>
    <span data-bs-original-title="Near Mint" aria-label="Near Mint"></span>
    <div class="price-container"><span>1,99 €</span></div>
  </div>
</div>
<div id="articleRow2" class="row g-0 article-row">
  <div class="col-seller"><a href="/en/YuGiOh/Users/seller-two">seller-two</a></div>
  <div class="col-product">
    <span data-bs-original-title="French" aria-label="French"></span>
    <span data-bs-original-title="Near Mint" aria-label="Near Mint"></span>
    <div class="price-container"><span>2,10 €</span></div>
  </div>
</div>
</body></html>
"""


def _write_corpus_csv(cm_example: ModuleType, path: Path, paths: list[str]) -> None:
    rows = [
        {
            "public_product_path": p,
            "canonical_url": cm_example.canonical_product_url(p),
            "expansion": p.strip("/").split("/")[4].replace("-", " "),
            "product_label": "Blue Eyes White Dragon",
            "version": "",
            "rarity": "",
            "edition": "",
            "language": "",
            "finish": "",
            "printed_code": "",
            "from_status": "priced",
            "from_cents": "100",
            "from_eur": "1.00",
            "available_count": "10",
            "discovered_via": "versions",
            "detail_attempted": "no",
            "detail_ok": "",
            "fields_present": "",
            "fields_absent": "",
            "source_date": "2026-08-04",
        }
        for p in paths
    ]
    cm_example.export_coverage_csv(rows, destination=path)


def test_parse_product_public_metrics_and_no_invented_collector(
    cm_example: ModuleType,
) -> None:
    detail = cm_example.parse_product_public_details(
        SYNTHETIC_PRODUCT_METRICS_HTML,
        product_path="/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon",
        source_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon",
    )
    assert detail["printed_code"] == "LOB-001"
    assert detail["from_cents"] == 199
    assert detail["available_count"] == 42
    assert detail["price_trend_cents"] == 250
    assert detail["avg_30d_cents"] == 240
    assert detail["avg_7d_cents"] == 255
    assert detail["avg_1d_cents"] == 260
    assert detail["language_counts"] == {"English": 1, "French": 1}
    assert detail["condition_counts"] == {"Near Mint": 2}
    blob = json_dumps(detail)
    assert "seller-hidden" not in blob
    assert "seller-two" not in blob
    assert "offers" not in detail

    # Expansion abbreviation alone must not invent a collector code.
    bare = cm_example.parse_product_public_details(
        "<html><body><h1>Blue-Eyes White Dragon Ultra Rare</h1></body></html>",
        product_path="/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon",
        source_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon/Blue-Eyes-White-Dragon",
    )
    assert bare["printed_code"] is None


def test_deep_queue_order_overcount_checkpoint_resume_and_challenge_stop(
    monkeypatch: pytest.MonkeyPatch, cm_example: ModuleType, tmp_path: Path
) -> None:
    repo_csv = (
        Path(__file__).resolve().parents[1]
        / "docs/data/cardmarket-blue-eyes-coverage-2026-08-04.csv"
    )
    paths = cm_example.load_exact_blue_eyes_paths_from_coverage_csv(repo_csv)
    assert len(paths) == 177
    assert paths == sorted(paths, key=str.lower)
    assert all(cm_example.is_exact_blue_eyes_white_dragon_path(p) for p in paths)
    assert not any("Phantom-Beast" in p for p in paths)

    with pytest.raises(ValueError, match="177"):
        cm_example.seed_deep_enrichment_queue(paths + [paths[0] + "-extra"])

    over_csv = tmp_path / "over.csv"
    _write_corpus_csv(cm_example, over_csv, paths + [
        "/en/YuGiOh/Products/Singles/Extra-Set/Blue-Eyes-White-Dragon-V9-Common"
    ])
    with pytest.raises(ValueError, match="177"):
        cm_example.load_exact_blue_eyes_paths_from_coverage_csv(over_csv)

    wpb_csv = tmp_path / "wpb.csv"
    bad_paths = paths[:-1] + [
        "/en/YuGiOh/Products/Singles/Limit-Over-Collection-The-Rivals/"
        "Blue-Eyes-White-Dragon-the-White-Phantom-Beast-V1-Ultra-Rare"
    ]
    # Bypass export helper validation by writing raw CSV with coverage columns.
    with wpb_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cm_example.COVERAGE_CSV_COLUMNS))
        writer.writeheader()
        for p in bad_paths:
            writer.writerow({col: "" for col in cm_example.COVERAGE_CSV_COLUMNS} | {
                "public_product_path": p,
                "canonical_url": cm_example.canonical_product_url(p),
                "source_date": "2026-08-04",
            })
    with pytest.raises(ValueError, match="non-exact"):
        cm_example.load_exact_blue_eyes_paths_from_coverage_csv(wpb_csv)

    corpus_csv = tmp_path / "corpus.csv"
    _write_corpus_csv(cm_example, corpus_csv, paths)
    checkpoint = tmp_path / "ledger.json"

    calls: list[str] = []

    def fake_fetch(url: str, **kwargs: object) -> tuple[FetchedResource, list[str]]:
        calls.append(url)
        if "site=" in url:
            site = int(re.search(r"site=(\d+)", url).group(1))  # type: ignore[union-attr]
            html = (
                f"<html><body><p>Page {site} of 10</p>"
                f'<a href="/en/YuGiOh/Products/Singles/Rarity-Collection-5/'
                f'Blue-Eyes-White-Dragon-V1-Ultra-Rare">x</a></body></html>'
            ).encode()
        else:
            html = SYNTHETIC_PRODUCT_METRICS_HTML.encode()
        return (
            FetchedResource(url, url, 200, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}),
            [],
        )

    monkeypatch.setattr(cm_example, "fetch_listing_markup", fake_fetch)
    monkeypatch.setattr(cm_example.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cm_example.random, "uniform", lambda _a, _b: 0.0)

    result = cm_example.crawl_deep_enrichment(
        coverage_csv=corpus_csv,
        checkpoint_path=checkpoint,
        delay_seconds=8.0,
        max_navigations=5,
        search_start_site=8,
        fetch_product_details=True,
    )
    assert checkpoint.exists()
    assert result["budget"]["navigations_used"] == 5
    assert result["search_last_site"] >= 8
    # First search URL must be site=8.
    assert any("site=8" in url for url in calls)
    assert calls[0].endswith("site=8") or "site=8" in calls[0]
    # Deterministic product order after search pages consume part of budget.
    ok_paths = [item["product_path"] for item in result["queue"] if item["status"] == "ok"]
    assert ok_paths == sorted(ok_paths, key=str.lower)
    assert all(item["status"] in {"pending", "ok", "challenge", "error"} for item in result["queue"])

    # Resume must not re-attempt ok items.
    calls_before = len(calls)
    ok_before = {item["product_path"] for item in result["queue"] if item["status"] == "ok"}
    resumed = cm_example.crawl_deep_enrichment(
        coverage_csv=corpus_csv,
        checkpoint_path=checkpoint,
        delay_seconds=8.0,
        max_navigations=3,
        search_start_site=8,
        fetch_product_details=True,
        resume_from=result,
    )
    assert "resuming deep enrichment ledger" in " ".join(resumed.get("warnings") or [])
    # Previously ok paths remain ok and were not refetched as product URLs.
    ok_after = {item["product_path"] for item in resumed["queue"] if item["status"] == "ok"}
    assert ok_before <= ok_after
    product_calls = [url for url in calls[calls_before:] if "/Products/Singles/" in url]
    assert not any(
        cm_example.normalize_public_path(urlparse(url).path) in ok_before for url in product_calls
    )

    rows = cm_example.build_deep_enrichment_rows(resumed["queue"], source_date="2026-08-04")
    csv_text = cm_example.export_deep_enrichment_csv(rows, destination=tmp_path / "deep.csv")
    assert "seller" not in csv_text.lower()
    assert "username" not in csv_text.lower()
    assert csv_text.splitlines()[0] == ",".join(cm_example.DEEP_ENRICHMENT_CSV_COLUMNS)
    assert len(rows) == 177
    manifest = cm_example.build_deep_enrichment_manifest(resumed, rows=rows)
    assert manifest["corpus"]["total"] == 177
    assert manifest["scope_qualification"]["versions_complete"] is True
    assert manifest["scope_qualification"]["offers_non_exhaustive"] is True
    assert "seller" not in json_dumps(manifest).lower() or manifest["privacy"]["seller_fields"].startswith("never")

    # Hard challenge stop after exactly 2 consecutive challenges.
    challenge_calls: list[str] = []

    def always_challenge(url: str, **kwargs: object) -> tuple[FetchedResource, list[str]]:
        challenge_calls.append(url)
        html = b"<html><body>Please verify you are a human captcha</body></html>"
        return FetchedResource(url, url, 403, html, "text/html; charset=utf-8", {"x-fetch-method": "cloak"}), []

    monkeypatch.setattr(cm_example, "fetch_listing_markup", always_challenge)
    blocked_checkpoint = tmp_path / "blocked.json"
    blocked = cm_example.crawl_deep_enrichment(
        coverage_csv=corpus_csv,
        checkpoint_path=blocked_checkpoint,
        delay_seconds=8.0,
        max_navigations=10,
        search_start_site=8,
        fetch_product_details=True,
        first_access_cooldown_seconds=0.0,
    )
    # First access challenge triggers one cooldown retry, then stop.
    assert blocked["stop_reason"] == "first_access_hard_challenge_after_cooldown"
    assert len(challenge_calls) == 2

    # Non-first-access: stop after 2 consecutive hard challenges on product details.
    challenge_calls.clear()
    monkeypatch.setattr(cm_example, "fetch_listing_markup", fake_fetch)
    mid = cm_example.crawl_deep_enrichment(
        coverage_csv=corpus_csv,
        checkpoint_path=tmp_path / "mid.json",
        delay_seconds=8.0,
        max_navigations=1,
        search_start_site=8,
        fetch_product_details=False,
        resume_from=None,
    )
    assert mid["pages"]
    monkeypatch.setattr(cm_example, "fetch_listing_markup", always_challenge)
    # Mark first_access already consumed so normal stop=2 applies.
    mid["first_access_retry_consumed"] = True
    mid["search_pagination_complete"] = True
    mid["search_last_site"] = 10
    stopped = cm_example.crawl_deep_enrichment(
        coverage_csv=corpus_csv,
        checkpoint_path=tmp_path / "stopped.json",
        delay_seconds=8.0,
        max_navigations=5,
        search_start_site=8,
        fetch_product_details=True,
        resume_from=mid,
        first_access_cooldown_seconds=0.0,
    )
    assert stopped["stop_reason"] == "hard_challenges_x2"
    assert len(challenge_calls) == 2


def test_deep_enrichment_delay_and_budget_guards(cm_example: ModuleType, tmp_path: Path) -> None:
    repo_csv = (
        Path(__file__).resolve().parents[1]
        / "docs/data/cardmarket-blue-eyes-coverage-2026-08-04.csv"
    )
    with pytest.raises(ValueError, match="delay_seconds"):
        cm_example.crawl_deep_enrichment(
            coverage_csv=repo_csv,
            checkpoint_path=tmp_path / "x.json",
            delay_seconds=2.0,
            max_navigations=5,
        )
    with pytest.raises(ValueError, match="max_navigations"):
        cm_example.crawl_deep_enrichment(
            coverage_csv=repo_csv,
            checkpoint_path=tmp_path / "x.json",
            delay_seconds=8.0,
            max_navigations=99,
        )


def test_sanitize_deep_ledger_strips_seller_and_html(cm_example: ModuleType) -> None:
    dirty = {
        "queue": [{"product_path": "/x", "seller_name": "bad", "from_cents": 10}],
        "raw_html": "<html>secret</html>",
        "offers": [{"seller": "nope"}],
        "ok": True,
    }
    clean = cm_example.sanitize_deep_ledger(dirty)
    blob = json_dumps(clean)
    assert "seller" not in blob
    assert "raw_html" not in clean
    assert "offers" not in clean
    assert clean["ok"] is True
    assert clean["queue"][0]["from_cents"] == 10


def _synthetic_official_catalogs() -> tuple[bytes, bytes]:
    products = {
        "version": 1,
        "createdAt": "2026-08-04T11:20:15+0200",
        "products": [
            {
                "idProduct": 1001,
                "name": "Blue-Eyes White Dragon",
                "idCategory": 5,
                "categoryName": "Yugioh Single",
                "idExpansion": 10,
                "idMetacard": 102062,
                "dateAdded": "2007-01-01 00:00:00",
            },
            {
                "idProduct": 1002,
                "name": "Blue-Eyes White Dragon",
                "idCategory": 5,
                "categoryName": "Yugioh Single",
                "idExpansion": 11,
                "idMetacard": 102062,
                "dateAdded": "2008-01-01 00:00:00",
            },
            {
                "idProduct": 2001,
                "name": "Malefic Blue-Eyes White Dragon",
                "idCategory": 5,
                "categoryName": "Yugioh Single",
                "idExpansion": 12,
                "idMetacard": 999,
                "dateAdded": "2010-01-01 00:00:00",
            },
            {
                "idProduct": 2002,
                "name": "Blue-Eyes White Dragon, the White Phantom Beast",
                "idCategory": 5,
                "categoryName": "Yugioh Single",
                "idExpansion": 13,
                "idMetacard": 888,
                "dateAdded": "2024-01-01 00:00:00",
            },
            {
                "idProduct": 3001,
                "name": "Dark Magician",
                "idCategory": 5,
                "categoryName": "Yugioh Single",
                "idExpansion": 14,
                "idMetacard": 1,
                "dateAdded": "2007-01-01 00:00:00",
            },
        ],
    }
    prices = {
        "version": 1,
        "createdAt": "2026-08-04T02:47:19+0200",
        "priceGuides": [
            {
                "idProduct": 1001,
                "idCategory": 5,
                "avg": 5.78,
                "low": 0.05,
                "trend": 6.41,
                "avg1": 0.41,
                "avg7": 9.02,
                "avg30": 8.47,
                "avg-foil": None,
                "low-foil": None,
                "trend-foil": 5.12,
                "avg1-foil": 7.89,
                "avg7-foil": 3.6,
                "avg30-foil": 5.54,
            },
            {
                "idProduct": 1002,
                "idCategory": 5,
                "avg": 1.20,
                "low": 0.10,
                "trend": 1.15,
                "avg1": 1.1,
                "avg7": 1.2,
                "avg30": 1.25,
                "avg-foil": 2.5,
                "low-foil": 1.0,
                "trend-foil": 2.4,
                "avg1-foil": 2.3,
                "avg7-foil": 2.2,
                "avg30-foil": 2.1,
            },
            {
                "idProduct": 2001,
                "idCategory": 5,
                "avg": 0.5,
                "low": 0.1,
                "trend": 0.4,
                "avg1": 0.4,
                "avg7": 0.4,
                "avg30": 0.4,
            },
            {
                "idProduct": 3001,
                "idCategory": 5,
                "avg": 3.0,
                "low": 1.0,
                "trend": 2.5,
                "avg1": 2.5,
                "avg7": 2.5,
                "avg30": 2.5,
            },
        ],
    }
    # Keep decimal literals exact in the fixture bytes (avoid float re-dump drift).
    products_raw = json.dumps(products, ensure_ascii=False, separators=(",", ":")).encode()
    prices_raw = (
        b'{"version":1,"createdAt":"2026-08-04T02:47:19+0200","priceGuides":['
        b'{"idProduct":1001,"idCategory":5,"avg":5.78,"low":0.05,"trend":6.41,'
        b'"avg1":0.41,"avg7":9.02,"avg30":8.47,"avg-foil":null,"low-foil":null,'
        b'"trend-foil":5.12,"avg1-foil":7.89,"avg7-foil":3.6,"avg30-foil":5.54},'
        b'{"idProduct":1002,"idCategory":5,"avg":1.20,"low":0.10,"trend":1.15,'
        b'"avg1":1.1,"avg7":1.2,"avg30":1.25,"avg-foil":2.5,"low-foil":1.0,'
        b'"trend-foil":2.4,"avg1-foil":2.3,"avg7-foil":2.2,"avg30-foil":2.1},'
        b'{"idProduct":2001,"idCategory":5,"avg":0.5,"low":0.1,"trend":0.4,'
        b'"avg1":0.4,"avg7":0.4,"avg30":0.4},'
        b'{"idProduct":3001,"idCategory":5,"avg":3.0,"low":1.0,"trend":2.5,'
        b'"avg1":2.5,"avg7":2.5,"avg30":2.5}'
        b"]}"
    )
    return products_raw, prices_raw


def test_official_catalog_url_protections(cm_example: ModuleType) -> None:
    good = cm_example.OFFICIAL_PRODUCTS_SINGLES_URL
    assert cm_example.validate_official_catalog_url(good, expected_url=good) == good
    with pytest.raises(ValueError, match="non-canonical"):
        cm_example.validate_official_catalog_url(
            good + "?x=1", expected_url=good
        )
    with pytest.raises(ValueError, match="non-canonical|https|host|query"):
        cm_example.validate_official_catalog_url(
            "http://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_3.json",
            expected_url=good,
        )
    with pytest.raises(ValueError, match="non-canonical"):
        cm_example.validate_official_catalog_url(
            "https://evil.example/products_singles_3.json",
            expected_url=good,
        )
    with pytest.raises(ValueError, match="non-canonical"):
        cm_example.validate_official_catalog_url(
            "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_99.json",
            expected_url=good,
        )


def test_official_join_schema_filter_join_decimals_hashes_determinism(
    cm_example: ModuleType, tmp_path: Path
) -> None:
    products_raw, prices_raw = _synthetic_official_catalogs()
    html_csv = tmp_path / "html.csv"
    html_csv.write_text(
        "public_product_path,from_status\n"
        "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon,priced\n"
        "/en/YuGiOh/Products/Singles/B/Blue-Eyes-White-Dragon-V1-Common,priced\n",
        encoding="utf-8",
    )
    # Bypass the 177-path guard for this tiny fixture by patching expectation briefly.
    original = cm_example.EXPECTED_EXACT_BLUE_EYES_PATHS
    cm_example.EXPECTED_EXACT_BLUE_EYES_PATHS = 2
    try:
        # load_exact_blue_eyes_paths_from_coverage_csv enforces 177 — build html paths manually.
        join = cm_example.build_official_blue_eyes_join(
            products_raw=products_raw,
            price_raw=prices_raw,
            html_coverage_csv=None,
            source_date="2026-08-04",
            fetched_live=False,
        )
    finally:
        cm_example.EXPECTED_EXACT_BLUE_EYES_PATHS = original

    assert join["exact_count"] == 2
    assert join["excluded_count"] == 2
    rows = join["rows"]
    assert [row["idProduct"] for row in rows] == ["1001", "1002"]
    assert rows[0]["avg"] == "5.78"
    assert rows[0]["low"] == "0.05"
    assert rows[1]["avg"] == "1.20"
    assert rows[1]["low"] == "0.10"
    assert join["products_sha256"] == cm_example.sha256_hex(products_raw)
    assert join["price_sha256"] == cm_example.sha256_hex(prices_raw)
    manifest = join["manifest"]
    assert manifest["filter"]["contains_excluded"] == 2
    assert manifest["join"]["matched"] == 2
    assert manifest["corpus"]["url_to_idProduct_mapping"]["verified"] is False
    assert "102062" in {str(x) for x in manifest["corpus"]["official_by_idProduct"]["idMetacard"]}

    csv_a = cm_example.export_official_join_csv(rows)
    csv_b = cm_example.export_official_join_csv(rows)
    assert csv_a == csv_b
    again = cm_example.build_official_blue_eyes_join(
        products_raw=products_raw,
        price_raw=prices_raw,
        source_date="2026-08-04",
        fetched_live=False,
    )
    assert cm_example.export_official_join_csv(again["rows"]) == csv_a
    assert again["products_sha256"] == join["products_sha256"]
    assert again["price_sha256"] == join["price_sha256"]


def test_official_join_rejects_duplicate_id_product(cm_example: ModuleType) -> None:
    products_raw, prices_raw = _synthetic_official_catalogs()
    products = json.loads(products_raw.decode())
    products["products"].append(dict(products["products"][0]))
    with pytest.raises(ValueError, match="duplicate idProduct"):
        cm_example.build_official_blue_eyes_join(
            products_raw=json.dumps(products).encode(),
            price_raw=prices_raw,
            source_date="2026-08-04",
        )


def test_official_join_rejects_missing_price_rows(cm_example: ModuleType) -> None:
    products_raw, prices_raw = _synthetic_official_catalogs()
    prices = json.loads(prices_raw.decode(), parse_float=__import__("decimal").Decimal)
    # Drop exact product 1002 from guide.
    prices["priceGuides"] = [row for row in prices["priceGuides"] if int(row["idProduct"]) != 1002]
    # Re-serialize carefully
    rebuilt = json.dumps(prices, default=str).encode()
    with pytest.raises(ValueError, match="price guide missing"):
        cm_example.build_official_blue_eyes_join(
            products_raw=products_raw,
            price_raw=rebuilt,
            source_date="2026-08-04",
        )


def test_official_join_rejects_non_exact_name_as_exact(cm_example: ModuleType) -> None:
    products_raw, prices_raw = _synthetic_official_catalogs()
    exact, excluded = cm_example.filter_exact_blue_eyes_official_products(
        cm_example.validate_products_catalog_schema(
            cm_example.parse_official_catalog_json(products_raw)
        )
    )
    assert all(row["name"] == "Blue-Eyes White Dragon" for row in exact)
    assert all("Blue-Eyes White Dragon" in row["name"] for row in excluded)
    assert all(row["name"] != "Blue-Eyes White Dragon" for row in excluded)


def test_official_download_single_get_and_bound(
    cm_example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    payload = b'{"version":1,"createdAt":"x","products":[]}'

    class FakeResp:
        def geturl(self) -> str:
            return cm_example.OFFICIAL_PRODUCTS_SINGLES_URL

        def read(self, n: int = -1) -> bytes:
            if not hasattr(self, "_sent"):
                self._sent = False
            if self._sent:
                return b""
            self._sent = True
            return payload

        def __enter__(self) -> "FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, timeout: int = 0) -> FakeResp:
        calls.append(getattr(request, "full_url", None) or request.get_full_url())
        return FakeResp()

    monkeypatch.setattr(cm_example, "urlopen", fake_urlopen)
    raw, digest = cm_example.download_official_catalog_bytes(
        cm_example.OFFICIAL_PRODUCTS_SINGLES_URL,
        expected_url=cm_example.OFFICIAL_PRODUCTS_SINGLES_URL,
    )
    assert raw == payload
    assert digest == cm_example.sha256_hex(payload)
    assert calls == [cm_example.OFFICIAL_PRODUCTS_SINGLES_URL]

    # Oversized response
    class FatResp(FakeResp):
        def read(self, n: int = -1) -> bytes:
            if getattr(self, "_n", 0) > 2:
                return b""
            self._n = getattr(self, "_n", 0) + 1
            return b"x" * 100

    def fat_urlopen(request: object, timeout: int = 0) -> FatResp:
        return FatResp()

    monkeypatch.setattr(cm_example, "urlopen", fat_urlopen)
    with pytest.raises(cm_example.FetchError, match="max_bytes"):
        cm_example.download_official_catalog_bytes(
            cm_example.OFFICIAL_PRODUCTS_SINGLES_URL,
            expected_url=cm_example.OFFICIAL_PRODUCTS_SINGLES_URL,
            max_bytes=50,
        )


def test_deep_enrichment_manifest_marks_baseline_reuse(cm_example: ModuleType) -> None:
    rows = [
        {
            "public_product_path": "/en/YuGiOh/Products/Singles/A/Blue-Eyes-White-Dragon",
            "from_cents": "100",
            "available_count": "3",
            "fields_present": "",
            "detail_ok": "no",
        }
    ]
    ledger = {
        "timezone": "UTC",
        "started_at": "2026-08-04T00:00:00+00:00",
        "stop_reason": "first_access_hard_challenge_after_cooldown",
        "search_last_site": 8,
        "budget": {"navigations_used": 2, "max_navigations": 40},
        "pages": [
            {"route": "search", "challenge": True},
            {"route": "search", "challenge": True},
        ],
        "queue": [{"status": "pending", "attempts": 0}],
    }
    manifest = cm_example.build_deep_enrichment_manifest(ledger, rows=rows)
    assert manifest["scope_qualification"]["live_product_details_succeeded"] == 0
    assert manifest["scope_qualification"]["live_search_challenge_navigations"] == 2
    assert "baseline" in manifest["baseline_reused_fields"]["note"].lower()
    md = cm_example.render_deep_enrichment_manifest_markdown(manifest)
    assert "Baseline reuse" in md
    assert "Live Search challenge navigations" in md


def test_published_official_join_snapshot_shape() -> None:
    repo = Path(__file__).resolve().parents[1]
    csv_path = repo / "docs/data/cardmarket-blue-eyes-official-join-2026-08-04.csv"
    manifest_path = repo / "docs/data/cardmarket-blue-eyes-official-join-manifest-2026-08-04.json"
    assert csv_path.exists()
    assert manifest_path.exists()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) == 177
    assert manifest["filter"]["exact_matches"] == 177
    assert manifest["filter"]["contains_excluded"] == 15
    assert manifest["official_sources"]["products"]["singles_before_filter"] == 86255
    assert manifest["corpus"]["official_by_idProduct"]["idExpansion_count"] == 102
    assert manifest["corpus"]["official_by_idProduct"]["idMetacard"] == [102062]
    assert manifest["join"]["matched"] == 177
    assert manifest["corpus"]["url_to_idProduct_mapping"]["verified"] is False
    assert manifest["corpus"]["html_by_url"]["count"] == 177
    # Deterministic idProduct order
    ids = [int(row["idProduct"]) for row in rows]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    # No raw catalog committed
    assert not (repo / "docs/data/products_singles_3.json").exists()
    assert not (repo / "docs/data/price_guide_3.json").exists()


def test_published_deep_manifest_does_not_claim_live_from_extraction() -> None:
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (
            repo / "docs/data/cardmarket-blue-eyes-deep-enrichment-manifest-2026-08-04.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["succeeded"] == 0
    assert manifest["scope_qualification"]["live_product_details_succeeded"] == 0
    assert manifest["scope_qualification"]["live_search_challenge_navigations"] == 2
    note = manifest["baseline_reused_fields"]["note"].lower()
    assert "reused" in note or "baseline" in note
    assert "not newly extracted" in note
