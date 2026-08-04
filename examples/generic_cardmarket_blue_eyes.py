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
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

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

    return {
        "version_floors": {
            "n": len(version_floors),
            "stats": price_quartiles(float(row["price_eur"]) for row in version_floors),
            "by_rarity": {
                rarity: price_quartiles(values) for rarity, values in sorted(rarity_groups.items())
            },
            "note": "Version floors are product-level 'From' prices, not offer rows.",
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
    args = parser.parse_args(argv)
    urls = list(args.url)
    if not urls:
        parser.error("provide at least one --url")
    payload = collect_cardmarket_blue_eyes(
        urls,
        delay_seconds=args.delay_seconds,
        browser_fallback=not args.no_browser_fallback,
        browser_post_load_wait_ms=args.browser_post_load_wait_ms,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
