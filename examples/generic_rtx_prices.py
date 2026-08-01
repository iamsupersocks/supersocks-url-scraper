#!/usr/bin/env python3
"""Generic RTX listing price example built on supersocks-url-scraper.

This intentionally uses placeholder URLs and synthetic JSON shapes. It does not
ship marketplace domains, real listing IDs, saved HTML, cookies, tokens, or live
results.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
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

MAX_PAGE_LIMIT = 100
RTX_PATTERN = re.compile(r"\brtx(?=\s|\d|[-_/]|$)", re.I)
ACCESS_CHALLENGE_PATTERN = re.compile(
    r"\b(captcha|challenge|access denied|forbidden)\b|geo\.captcha-delivery\.com",
    re.I,
)
SCRIPT_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.I | re.S)
ID_ATTR_RE = re.compile(r"\bid\s*=\s*(['\"])(?P<id>[^'\"]+)\1", re.I)


@dataclass(frozen=True)
class JsonConfig:
    script_id: str = "RTX_PRICE_DATA"
    items_path: tuple[str, ...] = ("items",)
    id_field: str = "id"
    title_field: str = "title"
    price_field: str = "price"
    currency_field: str = "currency"
    url_field: str = "url"
    price_unit: str = "major"
    default_currency: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> "JsonConfig":
        items_path = raw.get("items_path", ["items"])
        if isinstance(items_path, str):
            path = tuple(part for part in items_path.split(".") if part)
        elif isinstance(items_path, list):
            path = tuple(str(part) for part in items_path if str(part))
        else:
            path = ("items",)
        price_unit = str(raw.get("price_unit") or "major").strip().lower()
        if price_unit not in {"major", "cents"}:
            raise ValueError("price_unit must be 'major' or 'cents'")
        currency_field = str(raw["currency_field"]) if "currency_field" in raw else "currency"
        return cls(
            script_id=str(raw.get("script_id") or "RTX_PRICE_DATA"),
            items_path=path or ("items",),
            id_field=str(raw.get("id_field") or "id"),
            title_field=str(raw.get("title_field") or "title"),
            price_field=str(raw.get("price_field") or "price"),
            currency_field=currency_field,
            url_field=str(raw.get("url_field") or "url"),
            price_unit=price_unit,
            default_currency=str(raw.get("default_currency") or "").strip(),
        )


def _nested_value(data: Any, path: Iterable[str]) -> Any:
    current = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def extract_embedded_json(markup: str, *, script_id: str = "RTX_PRICE_DATA") -> Any:
    """Extract JSON from an isolated script tag id.

    The example keeps the selector configurable but intentionally narrow: it does
    not scrape arbitrary scripts, execute JavaScript, or infer marketplace state.
    """
    for match in SCRIPT_RE.finditer(markup or ""):
        attrs = match.group("attrs") or ""
        id_match = ID_ATTR_RE.search(attrs)
        if not id_match or id_match.group("id") != script_id:
            continue
        raw = match.group("body").strip()
        if not raw:
            raise ValueError(f"script #{script_id} is empty")
        return json.loads(raw)
    raise ValueError(f"script #{script_id} not found")


def is_access_challenge(markup: str) -> bool:
    """Return whether markup looks like a block/challenge page.

    The example reports the boundary and stops; it never attempts to solve or
    evade an access challenge.
    """
    return ACCESS_CHALLENGE_PATTERN.search(markup or "") is not None


def iter_listing_items(data: Any, config: JsonConfig = JsonConfig()) -> list[dict[str, Any]]:
    items = _nested_value(data, config.items_path)
    if not isinstance(items, list):
        raise ValueError("configured items_path does not resolve to a list")
    return [item for item in items if isinstance(item, dict)]


def _first_scalar(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return next((item for item in value if item is not None and item != ""), None)
    return value


def parse_euro_price(
    value: object,
    currency: object = "",
    *,
    price_unit: str = "major",
    allow_bare_numeric: bool = False,
) -> int | None:
    """Return price in euro cents, accepting a scalar or one-value array."""
    scalar = _first_scalar(value)
    currency_text = str(currency or "").strip().upper()
    text = str(scalar or "").strip()
    if not text:
        return None
    if price_unit not in {"major", "cents"}:
        return None
    normalized_currency = currency_text in {"EUR", "EURO", "EUROS", "€"}
    text_has_eur = "€" in text or re.search(r"\bEUR\b|\bEURO(S)?\b", text, re.I) is not None
    if currency_text and not normalized_currency:
        return None
    if price_unit != "cents" and not normalized_currency and not text_has_eur and not allow_bare_numeric:
        return None

    cleaned = re.sub(r"(?i)\bEUR\b|\bEURO(S)?\b|€", "", text)
    cleaned = cleaned.replace("\u00a0", " ").replace("'", "")
    cleaned = re.sub(r"[^0-9,\.\- ]", "", cleaned).strip().replace(" ", "")
    if not cleaned or cleaned in {"-", ".", ","}:
        return None

    if "," in cleaned and "." in cleaned:
        decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = "".join(parts) if len(parts[-1]) == 3 and len(parts) > 1 else cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    elif "." in cleaned and len(cleaned.rsplit(".", 1)[-1]) == 3:
        cleaned = cleaned.replace(".", "")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    multiplier = Decimal("1") if price_unit == "cents" else Decimal("100")
    return int((amount * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def is_rtx_listing(title: object) -> bool:
    return RTX_PATTERN.search(str(title or "")) is not None


def normalize_listing(item: dict[str, Any], config: JsonConfig = JsonConfig()) -> dict[str, Any] | None:
    listing_id = str(item.get(config.id_field) or "").strip()
    title = str(item.get(config.title_field) or "").strip()
    if not listing_id or not is_rtx_listing(title):
        return None
    currency = item.get(config.currency_field) if config.currency_field else ""
    currency = currency or config.default_currency
    price_cents = parse_euro_price(
        item.get(config.price_field),
        currency,
        price_unit=config.price_unit,
        allow_bare_numeric=bool(config.default_currency),
    )
    if price_cents is None:
        return None
    out = {
        "id": listing_id,
        "title": title,
        "price_eur": float(Decimal(price_cents) / Decimal(100)),
        "price_cents": price_cents,
    }
    relative_url = str(item.get(config.url_field) or "").strip()
    if relative_url and not relative_url.lower().startswith(("http://", "https://")):
        out["relative_url"] = relative_url
    return out


def fetch_listing_markup(
    url: str,
    *,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 8000,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
) -> tuple[FetchedResource | None, list[str]]:
    """Fetch raw markup through the existing HTTP/SEO/browser runtime pieces."""
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
        except Exception as browser_error:
            warnings.append(f"browser fallback failed: {browser_error}")
    return None, warnings


def collect_rtx_prices(
    url_template: str,
    *,
    max_pages: int = 3,
    delay_seconds: float = 1.0,
    browser_fallback: bool = True,
    browser_post_load_wait_ms: int = 8000,
    config: JsonConfig = JsonConfig(),
) -> dict[str, Any]:
    if max_pages < 1 or max_pages > MAX_PAGE_LIMIT:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGE_LIMIT}")
    seen: set[str] = set()
    listings: list[dict[str, Any]] = []
    page_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    incomplete = False

    for page in range(1, max_pages + 1):
        url = url_template.format(page=page)
        resource, fetch_warnings = fetch_listing_markup(
            url,
            browser_fallback=browser_fallback,
            browser_post_load_wait_ms=browser_post_load_wait_ms,
        )
        warnings.extend(fetch_warnings)
        page_status = "ok" if resource is not None else "error"
        fetch_method = resource.headers.get("x-fetch-method", "http") if resource is not None else None
        page_results.append({"page": page, "status": page_status, "fetch_method": fetch_method, "url": url})
        if resource is None:
            incomplete = True
            continue
        try:
            data = extract_embedded_json(resource.text, script_id=config.script_id)
            items = iter_listing_items(data, config)
        except Exception as exc:
            incomplete = True
            if is_access_challenge(resource.text):
                page_results[-1]["status"] = "blocked"
                warnings.append(f"page {page}: access challenge detected; pagination stopped")
                break
            page_results[-1]["status"] = "partial"
            warnings.append(f"page {page}: embedded JSON extraction failed: {exc}")
            continue
        for item in items:
            listing = normalize_listing(item, config)
            if not listing or listing["id"] in seen:
                continue
            seen.add(listing["id"])
            listings.append(listing | {"page": page})
        if page != max_pages and delay_seconds > 0:
            time.sleep(delay_seconds)

    if listings:
        status = "partial" if incomplete else "ok"
    elif any(p["status"] in {"ok", "partial"} for p in page_results):
        status = "partial"
    else:
        status = "error"
    return {"status": status, "count": len(listings), "listings": listings, "pages": page_results, "warnings": sorted(set(warnings))}


def _load_config(path: str) -> JsonConfig:
    if not path:
        return JsonConfig()
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("config file must contain a JSON object")
    return JsonConfig.from_mapping(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generic placeholder RTX price extraction example")
    parser.add_argument("--url-template", required=True, help="Placeholder page URL template, e.g. https://marketplace.invalid/search?q=rtx&page={page}")
    parser.add_argument("--config", default="", help="Optional JSON config for embedded script id and item fields")
    parser.add_argument("--max-pages", type=int, default=3, help=f"Bounded page count, 1..{MAX_PAGE_LIMIT}")
    parser.add_argument("--delay-seconds", type=float, default=1.0, help="Polite delay between pages")
    parser.add_argument("--browser-post-load-wait-ms", type=int, default=8000, help="Extra wait for dynamic pages when browser fallback is used")
    parser.add_argument("--no-browser-fallback", action="store_true", help="Disable browser fallback")
    args = parser.parse_args(argv)
    payload = collect_rtx_prices(
        args.url_template,
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
        browser_fallback=not args.no_browser_fallback,
        browser_post_load_wait_ms=args.browser_post_load_wait_ms,
        config=_load_config(args.config),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
