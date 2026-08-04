#!/usr/bin/env python3
"""Cardmarket Blue-Eyes White Dragon market example on supersocks-url-scraper.

Parses publicly rendered Cardmarket HTML for Blue-Eyes White Dragon / Dragon
Blanc aux Yeux Bleus. Seller identities are stripped and never emitted. Raw
HTML is kept in memory only; this example does not write page dumps, cookies,
tokens, or browser profiles.

It never solves CAPTCHA, logs in, accepts tracking consent, or bypasses access
challenges. When a strict challenge page is detected, pagination/collection
stops and the result is reported as partial/blocked.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    SRC = Path(__file__).resolve().parents[1] / "src"
    if SRC.exists():
        sys.path.insert(0, str(SRC))

from supersocks_url_scraper.reader import (
    DEFAULT_TIMEOUT,
    DESKTOP_UA,
    MAX_BYTES,
    FetchError,
    FetchedResource,
    fetch_url,
    fetch_with_browser,
    fetch_with_seo_variants,
)

MAX_URL_LIMIT = 20
MAX_COVERAGE_NAVIGATIONS = 230
MIN_PRODUCT_DELAY_SECONDS = 2.0
HARD_CHALLENGE_STOP = 3
MAX_DEEP_ENRICHMENT_NAVIGATIONS = 40
MIN_DEEP_DELAY_SECONDS = 8.0
DEEP_HARD_CHALLENGE_STOP = 2
FIRST_ACCESS_COOLDOWN_SECONDS = 45.0
EXPECTED_EXACT_BLUE_EYES_PATHS = 177
DEFAULT_SEARCH_QUERY = "Blue-Eyes White Dragon"
DEEP_ENRICHMENT_STATUSES = frozenset({"pending", "ok", "challenge", "error"})
EXACT_BLUE_EYES_PRODUCT_NAME = "Blue-Eyes White Dragon"
OFFICIAL_PRODUCTS_SINGLES_URL = (
    "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_3.json"
)
OFFICIAL_PRICE_GUIDE_URL = (
    "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_3.json"
)
OFFICIAL_CATALOG_HOST = "downloads.s3.cardmarket.com"
OFFICIAL_CATALOG_TIMEOUT_SECONDS = 120
OFFICIAL_CATALOG_MAX_BYTES = MAX_BYTES
OFFICIAL_CATALOG_CHUNK_SIZE = 64 * 1024
OFFICIAL_PRICE_NUMERIC_FIELDS: tuple[str, ...] = (
    "avg",
    "low",
    "trend",
    "avg1",
    "avg7",
    "avg30",
    "avg-foil",
    "low-foil",
    "trend-foil",
    "avg1-foil",
    "avg7-foil",
    "avg30-foil",
)
OFFICIAL_JOIN_CSV_COLUMNS: tuple[str, ...] = (
    "idProduct",
    "idExpansion",
    "idMetacard",
    "dateAdded",
    "category",
    "name",
    "avg",
    "low",
    "trend",
    "avg1",
    "avg7",
    "avg30",
    "avg_foil",
    "low_foil",
    "trend_foil",
    "avg1_foil",
    "avg7_foil",
    "avg30_foil",
    "fields_present",
    "fields_absent",
    "products_created_at",
    "price_guide_created_at",
    "source_date",
)
SELLER_LIKE_KEYS = frozenset(
    {
        "seller",
        "seller_name",
        "username",
        "user",
        "location",
        "country",
        "cookie",
        "cookies",
        "account",
        "email",
        "html",
        "raw_html",
        "markup",
        "offers",
        "offer_rows",
        "article_id",
        "seller_url",
    }
)
VERSIONS_EN_URL = "https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions"
VERSIONS_FR_URL = "https://www.cardmarket.com/fr/YuGiOh/Cards/BlueEyes-White-Dragon/Versions"
SEARCH_EN_URL = "https://www.cardmarket.com/en/YuGiOh/Products/Search"
CARD_HUB_EN_URL = "https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon"
# Prefer interstitial / delivery markers. Do not treat Cloudflare's passive
# `/cdn-cgi/challenge-platform/scripts/jsd/main.js` beacon as a hard block when
# offer markup is otherwise present.
ACCESS_CHALLENGE_PATTERN = re.compile(
    r"geo\.captcha-delivery\.com|"
    r"cf-browser-verification|"
    r"cdn-cgi/challenge-platform/h/|"
    r"\baccess\s+denied\b|"
    r"\baccess\s+challenge\b|"
    r"\bdatadome\b|"
    r"please\s+verify\s+you\s+are\s+a\s+human|"
    r"\bcaptcha\b",
    re.I,
)
PASSIVE_CF_JSD = re.compile(r"cdn-cgi/challenge-platform/scripts/jsd/", re.I)

COND_MAP = {
    "mt": "Mint",
    "nm": "Near Mint",
    "ex": "Excellent",
    "gd": "Good",
    "lp": "Light Played",
    "pl": "Played",
    "po": "Poor",
}
LANGUAGES = (
    "English",
    "French",
    "German",
    "Italian",
    "Spanish",
    "Portuguese",
    "Japanese",
    "Korean",
    "Chinese",
    "Dutch",
)
RARITIES = (
    "Quarter Century Secret Rare",
    "Platinum Secret Rare",
    "Prismatic Secret Rare",
    "Starlight Rare",
    "Ghost Rare",
    "Secret Rare",
    "Ultimate Rare",
    "Collector's Rare",
    "Ultra Rare",
    "Super Rare",
    "Rare",
    "Common",
)
EDITIONS = ("First Edition", "1st Edition", "Unlimited", "Limited Edition")


@dataclass(frozen=True)
class CollectConfig:
    browser_fallback: bool = True
    browser_post_load_wait_ms: int = 9000
    delay_seconds: float = 2.5
    timeout: int = DEFAULT_TIMEOUT


def is_access_challenge(markup: str) -> bool:
    """Return whether markup looks like a hard access challenge.

    Passive Cloudflare JSD beacons alone are not treated as a block when the
    page still exposes Cardmarket offer/version markup.
    """
    text = markup or ""
    if not ACCESS_CHALLENGE_PATTERN.search(text):
        return False
    if re.search(r'id="articleRow\d+"|card-text text-muted">From\s|<b>\s*[0-9]', text, re.I):
        # Offer/version content present: ignore passive captcha/jsd string hits.
        if PASSIVE_CF_JSD.search(text) and not re.search(
            r"geo\.captcha-delivery\.com|cf-browser-verification|cdn-cgi/challenge-platform/h/|"
            r"\baccess\s+denied\b|\bdatadome\b|please\s+verify\s+you\s+are\s+a\s+human",
            text,
            re.I,
        ):
            return False
        # "captcha" may appear in cookie copy; require stronger markers or no offers.
        if re.search(r'id="articleRow\d+"|card-text text-muted">From\s', text, re.I):
            strong = re.search(
                r"geo\.captcha-delivery\.com|cf-browser-verification|"
                r"cdn-cgi/challenge-platform/h/|\baccess\s+denied\b|\bdatadome\b|"
                r"please\s+verify\s+you\s+are\s+a\s+human",
                text,
                re.I,
            )
            return strong is not None
    return True


def parse_euro_to_cents(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "€" not in text and not re.search(r"\bEUR\b", text, re.I):
        # Allow bare numeric major units when caller already isolated a price cell.
        if not re.fullmatch(r"[0-9][0-9\s\u00a0.,]*", text):
            return None
    match = re.search(
        r"([0-9]{1,3}(?:[.\s\u00a0][0-9]{3})*(?:[.,][0-9]{1,2})?|[0-9]+(?:[.,][0-9]{1,2})?)",
        text,
    )
    if not match:
        return None
    cleaned = match.group(1).replace("\u00a0", " ").replace(" ", "").replace("'", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = "".join(parts) if len(parts[-1]) == 3 and len(parts) > 1 else cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def strip_tags(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html or "", flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tooltip_values(chunk: str) -> list[str]:
    values: list[str] = []
    for pattern in (
        r'data-bs-original-title="([^"]+)"',
        r'data-original-title="([^"]+)"',
        r'data-bs-title="([^"]+)"',
        r'aria-label="([^"]+)"',
    ):
        for match in re.finditer(pattern, chunk or "", re.I):
            value = strip_tags(match.group(1).replace("&nbsp;", " ").replace("&amp;", "&"))
            if value:
                values.append(value)
    return values


def pick_attr(values: Iterable[str], options: Iterable[str]) -> str | None:
    lookup = {option.lower(): option for option in options}
    for value in values:
        hit = lookup.get(value.strip().lower())
        if hit:
            return "First Edition" if hit == "1st Edition" else hit
    return None


def redact_seller_blocks(chunk: str) -> str:
    """Drop seller columns before attribute extraction."""
    return re.sub(
        r'<div[^>]*class="[^"]*col-seller[^"]*"[^>]*>.*?(?=<div[^>]*class="[^"]*col-product|$)',
        " ",
        chunk or "",
        flags=re.I | re.S,
    )


def is_blue_eyes_target(text: str, path: str = "") -> bool:
    blob = f"{text} {path}".lower()
    return any(
        token in blob
        for token in (
            "blue-eyes white dragon",
            "blue-eyes-white-dragon",
            "dragon blanc aux yeux bleus",
            "dragon-blanc-aux-yeux-bleus",
        )
    )


def parse_version_floor_cards(markup: str, *, source_url: str) -> list[dict[str, Any]]:
    """Parse Versions overview tiles into per-product floor prices.

    Each record is one product version floor (`From X €`), not an individual
    seller offer. Do not mix these with live offer rows.
    """
    rows: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<a[^>]+href="(/en/YuGiOh/Products/Singles/[^"]+)"[^>]*>(.*?)</a>',
        markup or "",
        re.I | re.S,
    ):
        href = match.group(1)
        chunk = match.group(0)
        text = strip_tags(chunk)
        if not is_blue_eyes_target(text, href):
            continue
        from_match = re.search(r"From\s+([0-9][0-9.,\s\u00a0]*)\s*€", text, re.I)
        if not from_match:
            continue
        cents = parse_euro_to_cents(from_match.group(1) + " €")
        if cents is None:
            continue
        available = None
        avail_match = re.search(r"([0-9][0-9.,]*)\s+Available", text, re.I)
        if avail_match:
            available = int(avail_match.group(1).replace(".", "").replace(",", ""))
        parts = href.strip("/").split("/")
        expansion = parts[4].replace("-", " ") if len(parts) > 4 else None
        product_slug = parts[5] if len(parts) > 5 else None
        rarity = None
        slug = (product_slug or "").lower()
        for candidate in RARITIES:
            token = candidate.lower().replace(" ", "-").replace("'", "")
            if token and token in slug:
                rarity = candidate
                break
        if rarity is None:
            rarity = pick_attr([text], RARITIES)
        version = None
        version_match = re.search(r"-V(\d+)-", href, re.I) or re.search(r"\bV(?:ersion)?\s*([0-9]+)\b", text, re.I)
        if version_match:
            version = f"V{version_match.group(1)}"
        anon_id = hashlib.sha256(f"version_floor|{href}|{cents}".encode()).hexdigest()[:16]
        rows.append(
            {
                "id": anon_id,
                "record_kind": "version_floor",
                "title": (product_slug or "Blue-Eyes White Dragon").replace("-", " "),
                "price_eur": float(Decimal(cents) / Decimal(100)),
                "price_cents": cents,
                "available_count": available,
                "language": None,
                "condition": None,
                "rarity": rarity,
                "edition": None,
                "expansion": expansion,
                "version": version,
                "graded": False,
                "product_path": href,
                "source_url": source_url,
            }
        )
    return dedupe_records(rows)


def parse_offer_rows(markup: str, *, source_url: str) -> list[dict[str, Any]]:
    """Parse live article-row offers without seller identities."""
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<div[^>]*id="(articleRow\d+)"[^>]*class="[^"]*article-row[^"]*"[^>]*>(.*?)(?=<div[^>]*id="articleRow\d+"|$)',
        re.I | re.S,
    )
    for match in pattern.finditer(markup or ""):
        article_id = match.group(1)
        chunk = redact_seller_blocks(match.group(2)[:8000])
        tips = tooltip_values(chunk)
        condition = None
        condition_match = re.search(r"article-condition\s+condition-([a-z0-9-]+)", chunk, re.I)
        if condition_match:
            condition = COND_MAP.get(condition_match.group(1).lower(), condition_match.group(1))
        condition = condition or pick_attr(tips, COND_MAP.values())
        language = pick_attr(tips, LANGUAGES)
        rarity = pick_attr(tips, RARITIES)
        edition = pick_attr(tips, EDITIONS)
        expansion_code = None
        expansion_match = re.search(
            r"yugiohExpansionIcon[^>]*>\s*<span>([^<]+)</span>",
            chunk,
            re.I,
        )
        if expansion_match:
            expansion_code = expansion_match.group(1).strip()
        price_cents = None
        price_match = re.search(
            r"price-container.*?([0-9][0-9.,\s\u00a0]*)\s*€",
            chunk,
            re.I | re.S,
        )
        if price_match:
            price_cents = parse_euro_to_cents(price_match.group(1) + " €")
        if price_cents is None:
            price_cents = parse_euro_to_cents(strip_tags(chunk))
        if price_cents is None:
            continue
        title = "Blue-Eyes White Dragon"
        title_match = re.search(r'alt="([^"]*Blue-Eyes[^"]*)"', match.group(0), re.I)
        if title_match:
            title = strip_tags(title_match.group(1))[:180]
        graded = bool(re.search(r"\b(PSA|BGS|CGC|SGC)\s*\d+", strip_tags(chunk), re.I))
        anon_id = hashlib.sha256(
            f"offer|{article_id}|{price_cents}|{language}|{condition}|{rarity}|{edition}|{expansion_code}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": anon_id,
                "record_kind": "offer",
                "article_id_hash": hashlib.sha256(article_id.encode()).hexdigest()[:12],
                "title": title,
                "price_eur": float(Decimal(price_cents) / Decimal(100)),
                "price_cents": price_cents,
                "language": language,
                "condition": condition,
                "rarity": rarity,
                "edition": edition,
                "expansion_code": expansion_code,
                "graded": graded,
                "source_url": source_url,
            }
        )
    return dedupe_records(rows)


def dedupe_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def price_quartiles(values: Iterable[float]) -> dict[str, Any]:
    series = sorted(float(value) for value in values)
    if not series:
        return {"n": 0}

    def quantile(p: float) -> float:
        index = (len(series) - 1) * p
        low = int(index)
        high = min(low + 1, len(series) - 1)
        frac = index - low
        return round(series[low] * (1 - frac) + series[high] * frac, 4)

    return {
        "n": len(series),
        "min": round(series[0], 4),
        "q1": quantile(0.25),
        "median": quantile(0.5),
        "q3": quantile(0.75),
        "max": round(series[-1], 4),
    }


def cents_to_eur(cents: int) -> Decimal:
    """Convert integer cents to EUR with half-up quantize to 0.01."""
    return (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_quartiles_cents(values: Iterable[int]) -> dict[str, Any]:
    """Quartiles over integer cents, then expose EUR via Decimal (no float series)."""
    series = sorted(int(value) for value in values)
    if not series:
        return {"n": 0}

    def quantile_cents(p: float) -> int:
        index = Decimal(len(series) - 1) * Decimal(str(p))
        low = int(index)
        high = min(low + 1, len(series) - 1)
        frac = index - Decimal(low)
        interpolated = (
            Decimal(series[low]) * (Decimal(1) - frac) + Decimal(series[high]) * frac
        )
        return int(interpolated.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    stats_cents = {
        "n": len(series),
        "min_cents": series[0],
        "q1_cents": quantile_cents(0.25),
        "median_cents": quantile_cents(0.5),
        "q3_cents": quantile_cents(0.75),
        "max_cents": series[-1],
    }
    return {
        **stats_cents,
        "min": float(cents_to_eur(stats_cents["min_cents"])),
        "q1": float(cents_to_eur(stats_cents["q1_cents"])),
        "median": float(cents_to_eur(stats_cents["median_cents"])),
        "q3": float(cents_to_eur(stats_cents["q3_cents"])),
        "max": float(cents_to_eur(stats_cents["max_cents"])),
    }


VERSION_FLOOR_CSV_COLUMNS: tuple[str, ...] = (
    "expansion",
    "product_label",
    "version",
    "rarity",
    "from_eur",
    "from_cents",
    "available_count",
    "public_product_path",
    "source_date",
)

FORBIDDEN_REFERENCE_CSV_COLUMNS: frozenset[str] = frozenset(
    {
        "seller",
        "seller_name",
        "username",
        "article_id",
        "article_id_hash",
        "offer_id",
        "id",
        "cookie",
        "html",
        "raw_html",
    }
)


def _blank(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def version_floor_sort_key(row: dict[str, Any]) -> tuple[str, str, str, str, int]:
    return (
        _blank(row.get("expansion")).lower(),
        _blank(row.get("version")).lower(),
        _blank(row.get("rarity")).lower(),
        _blank(row.get("product_path")).lower(),
        int(row.get("price_cents") or 0),
    )


def extract_version_floors(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept collector stdout or anonymized ledger shapes; floors only."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("version_floors"), list):
            rows = payload["version_floors"]
        elif isinstance(payload.get("records"), list):
            rows = payload["records"]
        else:
            raise ValueError("JSON payload has no version_floors list")
    else:
        raise TypeError("payload must be a dict or list")
    floors = [row for row in rows if isinstance(row, dict) and row.get("record_kind") == "version_floor"]
    if not floors and rows and all(isinstance(row, dict) for row in rows):
        # Ledger already filtered to floors without record_kind in some captures.
        if all("product_path" in row and "price_cents" in row for row in rows):
            floors = list(rows)
    return floors


def aggregate_version_floors_by_expansion(
    version_floors: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group version floors by expansion/set label with min/median/max From.

    Printed card numbers (LOB-001, …) are never inferred — only fields present
    on the floor records are aggregated.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in version_floors:
        if row.get("record_kind") not in {None, "version_floor"}:
            continue
        expansion = _blank(row.get("expansion")) or "unknown"
        groups[expansion].append(row)

    summaries: list[dict[str, Any]] = []
    for expansion, rows in groups.items():
        cents = [int(row["price_cents"]) for row in rows if row.get("price_cents") is not None]
        stats = price_quartiles_cents(cents)
        rarities = sorted({_blank(row.get("rarity")) or "unknown" for row in rows})
        versions = sorted({_blank(row.get("version")) or "unknown" for row in rows})
        available_total = sum(int(row.get("available_count") or 0) for row in rows)
        summaries.append(
            {
                "expansion": expansion,
                "n": stats.get("n", 0),
                "min": stats.get("min"),
                "median": stats.get("median"),
                "max": stats.get("max"),
                "min_cents": stats.get("min_cents"),
                "median_cents": stats.get("median_cents"),
                "max_cents": stats.get("max_cents"),
                "available_total": available_total,
                "rarities": rarities,
                "versions": versions,
                "variants_note": (
                    f"{len(rarities)} rarity label(s), {len(versions)} version label(s)"
                    if stats.get("n", 0) > 1
                    else "single reference in this set"
                ),
            }
        )
    summaries.sort(
        key=lambda item: (
            -int(item.get("n") or 0),
            -int(item.get("median_cents") or 0),
            str(item.get("expansion") or "").lower(),
        )
    )
    return summaries


def rank_version_floor_references(
    version_floors: Sequence[dict[str, Any]],
    *,
    limit: int = 15,
) -> dict[str, list[dict[str, Any]]]:
    """Return dearest / cheapest / mid-priced floor references (floors only)."""
    floors = [
        row
        for row in version_floors
        if row.get("record_kind") in {None, "version_floor"} and row.get("price_cents") is not None
    ]
    by_price_asc = sorted(floors, key=lambda row: (int(row["price_cents"]), version_floor_sort_key(row)))
    by_price_desc = list(reversed(by_price_asc))
    median_cents = int(price_quartiles_cents(int(row["price_cents"]) for row in floors).get("median_cents") or 0)
    mid = sorted(
        floors,
        key=lambda row: (abs(int(row["price_cents"]) - median_cents), version_floor_sort_key(row)),
    )

    def slim(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "expansion": row.get("expansion"),
            "product_label": row.get("title"),
            "version": row.get("version"),
            "rarity": row.get("rarity"),
            "from_eur": float(cents_to_eur(int(row["price_cents"]))),
            "from_cents": int(row["price_cents"]),
            "available_count": row.get("available_count"),
            "public_product_path": row.get("product_path"),
        }

    return {
        "most_expensive": [slim(row) for row in by_price_desc[:limit]],
        "most_accessible": [slim(row) for row in by_price_asc[:limit]],
        "near_global_median": [slim(row) for row in mid[:limit]],
    }


def version_floor_reference_rows(
    version_floors: Sequence[dict[str, Any]],
    *,
    source_date: str,
) -> list[dict[str, str]]:
    """Build deterministic, sanitized CSV row dicts from version floors only."""
    floors = [
        row
        for row in version_floors
        if row.get("record_kind") in {None, "version_floor"} and row.get("price_cents") is not None
    ]
    ordered = sorted(floors, key=version_floor_sort_key)
    rows: list[dict[str, str]] = []
    for row in ordered:
        cents = int(row["price_cents"])
        rows.append(
            {
                "expansion": _blank(row.get("expansion")),
                "product_label": _blank(row.get("title")),
                "version": _blank(row.get("version")),
                "rarity": _blank(row.get("rarity")),
                "from_eur": format(cents_to_eur(cents), "f"),
                "from_cents": str(cents),
                "available_count": "" if row.get("available_count") is None else str(int(row["available_count"])),
                "public_product_path": _blank(row.get("product_path")),
                "source_date": source_date,
            }
        )
    return rows


def export_version_floor_references_csv(
    version_floors: Sequence[dict[str, Any]],
    *,
    source_date: str,
    destination: Path | None = None,
) -> str:
    """Serialize sanitized reference CSV; optionally write to disk.

    Columns never include seller names, offer/article ids, cookies, or HTML.
    """
    rows = version_floor_reference_rows(version_floors, source_date=source_date)
    if any(col in FORBIDDEN_REFERENCE_CSV_COLUMNS for col in VERSION_FLOOR_CSV_COLUMNS):
        raise ValueError("CSV column contract includes a forbidden field")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(VERSION_FLOOR_CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row[column] for column in VERSION_FLOOR_CSV_COLUMNS})
    text = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return text


def segment_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("condition") or "unknown"),
        str(row.get("language") or "unknown"),
        str(row.get("rarity") or "unknown"),
        str(row.get("edition") or "unknown"),
        "graded" if row.get("graded") else "raw",
    )


def summarize_populations(version_floors: list[dict[str, Any]], offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Build stats without silently mixing incompatible populations."""
    offers_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for offer in offers:
        offers_by_source[str(offer.get("source_url") or "unknown")].append(offer)

    source_summaries: dict[str, Any] = {}
    for source, rows in sorted(offers_by_source.items()):
        segments: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            segments["|".join(segment_key(row))].append(float(row["price_eur"]))
        source_summaries[source] = {
            "n": len(rows),
            "page_level_stats": price_quartiles(float(row["price_eur"]) for row in rows),
            "warning": (
                "Page-level stats may still mix rarities/editions when the live "
                "page is unfiltered; prefer segments."
            ),
            "condition": dict(Counter(row.get("condition") for row in rows)),
            "language": dict(Counter(row.get("language") for row in rows)),
            "rarity": dict(Counter(row.get("rarity") or "unknown" for row in rows)),
            "edition": dict(Counter(row.get("edition") or "unknown" for row in rows)),
            "graded": dict(Counter(bool(row.get("graded")) for row in rows)),
            "segments": {
                key: price_quartiles(values)
                for key, values in sorted(segments.items(), key=lambda item: (-len(item[1]), item[0]))
            },
        }

    rarity_groups: dict[str, list[float]] = defaultdict(list)
    for row in version_floors:
        rarity_groups[str(row.get("rarity") or "unknown")].append(float(row["price_eur"]))

    by_expansion = aggregate_version_floors_by_expansion(version_floors)
    return {
        "version_floors": {
            "n": len(version_floors),
            "stats": price_quartiles(float(row["price_eur"]) for row in version_floors),
            "by_rarity": {
                rarity: price_quartiles(values) for rarity, values in sorted(rarity_groups.items())
            },
            "by_expansion": by_expansion,
            "reference_ranks": rank_version_floor_references(version_floors),
            "note": (
                "Version floors are product-level 'From' prices, not offer rows. "
                "Prefer by_expansion / public_product_path over global quartiles."
            ),
            "printed_card_code_note": (
                "Official printed set codes (LOB-001, SDK-001, …) are absent from "
                "version_floor records in this pipeline and are never invented."
            ),
        },
        "offers_by_source": source_summaries,
    }


