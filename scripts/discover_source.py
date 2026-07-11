#!/usr/bin/env python3
"""Discover/update source metadata for URLs seen by a URL-reader pipeline.

Flow:
    new URL -> POST /summarize -> record domain quality/routing metadata
    -> optionally update fetch-strategy cache when the read is good.

The discovery registry and strategy cache store metadata only; never fetched
content, cookies, browser sessions, tokens, credentials, or prompts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supersocks_url_scraper.reader import StrategyCache  # noqa: E402
from supersocks_url_scraper.source_discovery import extract_strategy_detail, update_source_discovery  # noqa: E402


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def call_summarize(
    base_url: str,
    url: str,
    *,
    length: int,
    token: str = "",
    browser_fallback: bool = True,
    archive_fallback: bool = True,
    browser_post_load_wait_ms: int = 10000,
    browser_profile_dir: str = "",
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/summarize"
    body = json.dumps(
        {
            "url": url,
            "length": length,
            "include_content": False,
            "browser_fallback": browser_fallback,
            "archive_fallback": archive_fallback,
            "browser_post_load_wait_ms": browser_post_load_wait_ms,
            "browser_profile_dir": browser_profile_dir,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"summarize HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"summarize failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("summarize returned non-object JSON")
    return payload


def discover_url(
    url: str,
    *,
    base_url: str,
    discovery_path: Path,
    strategy_cache_path: Path | None = None,
    token: str = "",
    length: int = 600,
    browser_fallback: bool = True,
    archive_fallback: bool = True,
    browser_post_load_wait_ms: int = 10000,
    browser_profile_dir: str = "",
) -> dict[str, Any]:
    response = call_summarize(
        base_url,
        url,
        length=length,
        token=token,
        browser_fallback=browser_fallback,
        archive_fallback=archive_fallback,
        browser_post_load_wait_ms=browser_post_load_wait_ms,
        browser_profile_dir=browser_profile_dir,
    )
    record = update_source_discovery(discovery_path, url, response)

    strategy_updated = False
    if strategy_cache_path and record["quality"] == "ok" and response.get("fetch_method"):
        StrategyCache(str(strategy_cache_path)).record_success(
            url,
            str(response.get("fetch_method")),
            detail=extract_strategy_detail(response),
        )
        strategy_updated = True

    return {
        "record": record,
        "strategy_cache_updated": strategy_updated,
        "summary_sample": str(response.get("summary") or "")[:240],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover URL source/routing metadata without storing page content.")
    parser.add_argument("--url", required=True, help="URL encountered by your application")
    parser.add_argument("--base-url", default=os.environ.get("SUPERSOCKS_URL_READER_BASE_URL", "http://127.0.0.1:8768"))
    parser.add_argument("--token", default=os.environ.get("SUPERSOCKS_URL_READER_TOKEN", os.environ.get("API_BEARER_TOKEN", "")))
    parser.add_argument("--length", type=int, default=600)
    parser.add_argument("--discovery", default="data/source-discovery.json")
    parser.add_argument("--strategy-cache", default=os.environ.get("FETCH_STRATEGY_CACHE_PATH", "data/fetch-strategies.json"))
    parser.add_argument("--no-strategy-cache", action="store_true")
    parser.add_argument("--browser-fallback", default=os.environ.get("BROWSER_FALLBACK", "1"))
    parser.add_argument("--browser-profile-dir", default=os.environ.get("BROWSER_PROFILE_DIR", ""))
    parser.add_argument("--browser-post-load-wait-ms", type=int, default=int(os.environ.get("BROWSER_POST_LOAD_WAIT_MS", "10000")))
    parser.add_argument("--no-archive-fallback", action="store_true")
    args = parser.parse_args()

    result = discover_url(
        args.url,
        base_url=args.base_url,
        discovery_path=Path(args.discovery),
        strategy_cache_path=None if args.no_strategy_cache else Path(args.strategy_cache),
        token=args.token,
        length=args.length,
        browser_fallback=_truthy(args.browser_fallback, True),
        archive_fallback=not args.no_archive_fallback,
        browser_post_load_wait_ms=args.browser_post_load_wait_ms,
        browser_profile_dir=args.browser_profile_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