def fetch_listing_markup(
    url: str,
    *,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 9000,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
) -> tuple[FetchedResource | None, list[str]]:
    warnings: list[str] = []
    try:
        return fetch_url(url, timeout=timeout, max_bytes=max_bytes, user_agent=DESKTOP_UA), warnings
    except FetchError as http_error:
        warnings.append(f"http fetch failed: {http_error}")
    try:
        resource = fetch_with_seo_variants(url, timeout=timeout, max_bytes=max_bytes)
        warnings.append(f"seo fallback used: {resource.headers.get('x-seo-method', '')}")
        return resource, warnings
    except FetchError as seo_error:
        warnings.append(f"seo fallback failed: {seo_error}")
    if browser_fallback:
        try:
            resource = fetch_with_browser(
                url,
                timeout=max(timeout, 60),
                max_bytes=max_bytes,
                post_load_wait_ms=browser_post_load_wait_ms,
            )
            warnings.append(f"browser fallback used: {resource.headers.get('x-fetch-method', 'cloak')}")
            return resource, warnings
        except Exception as browser_error:  # noqa: BLE001 - surface any browser failure
            warnings.append(f"browser fallback failed: {browser_error}")
    return None, warnings


def collect_cardmarket_blue_eyes(
    urls: list[str],
    *,
    delay_seconds: float = 2.5,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 9000,
) -> dict[str, Any]:
    if not urls:
        raise ValueError("at least one URL is required")
    if len(urls) > MAX_URL_LIMIT:
        raise ValueError(f"url count must be between 1 and {MAX_URL_LIMIT}")

    pages: list[dict[str, Any]] = []
    version_floors: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    warnings: list[str] = []
    incomplete = False

    for index, url in enumerate(urls):
        resource, fetch_warnings = fetch_listing_markup(
            url,
            browser_fallback=browser_fallback,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
        )
        warnings.extend(fetch_warnings)
        fetch_method = None
        if resource is not None:
            fetch_method = (
                resource.headers.get("x-fetch-method")
                or resource.headers.get("x-seo-method")
                or "http"
            )
        page: dict[str, Any] = {
            "url": url,
            "status": "error" if resource is None else "ok",
            "fetch_method": fetch_method,
            "http_status": resource.status_code if resource is not None else None,
            "bytes": len(resource.content) if resource is not None else 0,
            "parsed": 0,
            "record_kind": None,
        }
        pages.append(page)
        if resource is None:
            incomplete = True
            continue
        markup = resource.text
        if is_access_challenge(markup):
            page["status"] = "blocked"
            incomplete = True
            warnings.append(f"{url}: access challenge detected; collection stopped")
            break
        if "/Versions" in url or "card-text text-muted\">From" in markup and "articleRow" not in markup:
            rows = parse_version_floor_cards(markup, source_url=url)
            page["record_kind"] = "version_floor"
            page["parsed"] = len(rows)
            version_floors.extend(rows)
            if not rows:
                page["status"] = "partial"
                incomplete = True
                warnings.append(f"{url}: no version floor cards parsed")
        else:
            rows = parse_offer_rows(markup, source_url=url)
            page["record_kind"] = "offer"
            page["parsed"] = len(rows)
            offers.extend(rows)
            if not rows:
                page["status"] = "partial"
                incomplete = True
                warnings.append(f"{url}: no offer rows parsed")
        if index + 1 < len(urls) and delay_seconds > 0:
            time.sleep(delay_seconds)

    version_floors = dedupe_records(version_floors)
    offers = dedupe_records(offers)
    if version_floors or offers:
        status = "partial" if incomplete else "ok"
    elif any(page["status"] in {"ok", "partial", "blocked"} for page in pages):
        status = "partial"
    else:
        status = "error"

    raw_count = sum(int(page.get("parsed") or 0) for page in pages)
    net_count = len(version_floors) + len(offers)
    failure_pages = sum(1 for page in pages if page["status"] in {"error", "blocked"})
    return {
        "status": status,
        "count_raw": raw_count,
        "count_net": net_count,
        "failure_rate": {
            "pages_total": len(pages),
            "pages_error_or_blocked": failure_pages,
            "rate": round(failure_pages / len(pages), 4) if pages else None,
        },
        "version_floors": version_floors,
        "offers": offers,
        "populations": summarize_populations(version_floors, offers),
        "pages": pages,
        "warnings": sorted(set(warnings)),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_public_path(url_or_path: str) -> str:
    """Normalize a Cardmarket URL or path to a stable public path."""
    raw = (url_or_path or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        path = parsed.path or ""
    else:
        path = raw
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    # Prefer English locale paths when only the locale prefix differs.
    path = re.sub(r"^/(de|es|fr|it)/", "/en/", path, count=1, flags=re.I)
    return path.rstrip("/") or path


def product_slug(path: str) -> str:
    parts = normalize_public_path(path).strip("/").split("/")
    return parts[-1] if parts else ""


def is_exact_blue_eyes_white_dragon_path(path: str) -> bool:
    """True for Blue-Eyes White Dragon product paths only (not Alternative/Toon/…).

    Accepts bare `Blue-Eyes-White-Dragon` and versioned slugs:
    `…-V1-…` or `…-V-1` / `…-V-1-…`. Rejects related cards that only share the
    prefix (e.g. `Blue-Eyes-White-Dragon-the-White-Phantom-Beast-…`).
    """
    slug = product_slug(path).lower()
    if slug == "blue-eyes-white-dragon":
        return True
    if re.match(r"^blue-eyes-white-dragon-v\d+(-|$)", slug):
        return True
    if re.match(r"^blue-eyes-white-dragon-v-\d+(-|$)", slug):
        return True
    return False


def canonical_product_url(path: str, *, origin: str = "https://www.cardmarket.com") -> str:
    path = normalize_public_path(path)
    return urljoin(origin.rstrip("/") + "/", path.lstrip("/"))


def extract_announced_versions_count(markup: str) -> int | None:
    match = re.search(r"\b(\d{1,4})\s+Versions?\b", markup or "", re.I)
    if not match:
        return None
    return int(match.group(1))


def extract_page_of(markup: str) -> tuple[int, int] | None:
    match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", markup or "", re.I)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def extract_product_paths(markup: str, *, exact_blue_eyes_only: bool = True) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="(/[^"]*/Products/Singles/[^"]+)"', markup or "", re.I):
        path = normalize_public_path(match.group(1).replace("&amp;", "&"))
        if "/Products/Singles/" not in path:
            continue
        if exact_blue_eyes_only and not is_exact_blue_eyes_white_dragon_path(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def parse_version_product_refs(markup: str, *, source_url: str) -> list[dict[str, Any]]:
    """Parse Versions tiles into product refs, including From N/A rows.

    Unlike parse_version_floor_cards(), this keeps unpriced public references so
    coverage can reach the announced Versions counter.
    """
    rows: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<a[^>]+href="(/[^"]*/YuGiOh/Products/Singles/[^"]+)"[^>]*>(.*?)</a>',
        markup or "",
        re.I | re.S,
    ):
        href = normalize_public_path(match.group(1))
        if not is_exact_blue_eyes_white_dragon_path(href):
            continue
        chunk = match.group(0)
        text = strip_tags(chunk)
        if not is_blue_eyes_target(text, href):
            continue
        from_status = "missing"
        cents: int | None = None
        from_match = re.search(r"From\s+([0-9][0-9.,\s\u00a0]*)\s*€", text, re.I)
        if from_match:
            cents = parse_euro_to_cents(from_match.group(1) + " €")
            from_status = "priced" if cents is not None else "unparsed"
        elif re.search(r"From\s+N\s*/\s*A\b", text, re.I):
            from_status = "na"
        available = None
        avail_match = re.search(r"([0-9][0-9.,]*)\s+Available", text, re.I)
        if avail_match:
            available = int(avail_match.group(1).replace(".", "").replace(",", ""))
        parts = href.strip("/").split("/")
        expansion = parts[4].replace("-", " ") if len(parts) > 4 else None
        product_slug_value = parts[5] if len(parts) > 5 else None
        rarity = None
        slug = (product_slug_value or "").lower()
        for candidate in RARITIES:
            token = candidate.lower().replace(" ", "-").replace("'", "")
            if token and token in slug:
                rarity = candidate
                break
        if rarity is None:
            rarity = pick_attr([text], RARITIES)
        version = None
        version_match = re.search(r"-V(\d+)-", href, re.I) or re.search(
            r"\bV(?:ersion)?\s*[.]?\s*([0-9]+)\b", text, re.I
        )
        if version_match:
            version = f"V{version_match.group(1)}"
        anon_id = hashlib.sha256(f"version_ref|{href}|{from_status}|{cents}".encode()).hexdigest()[:16]
        rows.append(
            {
                "id": anon_id,
                "record_kind": "version_ref",
                "title": (product_slug_value or "Blue-Eyes White Dragon").replace("-", " "),
                "price_eur": None if cents is None else float(Decimal(cents) / Decimal(100)),
                "price_cents": cents,
                "from_status": from_status,
                "available_count": available,
                "language": None,
                "condition": None,
                "rarity": rarity,
                "edition": None,
                "finish": None,
                "printed_code": None,
                "expansion": expansion,
                "version": version,
                "graded": False,
                "product_path": href,
                "source_url": source_url,
            }
        )
    return dedupe_by_product_path(rows)


def dedupe_by_product_path(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep first row per stable public product_path (coverage identity)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        path = normalize_public_path(str(row.get("product_path") or ""))
        if not path:
            continue
        if path in seen:
            duplicates += 1
            continue
        seen.add(path)
        cleaned = dict(row)
        cleaned["product_path"] = path
        out.append(cleaned)
    for row in out:
        row.setdefault("_dedupe_duplicates_skipped", duplicates)
    return out


def count_path_duplicates(paths: Sequence[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    unique: list[str] = []
    dupes = 0
    for raw in paths:
        path = normalize_public_path(raw)
        if not path:
            continue
        if path in seen:
            dupes += 1
            continue
        seen.add(path)
        unique.append(path)
    return unique, dupes


def build_search_page_url(query: str, site: int, *, locale: str = "en") -> str:
    params = urlencode({"searchString": query, "site": str(site)})
    return f"https://www.cardmarket.com/{locale}/YuGiOh/Products/Search?{params}"


def parse_search_results_page(markup: str, *, source_url: str) -> dict[str, Any]:
    all_paths = extract_product_paths(markup, exact_blue_eyes_only=False)
    exact_paths = [path for path in all_paths if is_exact_blue_eyes_white_dragon_path(path)]
    page_of = extract_page_of(markup)
    return {
        "source_url": source_url,
        "page_of": {"current": page_of[0], "total": page_of[1]} if page_of else None,
        "product_paths_all": all_paths,
        "product_paths_exact": exact_paths,
        "related_non_exact": [path for path in all_paths if path not in set(exact_paths)],
        "announced_versions": extract_announced_versions_count(markup),
        "empty": len(all_paths) == 0,
    }


def parse_product_public_details(markup: str, *, product_path: str, source_url: str) -> dict[str, Any]:
    """Extract only public product-identity fields when the HTML exposes them.

    Never collects seller, location, account, cookies, or other personal data.
    Official printed codes are recorded only when an explicit collector-number
    pattern appears near the product heading — never inferred from expansion.
    """
    path = normalize_public_path(product_path)
    parts = path.strip("/").split("/")
    expansion = parts[4].replace("-", " ") if len(parts) > 4 else None
    slug = parts[5] if len(parts) > 5 else None
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", markup or "", re.I | re.S)
    h1 = strip_tags(h1_match.group(1)) if h1_match else ""
    rarity = None
    for candidate in RARITIES:
        token = candidate.lower().replace(" ", "-").replace("'", "")
        if slug and token and token in slug.lower():
            rarity = candidate
            break
    if rarity is None:
        rarity = pick_attr([h1], RARITIES)
    version = None
    version_match = (
        re.search(r"-V(\d+)-", path, re.I)
        or re.search(r"\(\s*V\.?\s*(\d+)\s*[-–]", h1, re.I)
        or re.search(r"\bV(?:ersion)?\s*[.]?\s*(\d+)\b", h1, re.I)
    )
    if version_match:
        version = f"V{version_match.group(1)}"
    edition = pick_attr([h1], EDITIONS)
    # Product-level language/finish are rarely exposed outside offer rows; only
    # accept them from the heading / product attribute strip, not seller rows.
    header = markup or ""
    # Truncate before offer table to avoid treating offer languages as product facts.
    offer_cut = re.search(r'id="articleRow\d+"', header, re.I)
    header_zone = header[: offer_cut.start()] if offer_cut else header[:12000]
    header_text = strip_tags(header_zone)
    language = pick_attr([h1, header_text], LANGUAGES) if h1 else None
    finish = None
    for token in ("Foil", "Parallel Rare", "Holo", "Non-Foil", "Normal"):
        if re.search(rf"\b{re.escape(token)}\b", h1, re.I) or re.search(
            rf"\b{re.escape(token)}\b", header_text[:500], re.I
        ):
            finish = token
            break
    printed_code = None
    # Explicit collector-number style tokens near the H1 / product info only.
    # Never invent codes from expansion abbreviations alone.
    search_zone = f"{h1} {header_text[:800]}"
    code_match = re.search(
        r"\b((?:LOB|SDK|SDP|SDJ|TP[0-9]|MRD|SKE|DL[0-9]|LC[0-9]|RA[0-9]{2}|BLMR|TN[0-9]{2})-[A-Z]{0,2}\d{1,3}[A-Z]?)\b",
        search_zone,
        re.I,
    )
    if code_match:
        printed_code = code_match.group(1).upper()
    # Number field on product info (e.g. "Number" + "LOB-001") — still requires an
    # explicit set-code pattern; bare digits are ignored.
    if printed_code is None:
        number_label = re.search(
            r"(?:Collector'?s?\s*Number|Number)\s*[:\s]*"
            r"((?:LOB|SDK|SDP|SDJ|TP[0-9]|MRD|SKE|DL[0-9]|LC[0-9]|RA[0-9]{2}|BLMR|TN[0-9]{2})"
            r"-[A-Z]{0,2}\d{1,3}[A-Z]?)",
            header_text[:2000],
            re.I,
        )
        if number_label:
            printed_code = number_label.group(1).upper()

    public_metrics = parse_product_public_metrics(header_zone)
    # Language/condition aggregates: computed in memory from redacted offer rows only.
    # Individual seller lines are never retained or returned.
    language_counts: dict[str, int] = {}
    condition_counts: dict[str, int] = {}
    offer_rows = parse_offer_rows(markup or "", source_url=source_url)
    for row in offer_rows:
        lang = row.get("language")
        if isinstance(lang, str) and lang:
            language_counts[lang] = language_counts.get(lang, 0) + 1
        cond = row.get("condition")
        if isinstance(cond, str) and cond:
            condition_counts[cond] = condition_counts.get(cond, 0) + 1
    # Drop offer_rows immediately; only keep aggregate counters.
    del offer_rows

    article_rows = len(re.findall(r'id="articleRow\d+"', markup or "", re.I))
    fields_present = {
        "printed_code": printed_code is not None,
        "expansion": expansion is not None,
        "version": version is not None,
        "rarity": rarity is not None,
        "edition": edition is not None,
        "language": language is not None,
        "finish": finish is not None,
        "from_price": public_metrics.get("from_cents") is not None
        or public_metrics.get("from_status") == "na",
        "available_count": public_metrics.get("available_count") is not None,
        "price_trend": public_metrics.get("price_trend_cents") is not None,
        "avg_1d": public_metrics.get("avg_1d_cents") is not None,
        "avg_7d": public_metrics.get("avg_7d_cents") is not None,
        "avg_30d": public_metrics.get("avg_30d_cents") is not None,
    }
    return {
        "record_kind": "product_detail",
        "product_path": path,
        "source_url": source_url,
        "title": h1 or (slug or "Blue-Eyes White Dragon").replace("-", " "),
        "expansion": expansion,
        "version": version,
        "rarity": rarity,
        "edition": edition,
        "language": language,
        "finish": finish,
        "printed_code": printed_code,
        "from_status": public_metrics.get("from_status"),
        "from_cents": public_metrics.get("from_cents"),
        "available_count": public_metrics.get("available_count"),
        "price_trend_cents": public_metrics.get("price_trend_cents"),
        "avg_1d_cents": public_metrics.get("avg_1d_cents"),
        "avg_7d_cents": public_metrics.get("avg_7d_cents"),
        "avg_30d_cents": public_metrics.get("avg_30d_cents"),
        "language_counts": language_counts or None,
        "condition_counts": condition_counts or None,
        "article_row_count_first_page": article_rows,
        "fields_present": fields_present,
        "fields_absent": sorted(name for name, present in fields_present.items() if not present),
    }


def parse_product_public_metrics(header_zone: str) -> dict[str, Any]:
    """Parse product-level public price metrics from the non-offer header zone."""
    text = strip_tags(header_zone or "")
    compact = re.sub(r"\s+", " ", text)

    def _labeled_euro(label: str) -> int | None:
        match = re.search(
            rf"{label}\s*([0-9][0-9.,\s\u00a0]*)\s*€",
            compact,
            re.I,
        )
        if not match:
            return None
        return parse_euro_to_cents(match.group(1))

    from_status: str | None = None
    from_cents: int | None = None
    if re.search(r"\bFrom\s+N\s*/\s*A\b", compact, re.I):
        from_status = "na"
    else:
        from_cents = _labeled_euro(r"From")
        if from_cents is not None:
            from_status = "priced"

    available = None
    avail_match = re.search(r"Available items\s*([0-9][0-9.,\s]*)", compact, re.I)
    if not avail_match:
        avail_match = re.search(r"\b([0-9][0-9.,\s]*)\s*Available\b", compact, re.I)
    if avail_match:
        digits = re.sub(r"[^\d]", "", avail_match.group(1))
        if digits:
            available = int(digits)

    return {
        "from_status": from_status,
        "from_cents": from_cents,
        "available_count": available,
        "price_trend_cents": _labeled_euro(r"Price Trend"),
        "avg_30d_cents": _labeled_euro(r"30-days? average price"),
        "avg_7d_cents": _labeled_euro(r"7-days? average price"),
        "avg_1d_cents": _labeled_euro(r"1-day average price"),
    }


@dataclass
class CoverageBudget:
    max_navigations: int = MAX_COVERAGE_NAVIGATIONS
    navigations: int = 0
    consecutive_hard_challenges: int = 0
    rate_limit_events: int = 0

    def remaining(self) -> int:
        return max(0, self.max_navigations - self.navigations)


@dataclass
class CoverageLedger:
    started_at: str = field(default_factory=utc_now_iso)
    timezone: str = "UTC"
    pages: list[dict[str, Any]] = field(default_factory=list)
    version_refs: list[dict[str, Any]] = field(default_factory=list)
    search_paths: list[str] = field(default_factory=list)
    product_details: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    announced_versions: int | None = None
    routes_attempted: list[str] = field(default_factory=list)
    search_pagination_complete: bool = False
    search_last_site: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "timezone": self.timezone,
            "finished_at": utc_now_iso(),
            "pages": self.pages,
            "version_refs": self.version_refs,
            "search_paths": self.search_paths,
            "product_details": self.product_details,
            "warnings": sorted(set(self.warnings)),
            "errors": self.errors,
            "stop_reason": self.stop_reason,
            "announced_versions": self.announced_versions,
            "routes_attempted": self.routes_attempted,
            "search_pagination_complete": self.search_pagination_complete,
            "search_last_site": self.search_last_site,
        }


def polite_delay(seconds: float, *, jitter_ratio: float = 0.15) -> None:
    if seconds <= 0:
        return
    jitter = seconds * jitter_ratio * random.uniform(-1.0, 1.0)
    time.sleep(max(0.0, seconds + jitter))


def backoff_seconds(attempt: int, *, base: float = 2.0, cap: float = 60.0) -> float:
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay + random.uniform(0.0, min(1.5, delay * 0.25))


def should_stop_search_pagination(
    *,
    parsed: dict[str, Any],
    previous_paths: set[str] | None,
    site: int,
) -> str | None:
    if parsed.get("empty"):
        return "search_page_empty"
    page_of = parsed.get("page_of") or {}
    current = page_of.get("current")
    total = page_of.get("total")
    if isinstance(current, int) and isinstance(total, int):
        if current > total:
            return "search_page_beyond_announced_total"
        if current == total:
            # Caller still records this page; stop afterwards.
            return None
    paths = set(parsed.get("product_paths_all") or [])
    if previous_paths is not None and paths and paths == previous_paths:
        return "search_page_repeated"
    if isinstance(total, int) and site > total:
        return "search_site_beyond_announced_total"
    return None


def fetch_coverage_page(
    url: str,
    *,
    budget: CoverageBudget,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 9000,
    rate_limit_attempt: int = 0,
) -> tuple[FetchedResource | None, dict[str, Any], list[str]]:
    """Fetch one public page, accounting for budget and soft 403/429 backoff."""
    warnings: list[str] = []
    if budget.remaining() <= 0:
        return None, {"url": url, "status": "skipped", "reason": "budget_exhausted"}, warnings
    budget.navigations += 1
    resource, fetch_warnings = fetch_listing_markup(
        url,
        browser_fallback=browser_fallback,
        browser_post_load_wait_ms=browser_post_load_wait_ms,
    )
    warnings.extend(fetch_warnings)
    page: dict[str, Any] = {
        "url": url,
        "attempted_at": utc_now_iso(),
        "navigation_index": budget.navigations,
        "status": "error" if resource is None else "ok",
        "fetch_method": None,
        "http_status": None,
        "bytes": 0,
        "challenge": False,
        "parsed": 0,
        "route": None,
    }
    if resource is None:
        return None, page, warnings
    page["fetch_method"] = (
        resource.headers.get("x-fetch-method")
        or resource.headers.get("x-seo-method")
        or "http"
    )
    page["http_status"] = resource.status_code
    page["bytes"] = len(resource.content)
    markup = resource.text
    if is_access_challenge(markup):
        page["status"] = "blocked"
        page["challenge"] = True
        budget.consecutive_hard_challenges += 1
        warnings.append(f"{url}: hard access challenge ({budget.consecutive_hard_challenges}/{HARD_CHALLENGE_STOP})")
        return resource, page, warnings
    budget.consecutive_hard_challenges = 0
    if resource.status_code in {403, 429}:
        budget.rate_limit_events += 1
        warnings.append(f"{url}: HTTP {resource.status_code} with usable markup path; applying backoff")
        if rate_limit_attempt < 3 and budget.remaining() > 0:
            polite_delay(backoff_seconds(rate_limit_attempt + 1))
    return resource, page, warnings


def load_coverage_ledger(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("coverage ledger must be a JSON object")
    return payload


def resume_attempted_urls(ledger: dict[str, Any]) -> set[str]:
    attempted: set[str] = set()
    for page in ledger.get("pages") or []:
        if isinstance(page, dict) and page.get("url"):
            attempted.add(str(page["url"]))
    for path in ledger.get("search_paths") or []:
        attempted.add(canonical_product_url(str(path)))
    for row in ledger.get("product_details") or []:
        if isinstance(row, dict) and row.get("product_path"):
            attempted.add(canonical_product_url(str(row["product_path"])))
    return attempted


COVERAGE_CSV_COLUMNS: tuple[str, ...] = (
    "public_product_path",
    "canonical_url",
    "expansion",
    "product_label",
    "version",
    "rarity",
    "edition",
    "language",
    "finish",
    "printed_code",
    "from_status",
    "from_cents",
    "from_eur",
    "available_count",
    "discovered_via",
    "detail_attempted",
    "detail_ok",
    "fields_present",
    "fields_absent",
    "source_date",
)

FORBIDDEN_COVERAGE_CSV_COLUMNS: frozenset[str] = frozenset(
    FORBIDDEN_REFERENCE_CSV_COLUMNS
    | {"seller_name", "username", "location", "country", "cookie", "account"}
)


def compare_coverage_to_prior_corpus(
    product_paths: Sequence[str],
    *,
    prior_paths: Sequence[str],
    prior_expansion_count: int | None = None,
) -> dict[str, Any]:
    current, _ = count_path_duplicates(product_paths)
    prior, _ = count_path_duplicates(prior_paths)
    current_set = set(current)
    prior_set = set(prior)
    expansions = sorted(
        {
            normalize_public_path(path).strip("/").split("/")[4].replace("-", " ")
            for path in current
            if len(normalize_public_path(path).strip("/").split("/")) > 4
        }
    )
    return {
        "prior_unique_paths": len(prior_set),
        "current_unique_paths": len(current_set),
        "overlap": len(current_set & prior_set),
        "new_vs_prior": sorted(current_set - prior_set),
        "missing_vs_prior": sorted(prior_set - current_set),
        "prior_expansion_count": prior_expansion_count,
        "current_expansion_count": len(expansions),
        "note": (
            "Comparison is against the previous public Versions floor corpus "
            "(priced tiles only). Coverage may include From N/A public refs."
        ),
    }


def prior_paths_from_csv(path: Path) -> tuple[list[str], int]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    paths = [normalize_public_path(row.get("public_product_path") or "") for row in rows]
    expansions = {
        (row.get("expansion") or "").strip() for row in rows if (row.get("expansion") or "").strip()
    }
    unique, _ = count_path_duplicates(paths)
    return unique, len(expansions)


def build_coverage_rows(
    *,
    version_refs: Sequence[dict[str, Any]],
    search_paths: Sequence[str],
    product_details: Sequence[dict[str, Any]],
    source_date: str,
) -> list[dict[str, str]]:
    details_by_path = {
        normalize_public_path(str(row.get("product_path") or "")): row
        for row in product_details
        if isinstance(row, dict)
    }
    refs_by_path = {
        normalize_public_path(str(row.get("product_path") or "")): row
        for row in version_refs
        if isinstance(row, dict)
    }
    all_paths, _ = count_path_duplicates(
        list(refs_by_path) + [normalize_public_path(path) for path in search_paths]
    )
    rows: list[dict[str, str]] = []
    for path in sorted(all_paths, key=lambda item: item.lower()):
        ref = refs_by_path.get(path) or {}
        detail = details_by_path.get(path) or {}
        discovered = []
        if path in refs_by_path:
            discovered.append("versions")
        if path in {normalize_public_path(p) for p in search_paths}:
            discovered.append("search")
        cents = ref.get("price_cents")
        fields_present = detail.get("fields_present") if isinstance(detail.get("fields_present"), dict) else {}
        present_names = sorted(name for name, ok in fields_present.items() if ok) if fields_present else []
        absent_names = detail.get("fields_absent") if isinstance(detail.get("fields_absent"), list) else []
        rows.append(
            {
                "public_product_path": path,
                "canonical_url": canonical_product_url(path),
                "expansion": _blank(detail.get("expansion") or ref.get("expansion")),
                "product_label": _blank(detail.get("title") or ref.get("title")),
                "version": _blank(detail.get("version") or ref.get("version")),
                "rarity": _blank(detail.get("rarity") or ref.get("rarity")),
                "edition": _blank(detail.get("edition") or ref.get("edition")),
                "language": _blank(detail.get("language")),
                "finish": _blank(detail.get("finish")),
                "printed_code": _blank(detail.get("printed_code")),
                "from_status": _blank(ref.get("from_status") or ("search_only" if path not in refs_by_path else "")),
                "from_cents": "" if cents is None else str(int(cents)),
                "from_eur": "" if cents is None else format(cents_to_eur(int(cents)), "f"),
                "available_count": ""
                if ref.get("available_count") is None
                else str(int(ref["available_count"])),
                "discovered_via": "|".join(discovered) if discovered else "unknown",
                "detail_attempted": "yes" if detail else "no",
                "detail_ok": "yes" if detail and not detail.get("error") else ("no" if detail else ""),
                "fields_present": "|".join(present_names),
                "fields_absent": "|".join(str(x) for x in absent_names),
                "source_date": source_date,
            }
        )
    return rows


def export_coverage_csv(
    rows: Sequence[dict[str, str]],
    *,
    destination: Path | None = None,
) -> str:
    if any(col in FORBIDDEN_COVERAGE_CSV_COLUMNS for col in COVERAGE_CSV_COLUMNS):
        raise ValueError("coverage CSV column contract includes a forbidden field")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COVERAGE_CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in COVERAGE_CSV_COLUMNS})
    text = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return text


def compute_coverage_completion_indicators(
    *,
    ledger: dict[str, Any],
    coverage_rows: Sequence[dict[str, str]],
) -> dict[str, bool]:
    """Stable booleans for what the crawl actually proved (not blanket exhaustivity)."""
    announced = ledger.get("announced_versions")
    unique_path_count = len(coverage_rows)
    versions_counter_reached = (
        isinstance(announced, int) and unique_path_count == announced
    )
    search_pagination_complete = bool(ledger.get("search_pagination_complete"))
    if not coverage_rows:
        product_details_complete = False
    else:
        all_attempted = all(row.get("detail_attempted") == "yes" for row in coverage_rows)
        all_ok = all(row.get("detail_ok") == "yes" for row in coverage_rows)
        printed_complete = all((row.get("printed_code") or "").strip() for row in coverage_rows)
        product_details_complete = all_attempted and all_ok and printed_complete
    return {
        "versions_counter_reached": versions_counter_reached,
        "search_pagination_complete": search_pagination_complete,
        "product_details_complete": product_details_complete,
    }


def format_proven_coverage_scope(
    *,
    indicators: dict[str, bool],
    announced_versions: int | None,
    unique_paths: int,
    search_last_site: int | None,
    hard_challenges_at_stop: int | None,
) -> dict[str, str]:
    if indicators["versions_counter_reached"] and announced_versions is not None:
        versions_text = (
            f"public Versions panel complete at observed counter "
            f"{unique_paths}/{announced_versions}"
        )
    elif announced_versions is not None:
        versions_text = (
            f"public Versions panel partial: {unique_paths}/{announced_versions} observed"
        )
    else:
        versions_text = (
            f"public Versions panel: {unique_paths} unique paths observed "
            "(no announced counter)"
        )

    if indicators["search_pagination_complete"]:
        search_text = "Search site=N pagination reached announced total"
    elif search_last_site is not None and hard_challenges_at_stop:
        search_text = (
            f"Search site=1–{search_last_site} only; interrupted by "
            f"{hard_challenges_at_stop} consecutive hard challenges"
        )
    elif search_last_site is not None:
        search_text = f"Search site=1–{search_last_site} only; pagination incomplete"
    else:
        search_text = "Search pagination incomplete or not attempted"

    if indicators["product_details_complete"]:
        details_text = "product details and printed codes complete for all paths"
    else:
        details_text = "product details and printed codes incomplete"

    return {
        "versions_panel": versions_text,
        "search_pagination": search_text,
        "product_details": details_text,
    }


def build_coverage_manifest(
    *,
    ledger: dict[str, Any],
    coverage_rows: Sequence[dict[str, str]],
    comparison: dict[str, Any],
    budget: CoverageBudget | None = None,
    model_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pages = ledger.get("pages") or []
    ok_pages = [page for page in pages if isinstance(page, dict) and page.get("status") == "ok"]
    blocked = [page for page in pages if isinstance(page, dict) and page.get("status") == "blocked"]
    errors = [page for page in pages if isinstance(page, dict) and page.get("status") == "error"]
    unique_paths = [row["public_product_path"] for row in coverage_rows]
    field_presence: dict[str, int] = Counter()
    for row in coverage_rows:
        for name in (row.get("fields_present") or "").split("|"):
            if name:
                field_presence[name] += 1
    announced_versions = ledger.get("announced_versions")
    unique_path_count = len(unique_paths)
    completion_indicators = compute_coverage_completion_indicators(
        ledger=ledger,
        coverage_rows=coverage_rows,
    )
    budget_payload = ledger.get("budget") if isinstance(ledger.get("budget"), dict) else {}
    hard_challenges_at_stop = (
        budget.consecutive_hard_challenges
        if budget is not None
        else budget_payload.get("consecutive_hard_challenges_at_stop")
    )
    proven_coverage_scope = format_proven_coverage_scope(
        indicators=completion_indicators,
        announced_versions=announced_versions if isinstance(announced_versions, int) else None,
        unique_paths=unique_path_count,
        search_last_site=ledger.get("search_last_site"),
        hard_challenges_at_stop=hard_challenges_at_stop,
    )
    return {
        "title": "Cardmarket Blue-Eyes White Dragon public coverage manifest",
        "generated_at": utc_now_iso(),
        "timezone": ledger.get("timezone") or "UTC",
        "started_at": ledger.get("started_at"),
        "finished_at": ledger.get("finished_at") or utc_now_iso(),
        "canonical_routes": {
            "versions_en": VERSIONS_EN_URL,
            "versions_fr": VERSIONS_FR_URL,
            "card_hub_en": CARD_HUB_EN_URL,
            "search_en": SEARCH_EN_URL,
            "search_pagination_param": "site",
        },
        "pages_attempted": len(pages),
        "pages_succeeded": len(ok_pages),
        "pages_blocked": len(blocked),
        "pages_error": len(errors),
        "announced_versions_counter": announced_versions,
        "unique_public_product_paths": unique_path_count,
        "completion_indicators": completion_indicators,
        "proven_coverage_scope": proven_coverage_scope,
        "search_last_site": ledger.get("search_last_site"),
        "duplicates_skipped": ledger.get("duplicates_skipped"),
        "errors": ledger.get("errors") or errors,
        "stop_reason": ledger.get("stop_reason"),
        "budget": {
            "max_navigations": (budget.max_navigations if budget else MAX_COVERAGE_NAVIGATIONS),
            "navigations_used": (budget.navigations if budget else len(pages)),
            "rate_limit_events": (budget.rate_limit_events if budget else None),
            "consecutive_hard_challenges_at_stop": (
                budget.consecutive_hard_challenges if budget else None
            ),
        },
        "field_presence_counts": dict(field_presence),
        "comparison_to_prior_175_102": comparison,
        "model_evidence": model_evidence
        or {
            "worker": "cursor-grok",
            "model": "grok-4.5-high",
            "transport": "direct worktree; CloakBrowser via supersocks-url-scraper fetch_with_browser",
            "no_login_captcha_proxy_fingerprint_spoof": True,
        },
        "privacy": {
            "seller_fields": "never collected/published",
            "raw_html": "memory-only; private ledger under gitignored runs/",
            "published_artifacts": ["sanitized CSV", "coverage manifest JSON/Markdown"],
        },
        "limits": [
            "Coverage scope is proven per completion_indicators; do not read this as Versions + Search exhaustive.",
            "Offer tables remain first-page only unless a future task paginates offers.",
            "Printed collector numbers are recorded only when HTML exposes them; absence is not inferred.",
        ],
    }


def render_coverage_manifest_markdown(manifest: dict[str, Any]) -> str:
    comparison = manifest.get("comparison_to_prior_175_102") or {}
    budget = manifest.get("budget") or {}
    indicators = manifest.get("completion_indicators") or {}
    scope = manifest.get("proven_coverage_scope") or {}
    lines = [
        "# Cardmarket Blue-Eyes public coverage manifest",
        "",
        f"- Generated: `{manifest.get('generated_at')}` ({manifest.get('timezone')})",
        f"- Stop reason: **{manifest.get('stop_reason')}**",
        f"- Pages attempted/succeeded: **{manifest.get('pages_attempted')}** / **{manifest.get('pages_succeeded')}**",
        f"- Announced Versions counter: **{manifest.get('announced_versions_counter')}**",
        f"- Unique public product paths: **{manifest.get('unique_public_product_paths')}**",
        f"- Navigations used: **{budget.get('navigations_used')}** / **{budget.get('max_navigations')}**",
        "",
        "## Completion indicators",
        "",
        f"- `versions_counter_reached`: **{indicators.get('versions_counter_reached')}**",
        f"- `search_pagination_complete`: **{indicators.get('search_pagination_complete')}**",
        f"- `product_details_complete`: **{indicators.get('product_details_complete')}**",
        "",
        "## Proven coverage scope (not Versions + Search exhaustive)",
        "",
        f"- **Versions panel:** {scope.get('versions_panel', 'unknown')}.",
        f"- **Search pagination:** {scope.get('search_pagination', 'unknown')}.",
        f"- **Product details / printed codes:** {scope.get('product_details', 'unknown')}.",
        "",
        "This crawl does **not** claim the full historical official Konami catalog, deleted listings,",
        "private inventory, or complete offer pagination.",
        "",
        "## Comparison to prior corpus (175 paths / 102 expansions)",
        "",
        f"- Prior unique paths: **{comparison.get('prior_unique_paths')}**",
        f"- Current unique paths: **{comparison.get('current_unique_paths')}**",
        f"- Overlap: **{comparison.get('overlap')}**",
        f"- New vs prior: **{len(comparison.get('new_vs_prior') or [])}**",
        f"- Missing vs prior: **{len(comparison.get('missing_vs_prior') or [])}**",
        f"- Current expansions: **{comparison.get('current_expansion_count')}** (prior {comparison.get('prior_expansion_count')})",
        "",
    ]
    return "\n".join(lines)


def crawl_public_blue_eyes_coverage(
    *,
    delay_seconds: float = MIN_PRODUCT_DELAY_SECONDS,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 9000,
    max_navigations: int = MAX_COVERAGE_NAVIGATIONS,
    include_fr_versions: bool = True,
    fetch_product_details: bool = True,
    search_query: str = DEFAULT_SEARCH_QUERY,
    resume_from: dict[str, Any] | None = None,
    max_search_pages: int = 30,
) -> dict[str, Any]:
    """Crawl public Versions + Search pagination, then optional product details.

    Stop proofs: empty search page, repeated search page, announced total reached,
    budget exhausted, or HARD_CHALLENGE_STOP consecutive hard challenges.
    """
    if delay_seconds < MIN_PRODUCT_DELAY_SECONDS:
        raise ValueError(f"delay_seconds must be >= {MIN_PRODUCT_DELAY_SECONDS}")
    if max_navigations < 1 or max_navigations > MAX_COVERAGE_NAVIGATIONS:
        raise ValueError(f"max_navigations must be between 1 and {MAX_COVERAGE_NAVIGATIONS}")

    budget = CoverageBudget(max_navigations=max_navigations)
    ledger = CoverageLedger()
    if resume_from:
        ledger.warnings.append("resuming from prior coverage ledger")
        for key in ("version_refs", "search_paths", "product_details", "pages", "errors"):
            prior_val = resume_from.get(key)
            if isinstance(prior_val, list):
                getattr(ledger, key).extend(prior_val)
        if resume_from.get("announced_versions") is not None:
            ledger.announced_versions = resume_from.get("announced_versions")
        ledger.search_pagination_complete = bool(resume_from.get("search_pagination_complete"))
        if resume_from.get("search_last_site") is not None:
            ledger.search_last_site = int(resume_from["search_last_site"])
        budget.navigations = len([p for p in ledger.pages if isinstance(p, dict)])

    attempted = resume_attempted_urls(resume_from) if resume_from else set()
    stop_reason: str | None = None

    def stop_for_challenges() -> bool:
        nonlocal stop_reason
        if budget.consecutive_hard_challenges >= HARD_CHALLENGE_STOP:
            stop_reason = f"hard_challenges_x{HARD_CHALLENGE_STOP}"
            return True
        return False

    def stop_for_budget() -> bool:
        nonlocal stop_reason
        if budget.remaining() <= 0:
            stop_reason = "budget_exhausted"
            return True
        return False

    # --- Versions routes (EN, optional FR) ---
    version_urls = [VERSIONS_EN_URL]
    if include_fr_versions:
        version_urls.append(VERSIONS_FR_URL)
    for url in version_urls:
        if url in attempted or stop_for_budget() or stop_reason:
            continue
        if ledger.pages:
            polite_delay(delay_seconds)
        resource, page, warnings = fetch_coverage_page(
            url,
            budget=budget,
            browser_fallback=browser_fallback,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
        )
        page["route"] = "versions"
        ledger.pages.append(page)
        ledger.routes_attempted.append(url)
        ledger.warnings.extend(warnings)
        attempted.add(url)
        if page.get("challenge") and stop_for_challenges():
            break
        if resource is None:
            ledger.errors.append({"url": url, "error": "fetch_failed"})
            continue
        announced = extract_announced_versions_count(resource.text)
        if announced is not None:
            ledger.announced_versions = announced
            page["announced_versions"] = announced
        refs = parse_version_product_refs(resource.text, source_url=url)
        page["parsed"] = len(refs)
        # Merge refs by path (EN wins on first insert; FR can add paths).
        existing = {normalize_public_path(str(r.get("product_path") or "")) for r in ledger.version_refs}
        for ref in refs:
            path = normalize_public_path(str(ref.get("product_path") or ""))
            if path and path not in existing:
                ledger.version_refs.append(ref)
                existing.add(path)
        if "/Versions" in url and not refs:
            ledger.warnings.append(f"{url}: Versions page returned no product refs")

    if stop_reason is None and ledger.announced_versions is not None:
        unique_version_paths = {
            normalize_public_path(str(r.get("product_path") or "")) for r in ledger.version_refs
        }
        if len(unique_version_paths) >= int(ledger.announced_versions):
            # Versions catalog exhausted relative to announced counter; still run Search.
            ledger.warnings.append(
                "versions_unique_reached_announced_counter:"
                f"{len(unique_version_paths)}/{ledger.announced_versions}"
            )

    # --- Search pagination via public site=N ---
    previous_paths: set[str] | None = None
    search_stop: str | None = None
    if ledger.search_pagination_complete:
        ledger.warnings.append("search_pagination_already_complete_on_resume")
    else:
        start_site = 1
        if ledger.search_last_site is not None:
            start_site = max(1, int(ledger.search_last_site) + 1)
        for site in range(start_site, max_search_pages + 1):
            if stop_reason or stop_for_budget():
                break
            url = build_search_page_url(search_query, site)
            if url in attempted:
                ledger.search_last_site = site
                continue
            polite_delay(delay_seconds)
            resource, page, warnings = fetch_coverage_page(
                url,
                budget=budget,
                browser_fallback=browser_fallback,
                browser_post_load_wait_ms=browser_post_load_wait_ms,
            )
            page["route"] = "search"
            page["search_site"] = site
            ledger.pages.append(page)
            ledger.routes_attempted.append(url)
            ledger.warnings.extend(warnings)
            attempted.add(url)
            ledger.search_last_site = site
            if page.get("challenge"):
                search_stop = "search_hard_challenge"
                if stop_for_challenges():
                    break
                # Do not treat challenge markup as an empty results page.
                continue
            if resource is None:
                ledger.errors.append({"url": url, "error": "fetch_failed"})
                search_stop = "search_fetch_failed"
                break
            parsed = parse_search_results_page(resource.text, source_url=url)
            page["parsed"] = len(parsed["product_paths_exact"])
            page["page_of"] = parsed.get("page_of")
            page["related_non_exact_count"] = len(parsed.get("related_non_exact") or [])
            for path in parsed["product_paths_exact"]:
                ledger.search_paths.append(path)
            page_of = parsed.get("page_of") or {}
            if isinstance(page_of.get("total"), int):
                # Persist announced search total so resume does not walk forever
                # after challenge pages without a Page X of Y marker.
                ledger.warnings.append(f"search_announced_total:{page_of['total']}")
                max_search_pages = min(max_search_pages, int(page_of["total"]))
            early = should_stop_search_pagination(
                parsed=parsed, previous_paths=previous_paths, site=site
            )
            previous_paths = set(parsed.get("product_paths_all") or [])
            if early:
                search_stop = early
                break
            if isinstance(page_of.get("current"), int) and isinstance(page_of.get("total"), int):
                if page_of["current"] >= page_of["total"]:
                    search_stop = "search_announced_total_reached"
                    break
        if search_stop in {
            "search_announced_total_reached",
            "search_page_empty",
            "search_page_repeated",
            "search_site_beyond_announced_total",
            "search_page_beyond_announced_total",
        }:
            ledger.search_pagination_complete = True
        if search_stop and stop_reason is None:
            ledger.warnings.append(f"search_pagination_stop:{search_stop}")

    # Deduplicate discovery sets
    version_paths = [
        normalize_public_path(str(r.get("product_path") or "")) for r in ledger.version_refs
    ]
    search_unique, search_dupes = count_path_duplicates(ledger.search_paths)
    ledger.search_paths = search_unique
    all_paths, total_dupes = count_path_duplicates(version_paths + search_unique)
    ledger_dict_preview = {"duplicates_skipped": total_dupes + search_dupes}

    # --- Product detail attempts ---
    if fetch_product_details and stop_reason is None:
        for path in all_paths:
            if stop_for_budget() or stop_reason:
                break
            url = canonical_product_url(path)
            if url in attempted:
                continue
            polite_delay(delay_seconds)
            resource, page, warnings = fetch_coverage_page(
                url,
                budget=budget,
                browser_fallback=browser_fallback,
                browser_post_load_wait_ms=browser_post_load_wait_ms,
            )
            page["route"] = "product_detail"
            page["product_path"] = path
            ledger.pages.append(page)
            ledger.routes_attempted.append(url)
            ledger.warnings.extend(warnings)
            attempted.add(url)
            if page.get("challenge"):
                ledger.product_details.append(
                    {
                        "record_kind": "product_detail",
                        "product_path": path,
                        "source_url": url,
                        "error": "hard_challenge",
                        "fields_present": {},
                        "fields_absent": [
                            "printed_code",
                            "expansion",
                            "version",
                            "rarity",
                            "edition",
                            "language",
                            "finish",
                        ],
                    }
                )
                if stop_for_challenges():
                    break
                polite_delay(backoff_seconds(budget.consecutive_hard_challenges + 1))
                continue
            if resource is None:
                ledger.errors.append({"url": url, "error": "fetch_failed"})
                ledger.product_details.append(
                    {
                        "record_kind": "product_detail",
                        "product_path": path,
                        "source_url": url,
                        "error": "fetch_failed",
                        "fields_present": {},
                        "fields_absent": [
                            "printed_code",
                            "expansion",
                            "version",
                            "rarity",
                            "edition",
                            "language",
                            "finish",
                        ],
                    }
                )
                continue
            detail = parse_product_public_details(
                resource.text, product_path=path, source_url=url
            )
            page["parsed"] = 1
            page["fields_present"] = detail.get("fields_present")
            ledger.product_details.append(detail)

    if stop_reason is None:
        if ledger.announced_versions is not None and len(all_paths) >= int(ledger.announced_versions):
            stop_reason = "announced_versions_counter_reached_with_search_and_versions"
        elif search_stop == "search_announced_total_reached":
            stop_reason = "search_pagination_complete_and_versions_parsed"
        elif search_stop:
            stop_reason = search_stop
        else:
            stop_reason = "completed_scheduled_routes"
    elif (
        stop_reason.startswith("hard_challenges")
        and ledger.announced_versions is not None
        and len(all_paths) >= int(ledger.announced_versions)
    ):
        stop_reason = (
            "versions_announced_counter_reached_then_"
            f"{stop_reason}_during_search_or_product_details"
        )

    ledger.stop_reason = stop_reason
    payload = ledger.to_dict()
    payload["duplicates_skipped"] = ledger_dict_preview["duplicates_skipped"]
    payload["unique_product_paths"] = all_paths
    payload["budget"] = {
        "max_navigations": budget.max_navigations,
        "navigations_used": budget.navigations,
        "rate_limit_events": budget.rate_limit_events,
        "consecutive_hard_challenges_at_stop": budget.consecutive_hard_challenges,
    }
    payload["status"] = (
        "blocked"
        if stop_reason and stop_reason.startswith("hard_challenges")
        else ("partial" if ledger.errors or any(p.get("status") == "error" for p in ledger.pages) else "ok")
    )
    payload["_budget_obj_navigations"] = budget.navigations
    payload["_budget_obj"] = budget
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via temp+rename so a crash cannot leave a half-written ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _strip_seller_like(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in SELLER_LIKE_KEYS or any(tok in key_l for tok in ("seller", "username", "cookie")):
                continue
            cleaned[str(key)] = _strip_seller_like(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_seller_like(item) for item in value]
    if isinstance(value, str) and len(value) > 5000 and ("<html" in value.lower() or "articleRow" in value):
        return "[redacted-markup]"
    return value


def sanitize_deep_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop seller/HTML fields before any private ledger write or public export."""
    cleaned = _strip_seller_like(
        {key: value for key, value in payload.items() if not str(key).startswith("_")}
    )
    if not isinstance(cleaned, dict):
        raise ValueError("deep ledger must sanitize to a JSON object")
    # Defense in depth: never persist raw page bodies or helper objects.
    cleaned.pop("html", None)
    cleaned.pop("raw_html", None)
    cleaned.pop("markup", None)
    cleaned.pop("_budget_obj", None)
    return cleaned


def load_exact_blue_eyes_paths_from_coverage_csv(path: Path) -> list[str]:
    """Load the exact 177 BEWD corpus; refuse overcount and White-Phantom-Beast paths."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    paths: list[str] = []
    for row in rows:
        raw = normalize_public_path(row.get("public_product_path") or "")
        if not raw:
            continue
        if not is_exact_blue_eyes_white_dragon_path(raw):
            raise ValueError(f"non-exact Blue-Eyes path refused: {raw}")
        paths.append(raw)
    unique, dupes = count_path_duplicates(paths)
    if dupes:
        raise ValueError(f"coverage CSV contains {dupes} duplicate path(s)")
    if len(unique) != EXPECTED_EXACT_BLUE_EYES_PATHS:
        raise ValueError(
            f"exact Blue-Eyes corpus must be {EXPECTED_EXACT_BLUE_EYES_PATHS}, got {len(unique)}"
        )
    # Deterministic order for the enrichment queue.
    return sorted(unique, key=lambda item: item.lower())


def seed_deep_enrichment_queue(
    paths: Sequence[str],
    *,
    coverage_rows: Sequence[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if len(paths) != EXPECTED_EXACT_BLUE_EYES_PATHS:
        raise ValueError(
            f"deep enrichment queue requires exactly {EXPECTED_EXACT_BLUE_EYES_PATHS} paths"
        )
    ordered = sorted(
        {normalize_public_path(path) for path in paths if is_exact_blue_eyes_white_dragon_path(path)},
        key=lambda item: item.lower(),
    )
    if len(ordered) != EXPECTED_EXACT_BLUE_EYES_PATHS:
        raise ValueError(
            f"after exact-path filter expected {EXPECTED_EXACT_BLUE_EYES_PATHS}, got {len(ordered)}"
        )
    by_path = {
        normalize_public_path(row.get("public_product_path") or ""): row
        for row in (coverage_rows or [])
        if isinstance(row, dict)
    }
    queue: list[dict[str, Any]] = []
    for path in ordered:
        prior = by_path.get(path) or {}
        from_cents = prior.get("from_cents")
        available = prior.get("available_count")
        queue.append(
            {
                "product_path": path,
                "canonical_url": canonical_product_url(path),
                "status": "pending",
                "attempts": 0,
                "last_error": None,
                "expanded_at": None,
                "expansion": prior.get("expansion") or None,
                "version": prior.get("version") or None,
                "rarity": prior.get("rarity") or None,
                "printed_code": prior.get("printed_code") or None,
                "from_status": prior.get("from_status") or None,
                "from_cents": int(from_cents) if str(from_cents).isdigit() else None,
                "available_count": int(available) if str(available).isdigit() else None,
                "price_trend_cents": None,
                "avg_1d_cents": None,
                "avg_7d_cents": None,
                "avg_30d_cents": None,
                "fields_present": {},
                "fields_absent": [],
                "language_counts": None,
                "condition_counts": None,
                "detail_ok": False,
            }
        )
    return queue


def deep_queue_status_counts(queue: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in sorted(DEEP_ENRICHMENT_STATUSES)}
    for item in queue:
        status = str(item.get("status") or "pending")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
    counts["total"] = len(queue)
    counts["attempted"] = sum(1 for item in queue if int(item.get("attempts") or 0) > 0)
    counts["succeeded"] = counts.get("ok", 0)
    return counts


def merge_detail_into_queue_item(item: dict[str, Any], detail: dict[str, Any], *, status: str) -> None:
    item["status"] = status
    item["attempts"] = int(item.get("attempts") or 0) + 1
    item["expanded_at"] = utc_now_iso()
    item["last_error"] = detail.get("error")
    for key in (
        "expansion",
        "version",
        "rarity",
        "edition",
        "language",
        "finish",
        "printed_code",
        "title",
        "from_status",
        "from_cents",
        "available_count",
        "price_trend_cents",
        "avg_1d_cents",
        "avg_7d_cents",
        "avg_30d_cents",
        "fields_present",
        "fields_absent",
        "language_counts",
        "condition_counts",
        "article_row_count_first_page",
    ):
        if key in detail and detail[key] is not None:
            item[key] = detail[key]
    item["detail_ok"] = status == "ok" and not detail.get("error")


DEEP_ENRICHMENT_CSV_COLUMNS: tuple[str, ...] = (
    "public_product_path",
    "canonical_url",
    "enrichment_status",
    "attempts",
    "expansion",
    "product_label",
    "version",
    "rarity",
    "edition",
    "printed_code",
    "from_status",
    "from_cents",
    "from_eur",
    "available_count",
    "price_trend_cents",
    "price_trend_eur",
    "avg_1d_cents",
    "avg_1d_eur",
    "avg_7d_cents",
    "avg_7d_eur",
    "avg_30d_cents",
    "avg_30d_eur",
    "detail_ok",
    "fields_present",
    "fields_absent",
    "source_date",
)

FORBIDDEN_DEEP_CSV_COLUMNS: frozenset[str] = frozenset(
    FORBIDDEN_COVERAGE_CSV_COLUMNS
    | {"seller_name", "username", "location", "country", "cookie", "account", "offer_rows"}
)


def _cents_cell(value: object) -> str:
    if value is None or value == "":
        return ""
    return str(int(value))


def _eur_cell(value: object) -> str:
    if value is None or value == "":
        return ""
    return format(cents_to_eur(int(value)), "f")


def build_deep_enrichment_rows(
    queue: Sequence[dict[str, Any]],
    *,
    source_date: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in sorted(queue, key=lambda row: str(row.get("product_path") or "").lower()):
        path = normalize_public_path(str(item.get("product_path") or ""))
        present = item.get("fields_present") if isinstance(item.get("fields_present"), dict) else {}
        present_names = sorted(name for name, ok in present.items() if ok) if present else []
        absent = item.get("fields_absent") if isinstance(item.get("fields_absent"), list) else []
        rows.append(
            {
                "public_product_path": path,
                "canonical_url": canonical_product_url(path),
                "enrichment_status": _blank(item.get("status")),
                "attempts": str(int(item.get("attempts") or 0)),
                "expansion": _blank(item.get("expansion")),
                "product_label": _blank(item.get("title")),
                "version": _blank(item.get("version")),
                "rarity": _blank(item.get("rarity")),
                "edition": _blank(item.get("edition")),
                "printed_code": _blank(item.get("printed_code")),
                "from_status": _blank(item.get("from_status")),
                "from_cents": _cents_cell(item.get("from_cents")),
                "from_eur": _eur_cell(item.get("from_cents")),
                "available_count": _cents_cell(item.get("available_count")),
                "price_trend_cents": _cents_cell(item.get("price_trend_cents")),
                "price_trend_eur": _eur_cell(item.get("price_trend_cents")),
                "avg_1d_cents": _cents_cell(item.get("avg_1d_cents")),
                "avg_1d_eur": _eur_cell(item.get("avg_1d_cents")),
                "avg_7d_cents": _cents_cell(item.get("avg_7d_cents")),
                "avg_7d_eur": _eur_cell(item.get("avg_7d_cents")),
                "avg_30d_cents": _cents_cell(item.get("avg_30d_cents")),
                "avg_30d_eur": _eur_cell(item.get("avg_30d_cents")),
                "detail_ok": "yes" if item.get("detail_ok") else "no",
                "fields_present": "|".join(present_names),
                "fields_absent": "|".join(str(x) for x in absent),
                "source_date": source_date,
            }
        )
    return rows


def export_deep_enrichment_csv(
    rows: Sequence[dict[str, str]],
    *,
    destination: Path | None = None,
) -> str:
    if any(col in FORBIDDEN_DEEP_CSV_COLUMNS for col in DEEP_ENRICHMENT_CSV_COLUMNS):
        raise ValueError("deep enrichment CSV column contract includes a forbidden field")
    if any(col.lower() in {"seller", "username", "cookie"} for col in DEEP_ENRICHMENT_CSV_COLUMNS):
        raise ValueError("seller-like column refused")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(DEEP_ENRICHMENT_CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in DEEP_ENRICHMENT_CSV_COLUMNS})
    text = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return text


def build_deep_enrichment_manifest(
    ledger: dict[str, Any],
    *,
    rows: Sequence[dict[str, str]],
    model_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = deep_queue_status_counts(ledger.get("queue") or [])
    field_presence: dict[str, int] = Counter()
    for row in rows:
        for name in (row.get("fields_present") or "").split("|"):
            if name:
                field_presence[name] += 1
        for metric in (
            "from_cents",
            "available_count",
            "price_trend_cents",
            "avg_1d_cents",
            "avg_7d_cents",
            "avg_30d_cents",
            "printed_code",
        ):
            if (row.get(metric) or "").strip():
                field_presence[metric] = field_presence.get(metric, 0) + 1
    budget = ledger.get("budget") if isinstance(ledger.get("budget"), dict) else {}
    pages = ledger.get("pages") or []
    return {
        "title": "Cardmarket Blue-Eyes White Dragon public deep-enrichment manifest",
        "generated_at": utc_now_iso(),
        "timezone": ledger.get("timezone") or "UTC",
        "started_at": ledger.get("started_at"),
        "finished_at": ledger.get("finished_at") or utc_now_iso(),
        "corpus": {
            "total": EXPECTED_EXACT_BLUE_EYES_PATHS,
            "unique_paths": len(rows),
            "white_phantom_beast_excluded": True,
            "versions_panel": "complete 177/177 (prior coverage; not re-fetched in deep pass)",
        },
        "counts": counts,
        "attempted": counts.get("attempted", 0),
        "succeeded": counts.get("succeeded", 0),
        "challenges": counts.get("challenge", 0),
        "pending": counts.get("pending", 0),
        "errors": counts.get("error", 0),
        "search_last_site": ledger.get("search_last_site"),
        "search_pagination_complete": bool(ledger.get("search_pagination_complete")),
        "stop_reason": ledger.get("stop_reason"),
        "pages_attempted": len(pages),
        "navigations_used": budget.get("navigations_used"),
        "budget": budget,
        "field_presence_counts": dict(sorted(field_presence.items())),
        "scope_qualification": {
            "versions_complete": True,
            "details_enriched_partially": counts.get("succeeded", 0) < EXPECTED_EXACT_BLUE_EYES_PATHS,
            "offers_non_exhaustive": True,
            "live_product_details_succeeded": counts.get("succeeded", 0),
            "live_search_challenge_navigations": sum(
                1
                for page in pages
                if isinstance(page, dict)
                and page.get("challenge")
                and str(page.get("route") or "").startswith("search")
            ),
        },
        "baseline_reused_fields": {
            "from_cents": field_presence.get("from_cents", 0),
            "available_count": field_presence.get("available_count", 0),
            "note": (
                "from_cents / available_count values on pending rows are reused from the prior "
                "coverage CSV baseline seed; they were not newly extracted by live product-detail "
                "fetches in this deep-enrichment window "
                f"(live detail successes={counts.get('succeeded', 0)})."
            ),
        },
        "model_evidence": model_evidence
        or {
            "worker": "cursor-grok",
            "model": "Cursor Grok 4.5 (grok-4.5-high)",
            "transport": "direct worktree; CloakBrowser via supersocks-url-scraper fetch_with_browser",
            "no_login_captcha_proxy_fingerprint_spoof": True,
            "no_parallelism": True,
        },
        "privacy": {
            "seller_fields": "never collected/published",
            "raw_html": "memory-only; private ledger sanitized on write under gitignored runs/",
            "published_artifacts": ["sanitized deep-enrichment CSV", "manifest JSON/Markdown"],
        },
        "limits": [
            "Versions catalog completeness is inherited from the prior 177/177 coverage pass.",
            "Product details may be only partially enriched within the live navigation budget.",
            "Offer tables remain first-page only; language/condition aggregates are in-memory counts without seller rows.",
            "Printed collector numbers are recorded only when HTML exposes an explicit set-code token.",
            "Deep CSV From/availability cells on pending rows are baseline coverage seeds, not live deep-pass extractions.",
        ],
    }


def render_deep_enrichment_manifest_markdown(manifest: dict[str, Any]) -> str:
    counts = manifest.get("counts") or {}
    scope = manifest.get("scope_qualification") or {}
    budget = manifest.get("budget") or {}
    lines = [
        "# Cardmarket Blue-Eyes public deep-enrichment manifest",
        "",
        f"- Generated: `{manifest.get('generated_at')}` ({manifest.get('timezone')})",
        f"- Stop reason: **{manifest.get('stop_reason')}**",
        f"- Corpus total: **{manifest.get('corpus', {}).get('total')}**",
        f"- Attempted / succeeded / challenges / pending / errors: "
        f"**{manifest.get('attempted')}** / **{manifest.get('succeeded')}** / "
        f"**{manifest.get('challenges')}** / **{manifest.get('pending')}** / "
        f"**{manifest.get('errors')}**",
        f"- Navigations used: **{budget.get('navigations_used')}** / **{budget.get('max_navigations')}**",
        f"- Search last site: **{manifest.get('search_last_site')}**",
        "",
        "## Scope qualification",
        "",
        f"- Versions complete: **{scope.get('versions_complete')}** (177/177 prior coverage).",
        f"- Details enriched partially: **{scope.get('details_enriched_partially')}**.",
        f"- Live product-detail successes: **{scope.get('live_product_details_succeeded', manifest.get('succeeded'))}**.",
        f"- Live Search challenge navigations: **{scope.get('live_search_challenge_navigations')}**.",
        f"- Offers non-exhaustive: **{scope.get('offers_non_exhaustive')}**.",
        "",
        "## Baseline reuse (not live deep extractions)",
        "",
    ]
    baseline = manifest.get("baseline_reused_fields") or {}
    lines.append(
        f"- `from_cents` / `available_count` on seeded pending rows: "
        f"**{baseline.get('from_cents')}** / **{baseline.get('available_count')}** "
        f"— {baseline.get('note')}"
    )
    lines.extend(["", "## Field presence", ""])
    for name, count in sorted((manifest.get("field_presence_counts") or {}).items()):
        lines.append(f"- `{name}`: **{count}**")
    lines.append("")
    lines.append("Queue status snapshot:")
    for key in ("pending", "ok", "challenge", "error", "total"):
        lines.append(f"- `{key}`: **{counts.get(key)}**")
    lines.append("")
    return "\n".join(lines)


def crawl_deep_enrichment(
    *,
    coverage_csv: Path,
    checkpoint_path: Path,
    delay_seconds: float = MIN_DEEP_DELAY_SECONDS,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 9000,
    max_navigations: int = MAX_DEEP_ENRICHMENT_NAVIGATIONS,
    search_start_site: int = 8,
    max_search_pages: int = 30,
    search_query: str = DEFAULT_SEARCH_QUERY,
    fetch_product_details: bool = True,
    resume_from: dict[str, Any] | None = None,
    first_access_cooldown_seconds: float = FIRST_ACCESS_COOLDOWN_SECONDS,
) -> dict[str, Any]:
    """Resume Search at site=N then enrich product details with atomic checkpoints.

    Safety rails: no parallelism, delay >= 8s + jitter, stop after 2 consecutive
    hard challenges, and a single bounded cooldown+retry if the first access is
    challenged.
    """
    if delay_seconds < MIN_DEEP_DELAY_SECONDS:
        raise ValueError(f"delay_seconds must be >= {MIN_DEEP_DELAY_SECONDS}")
    if max_navigations < 1 or max_navigations > MAX_DEEP_ENRICHMENT_NAVIGATIONS:
        raise ValueError(
            f"max_navigations must be between 1 and {MAX_DEEP_ENRICHMENT_NAVIGATIONS}"
        )

    with coverage_csv.open(encoding="utf-8", newline="") as handle:
        coverage_rows = list(csv.DictReader(handle))
    corpus_paths = load_exact_blue_eyes_paths_from_coverage_csv(coverage_csv)

    if resume_from:
        ledger = sanitize_deep_ledger(dict(resume_from))
        ledger.setdefault("warnings", [])
        if isinstance(ledger["warnings"], list):
            ledger["warnings"] = list(ledger["warnings"]) + ["resuming deep enrichment ledger"]
        queue = list(ledger.get("queue") or [])
        if len(queue) != EXPECTED_EXACT_BLUE_EYES_PATHS:
            raise ValueError("resume ledger queue must contain exactly 177 paths")
        # Never re-attempt ok items; keep deterministic path order.
        queue = sorted(queue, key=lambda item: str(item.get("product_path") or "").lower())
        for item in queue:
            if not is_exact_blue_eyes_white_dragon_path(str(item.get("product_path") or "")):
                raise ValueError("resume ledger contains non-exact Blue-Eyes path")
            if item.get("status") not in DEEP_ENRICHMENT_STATUSES:
                item["status"] = "pending"
    else:
        queue = seed_deep_enrichment_queue(corpus_paths, coverage_rows=coverage_rows)
        ledger = {
            "started_at": utc_now_iso(),
            "timezone": "UTC",
            "queue": queue,
            "pages": [],
            "search_paths": [],
            "warnings": [],
            "errors": [],
            "search_last_site": search_start_site - 1,
            "search_pagination_complete": False,
            "announced_versions": EXPECTED_EXACT_BLUE_EYES_PATHS,
            "corpus_paths": corpus_paths,
        }

    budget = CoverageBudget(max_navigations=max_navigations)
    prior_pages = [p for p in (ledger.get("pages") or []) if isinstance(p, dict)]
    # Fresh budget for this run; prior pages remain for audit but do not consume
    # the new deep-enrichment navigation allowance.
    budget.navigations = 0
    stop_reason: str | None = None
    first_access_consumed = bool(ledger.get("first_access_retry_consumed"))
    navigations_this_run = 0

    def checkpoint() -> None:
        snapshot = sanitize_deep_ledger(
            {
                **ledger,
                "queue": queue,
                "finished_at": utc_now_iso(),
                "stop_reason": stop_reason or ledger.get("stop_reason"),
                "budget": {
                    "max_navigations": budget.max_navigations,
                    "navigations_used": budget.navigations,
                    "rate_limit_events": budget.rate_limit_events,
                    "consecutive_hard_challenges_at_stop": budget.consecutive_hard_challenges,
                },
                "first_access_retry_consumed": first_access_consumed,
                "status_counts": deep_queue_status_counts(queue),
            }
        )
        atomic_write_json(checkpoint_path, snapshot)

    def stop_for_challenges() -> bool:
        nonlocal stop_reason
        if budget.consecutive_hard_challenges >= DEEP_HARD_CHALLENGE_STOP:
            stop_reason = f"hard_challenges_x{DEEP_HARD_CHALLENGE_STOP}"
            return True
        return False

    def stop_for_budget() -> bool:
        nonlocal stop_reason
        if budget.remaining() <= 0:
            stop_reason = "budget_exhausted"
            return True
        return False

    def fetch_once(url: str, *, route: str, extra: dict[str, Any] | None = None) -> tuple[Any, dict[str, Any], list[str]]:
        nonlocal navigations_this_run, first_access_consumed, stop_reason
        if navigations_this_run > 0 or prior_pages:
            polite_delay(delay_seconds)
        resource, page, warnings = fetch_coverage_page(
            url,
            budget=budget,
            browser_fallback=browser_fallback,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
        )
        navigations_this_run += 1
        page["route"] = route
        if extra:
            page.update(extra)
        ledger.setdefault("pages", []).append(page)
        ledger.setdefault("warnings", []).extend(warnings)

        # First-access special case: one bounded cooldown then a second try; then stop.
        if (
            navigations_this_run == 1
            and page.get("challenge")
            and not first_access_consumed
        ):
            first_access_consumed = True
            ledger.setdefault("warnings", []).append(
                f"first_access_challenge_cooldown_{first_access_cooldown_seconds}s"
            )
            checkpoint()
            polite_delay(first_access_cooldown_seconds, jitter_ratio=0.05)
            if stop_for_budget():
                checkpoint()
                return resource, page, warnings
            resource2, page2, warnings2 = fetch_coverage_page(
                url,
                budget=budget,
                browser_fallback=browser_fallback,
                browser_post_load_wait_ms=browser_post_load_wait_ms,
            )
            navigations_this_run += 1
            page2["route"] = route
            page2["first_access_retry"] = True
            if extra:
                page2.update(extra)
            ledger.setdefault("pages", []).append(page2)
            ledger.setdefault("warnings", []).extend(warnings2)
            checkpoint()
            if page2.get("challenge"):
                stop_reason = "first_access_hard_challenge_after_cooldown"
                budget.consecutive_hard_challenges = max(
                    budget.consecutive_hard_challenges, DEEP_HARD_CHALLENGE_STOP
                )
                return resource2, page2, warnings2
            return resource2, page2, warnings2

        checkpoint()
        return resource, page, warnings

    # --- Search resume from site=search_start_site ---
    previous_paths: set[str] | None = None
    search_stop: str | None = None
    if ledger.get("search_pagination_complete"):
        ledger.setdefault("warnings", []).append("search_pagination_already_complete_on_resume")
    else:
        start_site = search_start_site
        if ledger.get("search_last_site") is not None and int(ledger["search_last_site"]) >= search_start_site:
            # Continue after the last successfully recorded site when resuming mid-search.
            # Still allow retrying a previously challenged site when the stored last site
            # equals search_start_site - 1 (fresh deep pass after coverage site=7).
            last = int(ledger["search_last_site"])
            if last >= search_start_site and any(
                isinstance(p, dict)
                and p.get("route") == "search"
                and p.get("search_site") == last
                and p.get("status") == "ok"
                for p in ledger.get("pages") or []
            ):
                start_site = last + 1
        for site in range(start_site, max_search_pages + 1):
            if stop_reason or stop_for_budget():
                break
            url = build_search_page_url(search_query, site)
            resource, page, _warnings = fetch_once(url, route="search", extra={"search_site": site})
            ledger["search_last_site"] = site
            checkpoint()
            if page.get("challenge"):
                search_stop = "search_hard_challenge"
                if stop_reason or stop_for_challenges():
                    break
                continue
            if resource is None:
                ledger.setdefault("errors", []).append({"url": url, "error": "fetch_failed"})
                search_stop = "search_fetch_failed"
                break
            parsed = parse_search_results_page(resource.text, source_url=url)
            page["parsed"] = len(parsed["product_paths_exact"])
            page["page_of"] = parsed.get("page_of")
            for path in parsed["product_paths_exact"]:
                if is_exact_blue_eyes_white_dragon_path(path):
                    ledger.setdefault("search_paths", []).append(path)
            page_of = parsed.get("page_of") or {}
            if isinstance(page_of.get("total"), int):
                max_search_pages = min(max_search_pages, int(page_of["total"]))
            early = should_stop_search_pagination(
                parsed=parsed, previous_paths=previous_paths, site=site
            )
            previous_paths = set(parsed.get("product_paths_all") or [])
            if early:
                search_stop = early
                break
            if isinstance(page_of.get("current"), int) and isinstance(page_of.get("total"), int):
                if page_of["current"] >= page_of["total"]:
                    search_stop = "search_announced_total_reached"
                    break
        if search_stop in {
            "search_announced_total_reached",
            "search_page_empty",
            "search_page_repeated",
            "search_site_beyond_announced_total",
            "search_page_beyond_announced_total",
        }:
            ledger["search_pagination_complete"] = True
        if search_stop:
            ledger.setdefault("warnings", []).append(f"search_pagination_stop:{search_stop}")
        checkpoint()

    # --- Product detail enrichment (skip ok) ---
    if fetch_product_details and stop_reason is None:
        for item in queue:
            if stop_reason or stop_for_budget():
                break
            if item.get("status") == "ok":
                continue
            path = normalize_public_path(str(item.get("product_path") or ""))
            url = canonical_product_url(path)
            resource, page, _warnings = fetch_once(
                url, route="product_detail", extra={"product_path": path}
            )
            if page.get("challenge"):
                merge_detail_into_queue_item(
                    item,
                    {
                        "error": "hard_challenge",
                        "product_path": path,
                        "source_url": url,
                        "fields_present": {},
                        "fields_absent": [
                            "printed_code",
                            "expansion",
                            "version",
                            "rarity",
                            "from_price",
                            "available_count",
                            "price_trend",
                            "avg_1d",
                            "avg_7d",
                            "avg_30d",
                        ],
                    },
                    status="challenge",
                )
                checkpoint()
                if stop_reason or stop_for_challenges():
                    break
                polite_delay(backoff_seconds(budget.consecutive_hard_challenges + 1))
                continue
            if resource is None:
                merge_detail_into_queue_item(
                    item,
                    {
                        "error": "fetch_failed",
                        "product_path": path,
                        "source_url": url,
                        "fields_present": {},
                        "fields_absent": ["printed_code", "expansion", "version", "rarity"],
                    },
                    status="error",
                )
                ledger.setdefault("errors", []).append({"url": url, "error": "fetch_failed"})
                checkpoint()
                continue
            detail = parse_product_public_details(
                resource.text, product_path=path, source_url=url
            )
            # Sanitize immediately — never keep offer/seller material on the item.
            detail = sanitize_deep_ledger(detail)
            merge_detail_into_queue_item(item, detail, status="ok")
            page["parsed"] = 1
            page["fields_present"] = detail.get("fields_present")
            checkpoint()

    if stop_reason is None:
        if search_stop and not fetch_product_details:
            stop_reason = search_stop
        elif all(item.get("status") == "ok" for item in queue):
            stop_reason = "all_product_details_ok"
        elif search_stop == "search_announced_total_reached" and not any(
            item.get("status") == "pending" for item in queue
        ):
            stop_reason = "search_complete_and_details_attempted"
        elif budget.remaining() <= 0:
            stop_reason = "budget_exhausted"
        else:
            stop_reason = "completed_scheduled_deep_routes"

    ledger["queue"] = queue
    ledger["stop_reason"] = stop_reason
    ledger["finished_at"] = utc_now_iso()
    ledger["budget"] = {
        "max_navigations": budget.max_navigations,
        "navigations_used": budget.navigations,
        "rate_limit_events": budget.rate_limit_events,
        "consecutive_hard_challenges_at_stop": budget.consecutive_hard_challenges,
    }
    ledger["status_counts"] = deep_queue_status_counts(queue)
    ledger["first_access_retry_consumed"] = first_access_consumed
    ledger["unique_product_paths"] = [item["product_path"] for item in queue]
    ledger["status"] = (
        "blocked"
        if stop_reason
        and (
            str(stop_reason).startswith("hard_challenges")
            or stop_reason == "first_access_hard_challenge_after_cooldown"
        )
        else (
            "partial"
            if any(item.get("status") != "ok" for item in queue)
            else "ok"
        )
    )
    # Drop any non-serializable helpers and sanitize before final write.
    payload = sanitize_deep_ledger(
        {key: value for key, value in ledger.items() if not key.startswith("_")}
    )
    atomic_write_json(checkpoint_path, payload)
    payload["_budget_obj"] = budget
    return payload


def validate_official_catalog_url(url: str, *, expected_url: str) -> str:
    """Refuse any URL that is not the exact canonical HTTPS Cardmarket S3 download."""
    if url != expected_url:
        raise ValueError("refusing non-canonical official Cardmarket catalog URL")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("official catalog URL must use https")
    if parsed.netloc != OFFICIAL_CATALOG_HOST:
        raise ValueError("official catalog URL host is not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("official catalog URL must not carry query or fragment")
    if parsed.username or parsed.password:
        raise ValueError("official catalog URL must not embed credentials")
    return url


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def download_official_catalog_bytes(
    url: str,
    *,
    expected_url: str,
    timeout: int = OFFICIAL_CATALOG_TIMEOUT_SECONDS,
    max_bytes: int = OFFICIAL_CATALOG_MAX_BYTES,
) -> tuple[bytes, str]:
    """Single GET with bounded streaming read, SHA-256, and safe error messages."""
    validate_official_catalog_url(url, expected_url=expected_url)
    request = Request(
        url,
        headers={
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json",
        },
    )
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            validate_official_catalog_url(final_url, expected_url=expected_url)
            while True:
                chunk = response.read(OFFICIAL_CATALOG_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(f"response exceeds max_bytes={max_bytes}")
                digest.update(chunk)
                chunks.append(chunk)
    except FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — map to safe FetchError without body/URL secrets
        raise FetchError(f"official catalog fetch failed: {type(exc).__name__}") from exc
    raw = b"".join(chunks)
    return raw, digest.hexdigest()


def parse_official_catalog_json(raw: bytes) -> dict[str, Any]:
    """Parse official JSON while preserving decimal literals via Decimal."""
    try:
        payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("official catalog JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("official catalog JSON root must be an object")
    return payload


def validate_products_catalog_schema(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "version" not in payload:
        raise ValueError("products catalog missing version")
    if not payload.get("createdAt"):
        raise ValueError("products catalog missing createdAt")
    products = payload.get("products")
    if not isinstance(products, list):
        raise ValueError("products catalog missing products list")
    required = {
        "idProduct",
        "name",
        "idCategory",
        "categoryName",
        "idExpansion",
        "idMetacard",
        "dateAdded",
    }
    for index, row in enumerate(products):
        if not isinstance(row, dict):
            raise ValueError(f"products[{index}] is not an object")
        missing = required - set(row)
        if missing:
            raise ValueError(f"products[{index}] missing fields: {sorted(missing)}")
    return products


def validate_price_guide_schema(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "version" not in payload:
        raise ValueError("price guide missing version")
    if not payload.get("createdAt"):
        raise ValueError("price guide missing createdAt")
    guides = payload.get("priceGuides")
    if not isinstance(guides, list):
        raise ValueError("price guide missing priceGuides list")
    required = {"idProduct", "idCategory", "avg", "low", "trend", "avg1", "avg7", "avg30"}
    optional_foil = {
        "avg-foil",
        "low-foil",
        "trend-foil",
        "avg1-foil",
        "avg7-foil",
        "avg30-foil",
    }
    for index, row in enumerate(guides):
        if not isinstance(row, dict):
            raise ValueError(f"priceGuides[{index}] is not an object")
        missing = required - set(row)
        if missing:
            raise ValueError(f"priceGuides[{index}] missing fields: {sorted(missing)}")
        for field in optional_foil:
            row.setdefault(field, None)
    return guides


def assert_unique_id_products(rows: Sequence[dict[str, Any]], *, label: str) -> None:
    ids = [int(row["idProduct"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} contains duplicate idProduct values")


def format_official_decimal(value: object) -> str:
    """Serialize Decimal/number cells without float coercion or trailing-zero loss."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("boolean is not a price decimal")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"unsupported official numeric type: {type(value).__name__}")


def filter_exact_blue_eyes_official_products(
    products: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exact: list[dict[str, Any]] = []
    contains_excluded: list[dict[str, Any]] = []
    for row in products:
        name = row.get("name")
        if name == EXACT_BLUE_EYES_PRODUCT_NAME:
            exact.append(row)
        elif isinstance(name, str) and EXACT_BLUE_EYES_PRODUCT_NAME in name:
            contains_excluded.append(row)
    exact_sorted = sorted(exact, key=lambda row: int(row["idProduct"]))
    assert_unique_id_products(exact_sorted, label="exact Blue-Eyes official products")
    return exact_sorted, contains_excluded


def join_official_blue_eyes_price_rows(
    exact_products: Sequence[dict[str, Any]],
    price_guides: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int]]:
    by_id = {int(row["idProduct"]): row for row in price_guides}
    if len(by_id) != len(price_guides):
        raise ValueError("priceGuides contains duplicate idProduct values")
    joined: list[dict[str, Any]] = []
    missing: list[int] = []
    for product in exact_products:
        product_id = int(product["idProduct"])
        guide = by_id.get(product_id)
        if guide is None:
            missing.append(product_id)
            continue
        joined.append({"product": product, "price": guide})
    return joined, missing


def _official_field_presence(product: dict[str, Any], price: dict[str, Any]) -> tuple[str, str]:
    checks: list[tuple[str, object]] = [
        ("idProduct", product.get("idProduct")),
        ("idExpansion", product.get("idExpansion")),
        ("idMetacard", product.get("idMetacard")),
        ("dateAdded", product.get("dateAdded")),
        ("category", product.get("categoryName")),
        ("name", product.get("name")),
    ]
    for field in OFFICIAL_PRICE_NUMERIC_FIELDS:
        checks.append((field.replace("-", "_"), price.get(field)))
    present: list[str] = []
    absent: list[str] = []
    for name, value in checks:
        if value is None or value == "":
            absent.append(name)
        else:
            present.append(name)
    return "|".join(present), "|".join(absent)


def build_official_join_rows(
    joined: Sequence[dict[str, Any]],
    *,
    products_created_at: str,
    price_guide_created_at: str,
    source_date: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in joined:
        product = item["product"]
        price = item["price"]
        present, absent = _official_field_presence(product, price)
        rows.append(
            {
                "idProduct": str(int(product["idProduct"])),
                "idExpansion": str(int(product["idExpansion"])),
                "idMetacard": str(int(product["idMetacard"])),
                "dateAdded": str(product.get("dateAdded") or ""),
                "category": str(product.get("categoryName") or ""),
                "name": str(product.get("name") or ""),
                "avg": format_official_decimal(price.get("avg")),
                "low": format_official_decimal(price.get("low")),
                "trend": format_official_decimal(price.get("trend")),
                "avg1": format_official_decimal(price.get("avg1")),
                "avg7": format_official_decimal(price.get("avg7")),
                "avg30": format_official_decimal(price.get("avg30")),
                "avg_foil": format_official_decimal(price.get("avg-foil")),
                "low_foil": format_official_decimal(price.get("low-foil")),
                "trend_foil": format_official_decimal(price.get("trend-foil")),
                "avg1_foil": format_official_decimal(price.get("avg1-foil")),
                "avg7_foil": format_official_decimal(price.get("avg7-foil")),
                "avg30_foil": format_official_decimal(price.get("avg30-foil")),
                "fields_present": present,
                "fields_absent": absent,
                "products_created_at": products_created_at,
                "price_guide_created_at": price_guide_created_at,
                "source_date": source_date,
            }
        )
    return rows


def export_official_join_csv(rows: Sequence[dict[str, str]], *, destination: Path | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(OFFICIAL_JOIN_CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in OFFICIAL_JOIN_CSV_COLUMNS})
    text = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return text


def load_html_corpus_paths(coverage_csv: Path | None) -> list[str]:
    if coverage_csv is None or not coverage_csv.exists():
        return []
    return load_exact_blue_eyes_paths_from_coverage_csv(coverage_csv)


def build_official_join_manifest(
    *,
    products_payload: dict[str, Any],
    price_payload: dict[str, Any],
    products_sha256: str,
    price_sha256: str,
    products_bytes: int,
    price_bytes: int,
    singles_before: int,
    exact_products: Sequence[dict[str, Any]],
    contains_excluded: Sequence[dict[str, Any]],
    joined_rows: Sequence[dict[str, str]],
    missing_price_ids: Sequence[int],
    html_paths: Sequence[str],
    source_date: str,
    fetched_live: bool,
) -> dict[str, Any]:
    expansion_ids = sorted({int(row["idExpansion"]) for row in exact_products})
    metacard_ids = sorted({int(row["idMetacard"]) for row in exact_products})
    field_presence: dict[str, int] = Counter()
    field_absence: dict[str, int] = Counter()
    for row in joined_rows:
        for name in (row.get("fields_present") or "").split("|"):
            if name:
                field_presence[name] += 1
        for name in (row.get("fields_absent") or "").split("|"):
            if name:
                field_absence[name] += 1
    official_ids = [int(row["idProduct"]) for row in joined_rows]
    html_count = len(html_paths)
    # Official downloads do not expose public product URL ↔ idProduct. Keep corpora separate.
    mapping_verified = False
    return {
        "title": "Cardmarket Blue-Eyes White Dragon official catalog join manifest",
        "generated_at": utc_now_iso(),
        "timezone": "UTC",
        "source_date": source_date,
        "fetched_live": fetched_live,
        "official_sources": {
            "products_singles_url": OFFICIAL_PRODUCTS_SINGLES_URL,
            "price_guide_url": OFFICIAL_PRICE_GUIDE_URL,
            "products": {
                "version": products_payload.get("version"),
                "createdAt": products_payload.get("createdAt"),
                "sha256": products_sha256,
                "bytes": products_bytes,
                "singles_before_filter": singles_before,
            },
            "price_guide": {
                "version": price_payload.get("version"),
                "createdAt": price_payload.get("createdAt"),
                "sha256": price_sha256,
                "bytes": price_bytes,
                "rows_before_filter": len(price_payload.get("priceGuides") or []),
            },
        },
        "filter": {
            "name_equality": EXACT_BLUE_EYES_PRODUCT_NAME,
            "exact_matches": len(exact_products),
            "contains_matches": len(exact_products) + len(contains_excluded),
            "contains_excluded": len(contains_excluded),
            "excluded_names": sorted({str(row.get("name")) for row in contains_excluded}),
        },
        "join": {
            "key": "idProduct",
            "matched": len(joined_rows),
            "missing_price_guide_ids": list(missing_price_ids),
            "expected_exact": EXPECTED_EXACT_BLUE_EYES_PATHS,
        },
        "corpus": {
            "official_by_idProduct": {
                "count": len(official_ids),
                "unique_idProduct": len(set(official_ids)),
                "idExpansion_count": len(expansion_ids),
                "idMetacard": metacard_ids,
            },
            "html_by_url": {
                "count": html_count,
                "unique_paths": html_count,
                "source": "docs/data coverage CSV public_product_path when provided",
            },
            "url_to_idProduct_mapping": {
                "verified": mapping_verified,
                "note": (
                    "Official productList/priceGuide payloads do not expose public product URLs. "
                    "HTML coverage corpus is keyed by URL/path; official corpus is keyed by idProduct. "
                    "Do not invent rank-to-rank or lexical URL↔idProduct mappings."
                ),
            },
            "concordant_counts_without_row_mapping": {
                "official_exact": len(official_ids),
                "html_exact_paths": html_count,
                "both_177": len(official_ids) == EXPECTED_EXACT_BLUE_EYES_PATHS
                and html_count == EXPECTED_EXACT_BLUE_EYES_PATHS,
            },
        },
        "field_presence_counts": dict(sorted(field_presence.items())),
        "field_absence_counts": dict(sorted(field_absence.items())),
        "privacy": {
            "raw_official_json": "not committed; memory or gitignored runs/ only",
            "seller_fields": "never present in official catalog join",
            "published_artifacts": [
                "sanitized official join CSV",
                "official join manifest JSON/Markdown",
            ],
        },
        "limits": [
            "Exactly one GET per official URL when fetching live.",
            "Decimal price fields are preserved as provided (no float coercion).",
            "HTML URL corpus and official idProduct corpus stay separate unless a verifiable mapping exists.",
        ],
    }


def render_official_join_manifest_markdown(manifest: dict[str, Any]) -> str:
    sources = manifest.get("official_sources") or {}
    products = sources.get("products") or {}
    prices = sources.get("price_guide") or {}
    filt = manifest.get("filter") or {}
    join = manifest.get("join") or {}
    corpus = manifest.get("corpus") or {}
    official = corpus.get("official_by_idProduct") or {}
    html = corpus.get("html_by_url") or {}
    mapping = corpus.get("url_to_idProduct_mapping") or {}
    lines = [
        "# Cardmarket Blue-Eyes official catalog join manifest",
        "",
        f"- Generated: `{manifest.get('generated_at')}` ({manifest.get('timezone')})",
        f"- Source date: **{manifest.get('source_date')}**",
        f"- Fetched live: **{manifest.get('fetched_live')}**",
        "",
        "## Official sources",
        "",
        f"- Products URL: `{sources.get('products_singles_url')}`",
        f"- Price guide URL: `{sources.get('price_guide_url')}`",
        f"- Products `createdAt` / version / SHA-256: "
        f"**{products.get('createdAt')}** / **{products.get('version')}** / `{products.get('sha256')}`",
        f"- Price guide `createdAt` / version / SHA-256: "
        f"**{prices.get('createdAt')}** / **{prices.get('version')}** / `{prices.get('sha256')}`",
        f"- Singles before filter: **{products.get('singles_before_filter')}**",
        f"- Price guide rows before filter: **{prices.get('rows_before_filter')}**",
        "",
        "## Filter and join",
        "",
        f"- Exact name equality: `{filt.get('name_equality')}`",
        f"- Exact / contains / excluded: "
        f"**{filt.get('exact_matches')}** / **{filt.get('contains_matches')}** / "
        f"**{filt.get('contains_excluded')}**",
        f"- Join matched by `idProduct`: **{join.get('matched')}** "
        f"(missing: **{len(join.get('missing_price_guide_ids') or [])}**)",
        "",
        "## Corpora (no invented URL↔idProduct mapping)",
        "",
        f"- Official corpus by `idProduct`: **{official.get('count')}** "
        f"(expansions **{official.get('idExpansion_count')}**, metacard "
        f"**{official.get('idMetacard')}**)",
        f"- HTML corpus by URL/path: **{html.get('count')}**",
        f"- Mapping verified: **{mapping.get('verified')}** — {mapping.get('note')}",
        "",
        "## Field presence",
        "",
    ]
    for name, count in sorted((manifest.get("field_presence_counts") or {}).items()):
        lines.append(f"- `{name}`: **{count}**")
    if manifest.get("field_absence_counts"):
        lines.append("")
        lines.append("## Field absence")
        lines.append("")
        for name, count in sorted((manifest.get("field_absence_counts") or {}).items()):
            lines.append(f"- `{name}`: **{count}**")
    lines.append("")
    return "\n".join(lines)


def build_official_blue_eyes_join(
    *,
    products_raw: bytes,
    price_raw: bytes,
    products_sha256: str | None = None,
    price_sha256: str | None = None,
    html_coverage_csv: Path | None = None,
    source_date: str = "",
    fetched_live: bool = False,
) -> dict[str, Any]:
    products_payload = parse_official_catalog_json(products_raw)
    price_payload = parse_official_catalog_json(price_raw)
    products = validate_products_catalog_schema(products_payload)
    price_guides = validate_price_guide_schema(price_payload)
    assert_unique_id_products(products, label="products catalog")
    assert_unique_id_products(price_guides, label="priceGuides")
    exact, excluded = filter_exact_blue_eyes_official_products(products)
    joined, missing = join_official_blue_eyes_price_rows(exact, price_guides)
    if missing:
        raise ValueError(f"price guide missing {len(missing)} exact Blue-Eyes idProduct rows")
    stamped = (source_date or "").strip() or str(products_payload.get("createdAt") or "")[:10]
    rows = build_official_join_rows(
        joined,
        products_created_at=str(products_payload.get("createdAt") or ""),
        price_guide_created_at=str(price_payload.get("createdAt") or ""),
        source_date=stamped,
    )
    html_paths = load_html_corpus_paths(html_coverage_csv)
    products_digest = products_sha256 or sha256_hex(products_raw)
    price_digest = price_sha256 or sha256_hex(price_raw)
    manifest = build_official_join_manifest(
        products_payload=products_payload,
        price_payload=price_payload,
        products_sha256=products_digest,
        price_sha256=price_digest,
        products_bytes=len(products_raw),
        price_bytes=len(price_raw),
        singles_before=len(products),
        exact_products=exact,
        contains_excluded=excluded,
        joined_rows=rows,
        missing_price_ids=missing,
        html_paths=html_paths,
        source_date=stamped,
        fetched_live=fetched_live,
    )
    return {
        "status": "ok",
        "rows": rows,
        "manifest": manifest,
        "exact_count": len(exact),
        "excluded_count": len(excluded),
        "products_sha256": products_digest,
        "price_sha256": price_digest,
    }


def fetch_official_blue_eyes_join(
    *,
    html_coverage_csv: Path | None = None,
    source_date: str = "",
    timeout: int = OFFICIAL_CATALOG_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Download each official URL once, join exact Blue-Eyes rows, return derived payload."""
    products_raw, products_sha = download_official_catalog_bytes(
        OFFICIAL_PRODUCTS_SINGLES_URL,
        expected_url=OFFICIAL_PRODUCTS_SINGLES_URL,
        timeout=timeout,
    )
    price_raw, price_sha = download_official_catalog_bytes(
        OFFICIAL_PRICE_GUIDE_URL,
        expected_url=OFFICIAL_PRICE_GUIDE_URL,
        timeout=timeout,
    )
    return build_official_blue_eyes_join(
        products_raw=products_raw,
        price_raw=price_raw,
        products_sha256=products_sha,
        price_sha256=price_sha,
        html_coverage_csv=html_coverage_csv,
        source_date=source_date,
        fetched_live=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cardmarket Blue-Eyes public market extraction example (no seller identities, no HTML dumps)"
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Public Cardmarket URL to collect (repeatable). Prefer Versions + product pages.",
    )
    parser.add_argument("--delay-seconds", type=float, default=2.5, help="Polite delay between URLs")
    parser.add_argument(
        "--browser-post-load-wait-ms",
        type=int,
        default=9000,
        help="Extra wait for dynamic pages when browser fallback is used",
    )
    parser.add_argument("--no-browser-fallback", action="store_true", help="Disable browser fallback")
    parser.add_argument(
        "--from-json",
        type=Path,
        help=(
            "Offline mode: load collector stdout / anonymized ledger JSON and skip live fetch. "
            "Use with --export-references-csv for reproducible edition/reference export."
        ),
    )
    parser.add_argument(
        "--export-references-csv",
        type=Path,
        help=(
            "Write sanitized version-floor reference CSV (no sellers/offer ids/HTML). "
            "Works after a live collect or with --from-json."
        ),
    )
    parser.add_argument(
        "--coverage-crawl",
        action="store_true",
        help=(
            "Run public Versions + Search pagination coverage crawl (budget-capped), "
            "optionally fetching product-detail identity fields only."
        ),
    )
    parser.add_argument(
        "--coverage-budget",
        type=int,
        default=MAX_COVERAGE_NAVIGATIONS,
        help=f"Max HTTP/browser navigations for --coverage-crawl (1..{MAX_COVERAGE_NAVIGATIONS})",
    )
    parser.add_argument(
        "--no-product-details",
        action="store_true",
        help="With --coverage-crawl, skip per-product detail fetches",
    )
    parser.add_argument(
        "--no-fr-versions",
        action="store_true",
        help="With --coverage-crawl, skip FR Versions cross-check route",
    )
    parser.add_argument(
        "--resume-ledger",
        type=Path,
        help="Resume coverage crawl from a private gitignored ledger JSON",
    )
    parser.add_argument(
        "--export-coverage-csv",
        type=Path,
        help="Write sanitized coverage CSV (paths + public identity fields only)",
    )
    parser.add_argument(
        "--export-coverage-manifest",
        type=Path,
        help="Write coverage manifest JSON (Markdown sibling written when suffix is .json)",
    )
    parser.add_argument(
        "--prior-references-csv",
        type=Path,
        help="Prior sanitized references CSV for 175/102 comparison (default: docs/data snapshot)",
    )
    parser.add_argument(
        "--write-private-ledger",
        type=Path,
        help="Write full coverage ledger JSON under a gitignored path (e.g. runs/...)",
    )
    parser.add_argument(
        "--deep-enrichment",
        action="store_true",
        help=(
            "Resume Search at site=8 then enrich the exact 177-path corpus with public "
            "product metrics (budget-capped, atomic checkpoint, no seller data)."
        ),
    )
    parser.add_argument(
        "--deep-budget",
        type=int,
        default=MAX_DEEP_ENRICHMENT_NAVIGATIONS,
        help=f"Max navigations for --deep-enrichment (1..{MAX_DEEP_ENRICHMENT_NAVIGATIONS})",
    )
    parser.add_argument(
        "--deep-search-start-site",
        type=int,
        default=8,
        help="Search site=N to resume from during --deep-enrichment (default: 8)",
    )
    parser.add_argument(
        "--coverage-corpus-csv",
        type=Path,
        help="Exact 177-path coverage CSV used to seed the deep-enrichment queue",
    )
    parser.add_argument(
        "--deep-checkpoint",
        type=Path,
        help="Private atomic checkpoint ledger JSON for --deep-enrichment (gitignored runs/)",
    )
    parser.add_argument(
        "--export-deep-csv",
        type=Path,
        help="Write sanitized deep-enrichment CSV",
    )
    parser.add_argument(
        "--export-deep-manifest",
        type=Path,
        help="Write deep-enrichment manifest JSON (Markdown sibling when suffix is .json)",
    )
    parser.add_argument(
        "--no-deep-product-details",
        action="store_true",
        help="With --deep-enrichment, only resume Search pagination (skip product details)",
    )
    parser.add_argument(
        "--official-catalog-join",
        action="store_true",
        help=(
            "Join official Cardmarket products_singles_3 + price_guide_3 for exact "
            "Blue-Eyes White Dragon rows (one GET per URL unless offline JSON paths are set)."
        ),
    )
    parser.add_argument(
        "--official-products-json",
        type=Path,
        help="Offline products_singles_3.json (gitignored). Skips the products GET.",
    )
    parser.add_argument(
        "--official-price-guide-json",
        type=Path,
        help="Offline price_guide_3.json (gitignored). Skips the price-guide GET.",
    )
    parser.add_argument(
        "--export-official-csv",
        type=Path,
        help="Write sanitized official join CSV (177 exact rows; never the raw catalogs).",
    )
    parser.add_argument(
        "--export-official-manifest",
        type=Path,
        help="Write official join manifest JSON (Markdown sibling when suffix is .json).",
    )
    parser.add_argument(
        "--html-coverage-csv",
        type=Path,
        help=(
            "HTML corpus coverage CSV for concordant 177-path comparison in the official "
            "manifest (no invented URL↔idProduct mapping)."
        ),
    )
    parser.add_argument(
        "--source-date",
        default="",
        help="ISO date stamped on CSV rows (default: today UTC, or value from payload when present).",
    )
    parser.add_argument(
        "--quiet-json",
        action="store_true",
        help="With --from-json / CSV export, skip printing the full JSON payload to stdout.",
    )
    args = parser.parse_args(argv)

    payload: dict[str, Any]
    budget_obj: CoverageBudget | None = None

    if args.official_catalog_join:
        repo_root = Path(__file__).resolve().parents[1]
        html_csv = args.html_coverage_csv or (
            repo_root / "docs" / "data" / "cardmarket-blue-eyes-coverage-2026-08-04.csv"
        )
        offline_products = args.official_products_json
        offline_prices = args.official_price_guide_json
        if (offline_products is None) ^ (offline_prices is None):
            parser.error(
                "--official-products-json and --official-price-guide-json must be provided together"
            )
        if offline_products is not None and offline_prices is not None:
            products_raw = offline_products.read_bytes()
            price_raw = offline_prices.read_bytes()
            join_payload = build_official_blue_eyes_join(
                products_raw=products_raw,
                price_raw=price_raw,
                html_coverage_csv=html_csv if html_csv.exists() else None,
                source_date=args.source_date,
                fetched_live=False,
            )
        else:
            join_payload = fetch_official_blue_eyes_join(
                html_coverage_csv=html_csv if html_csv.exists() else None,
                source_date=args.source_date,
            )
        if args.export_official_csv is not None:
            export_official_join_csv(join_payload["rows"], destination=args.export_official_csv)
        if args.export_official_manifest is not None:
            manifest = join_payload["manifest"]
            args.export_official_manifest.parent.mkdir(parents=True, exist_ok=True)
            args.export_official_manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str)
                + "\n",
                encoding="utf-8",
            )
            md_path = args.export_official_manifest.with_suffix(".md")
            md_path.write_text(render_official_join_manifest_markdown(manifest), encoding="utf-8")
            join_payload["official_manifest"] = str(args.export_official_manifest)
            join_payload["official_manifest_markdown"] = str(md_path)
        serializable = {
            "status": join_payload.get("status"),
            "exact_count": join_payload.get("exact_count"),
            "excluded_count": join_payload.get("excluded_count"),
            "products_sha256": join_payload.get("products_sha256"),
            "price_sha256": join_payload.get("price_sha256"),
            "official_csv": str(args.export_official_csv) if args.export_official_csv else None,
            "official_manifest": join_payload.get("official_manifest"),
            "manifest": join_payload.get("manifest"),
        }
        if not args.quiet_json:
            print(json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        else:
            print(
                json.dumps(
                    {
                        "status": serializable["status"],
                        "exact_count": serializable["exact_count"],
                        "excluded_count": serializable["excluded_count"],
                        "products_sha256": serializable["products_sha256"],
                        "price_sha256": serializable["price_sha256"],
                        "official_csv": serializable["official_csv"],
                        "official_manifest": serializable["official_manifest"],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0 if join_payload.get("status") == "ok" else 1

    if args.deep_enrichment:
        repo_root = Path(__file__).resolve().parents[1]
        coverage_csv = args.coverage_corpus_csv or (
            repo_root / "docs" / "data" / "cardmarket-blue-eyes-coverage-2026-08-04.csv"
        )
        checkpoint = args.deep_checkpoint or (
            repo_root / "runs" / "cardmarket-blue-eyes-deep" / "ledger.json"
        )
        resume = None
        if args.resume_ledger and args.resume_ledger.exists():
            resume = sanitize_deep_ledger(load_coverage_ledger(args.resume_ledger))
        elif checkpoint.exists():
            resume = sanitize_deep_ledger(load_coverage_ledger(checkpoint))
        payload = crawl_deep_enrichment(
            coverage_csv=coverage_csv,
            checkpoint_path=checkpoint,
            delay_seconds=max(args.delay_seconds, MIN_DEEP_DELAY_SECONDS),
            browser_fallback=not args.no_browser_fallback,
            browser_post_load_wait_ms=args.browser_post_load_wait_ms,
            max_navigations=args.deep_budget,
            search_start_site=args.deep_search_start_site,
            fetch_product_details=not args.no_deep_product_details,
            resume_from=resume,
        )
        budget_obj = payload.pop("_budget_obj", None)
    elif args.coverage_crawl:
        resume = load_coverage_ledger(args.resume_ledger) if args.resume_ledger else None
        payload = crawl_public_blue_eyes_coverage(
            delay_seconds=max(args.delay_seconds, MIN_PRODUCT_DELAY_SECONDS),
            browser_fallback=not args.no_browser_fallback,
            browser_post_load_wait_ms=args.browser_post_load_wait_ms,
            max_navigations=args.coverage_budget,
            include_fr_versions=not args.no_fr_versions,
            fetch_product_details=not args.no_product_details,
            resume_from=resume,
        )
        budget_obj = payload.pop("_budget_obj", None)
        payload.pop("_budget_obj_navigations", None)
    elif args.from_json is not None:
        payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            parser.error("--from-json must contain a JSON object")
        if payload.get("version_refs") or payload.get("unique_product_paths"):
            # Coverage ledger offline mode.
            pass
        else:
            floors = extract_version_floors(payload)
            offers = [row for row in payload.get("offers", []) if isinstance(row, dict)]
            if "populations" not in payload:
                payload = {
                    **payload,
                    "version_floors": floors,
                    "offers": offers,
                    "populations": summarize_populations(floors, offers),
                    "status": payload.get("status") or "ok",
                }
            else:
                payload = {**payload, "version_floors": floors, "offers": offers}
    else:
        urls = list(args.url)
        if not urls:
            parser.error(
                "provide at least one --url, or use --from-json / --coverage-crawl / "
                "--deep-enrichment / --official-catalog-join"
            )
        payload = collect_cardmarket_blue_eyes(
            urls,
            delay_seconds=args.delay_seconds,
            browser_fallback=not args.no_browser_fallback,
            browser_post_load_wait_ms=args.browser_post_load_wait_ms,
        )

    source_date = (args.source_date or "").strip()
    if not source_date:
        source_date = str(
            payload.get("source_date")
            or payload.get("started_at")
            or time.strftime("%Y-%m-%d", time.gmtime())
        )[:10]

    if args.export_references_csv is not None:
        floors = extract_version_floors(payload) if "version_floors" in payload or "records" in payload else []
        if not floors and payload.get("version_refs"):
            # Map priced coverage refs into floor export shape when requested.
            floors = [
                {
                    **row,
                    "record_kind": "version_floor",
                }
                for row in payload["version_refs"]
                if isinstance(row, dict) and row.get("price_cents") is not None
            ]
        export_version_floor_references_csv(
            floors,
            source_date=source_date,
            destination=args.export_references_csv,
        )
        payload.setdefault("warnings", [])
        if isinstance(payload["warnings"], list):
            payload["warnings"] = sorted(
                set(payload["warnings"])
                | {
                    f"exported {len(floors)} version_floor references to {args.export_references_csv}",
                    "public_product_path values are Cardmarket URLs/paths, not printed card codes",
                }
            )

    coverage_rows: list[dict[str, str]] | None = None
    comparison: dict[str, Any] | None = None
    if args.export_coverage_csv is not None or args.export_coverage_manifest is not None or args.coverage_crawl:
        prior_csv = args.prior_references_csv
        if prior_csv is None:
            default_prior = (
                Path(__file__).resolve().parents[1]
                / "docs"
                / "data"
                / "cardmarket-blue-eyes-version-floors-2026-08-04.csv"
            )
            prior_csv = default_prior if default_prior.exists() else None
        prior_paths: list[str] = []
        prior_expansions = None
        if prior_csv is not None and prior_csv.exists():
            prior_paths, prior_expansions = prior_paths_from_csv(prior_csv)
        coverage_rows = build_coverage_rows(
            version_refs=payload.get("version_refs") or [],
            search_paths=payload.get("search_paths") or payload.get("unique_product_paths") or [],
            product_details=payload.get("product_details") or [],
            source_date=source_date,
        )
        comparison = compare_coverage_to_prior_corpus(
            [row["public_product_path"] for row in coverage_rows],
            prior_paths=prior_paths,
            prior_expansion_count=prior_expansions,
        )
        payload["comparison_to_prior_175_102"] = comparison

    if args.export_coverage_csv is not None:
        assert coverage_rows is not None
        export_coverage_csv(coverage_rows, destination=args.export_coverage_csv)
        payload.setdefault("warnings", [])
        if isinstance(payload["warnings"], list):
            payload["warnings"] = sorted(
                set(payload["warnings"])
                | {f"exported {len(coverage_rows)} coverage rows to {args.export_coverage_csv}"}
            )

    if args.export_coverage_manifest is not None:
        assert coverage_rows is not None and comparison is not None
        manifest = build_coverage_manifest(
            ledger=payload,
            coverage_rows=coverage_rows,
            comparison=comparison,
            budget=budget_obj,
        )
        args.export_coverage_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.export_coverage_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path = args.export_coverage_manifest.with_suffix(".md")
        md_path.write_text(render_coverage_manifest_markdown(manifest), encoding="utf-8")
        payload["coverage_manifest"] = str(args.export_coverage_manifest)
        payload["coverage_manifest_markdown"] = str(md_path)

    if args.write_private_ledger is not None:
        # Strip non-serializable helpers if any remain; sanitize seller/HTML fields.
        serializable = sanitize_deep_ledger(
            {
                key: value
                for key, value in payload.items()
                if not key.startswith("_") and key != "_budget_obj"
            }
        )
        args.write_private_ledger.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(args.write_private_ledger, serializable)

    deep_rows: list[dict[str, str]] | None = None
    if args.export_deep_csv is not None or args.export_deep_manifest is not None or args.deep_enrichment:
        queue = payload.get("queue") or []
        deep_rows = build_deep_enrichment_rows(queue, source_date=source_date)

    if args.export_deep_csv is not None:
        assert deep_rows is not None
        export_deep_enrichment_csv(deep_rows, destination=args.export_deep_csv)
        payload.setdefault("warnings", [])
        if isinstance(payload["warnings"], list):
            payload["warnings"] = sorted(
                set(payload["warnings"])
                | {f"exported {len(deep_rows)} deep-enrichment rows to {args.export_deep_csv}"}
            )

    if args.export_deep_manifest is not None:
        assert deep_rows is not None
        deep_manifest = build_deep_enrichment_manifest(payload, rows=deep_rows)
        args.export_deep_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.export_deep_manifest.write_text(
            json.dumps(deep_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path = args.export_deep_manifest.with_suffix(".md")
        md_path.write_text(render_deep_enrichment_manifest_markdown(deep_manifest), encoding="utf-8")
        payload["deep_enrichment_manifest"] = str(args.export_deep_manifest)
        payload["deep_enrichment_manifest_markdown"] = str(md_path)

    if not args.quiet_json:
        serializable = {
            key: value
            for key, value in payload.items()
            if not key.startswith("_") and not isinstance(value, CoverageBudget)
        }
        print(json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.export_coverage_csv is not None or args.export_coverage_manifest is not None:
        print(
            json.dumps(
                {
                    "status": payload.get("status") or "ok",
                    "stop_reason": payload.get("stop_reason"),
                    "unique_product_paths": len(payload.get("unique_product_paths") or coverage_rows or []),
                    "coverage_csv": str(args.export_coverage_csv) if args.export_coverage_csv else None,
                    "coverage_manifest": str(args.export_coverage_manifest)
                    if args.export_coverage_manifest
                    else None,
                    "source_date": source_date,
                    "comparison_to_prior_175_102": comparison,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif args.export_deep_csv is not None or args.export_deep_manifest is not None:
        counts = deep_queue_status_counts(payload.get("queue") or [])
        print(
            json.dumps(
                {
                    "status": payload.get("status") or "ok",
                    "stop_reason": payload.get("stop_reason"),
                    "counts": counts,
                    "navigations_used": (payload.get("budget") or {}).get("navigations_used"),
                    "deep_csv": str(args.export_deep_csv) if args.export_deep_csv else None,
                    "deep_manifest": str(args.export_deep_manifest)
                    if args.export_deep_manifest
                    else None,
                    "source_date": source_date,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif args.export_references_csv is not None:
        floors = extract_version_floors(payload) if payload.get("version_floors") else []
        print(
            json.dumps(
                {
                    "status": payload.get("status") or "ok",
                    "version_floor_count": len(floors),
                    "references_csv": str(args.export_references_csv),
                    "source_date": source_date,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    return 0 if payload.get("status") in {"ok", "partial", None} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
