#!/usr/bin/env python3
"""Probe URLs and write only per-domain fetch-routing metadata.

This is intentionally metadata-only. It must never persist page content, cookies,
browser profiles, credentials, summaries, or prompts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supersocks_url_scraper.reader import normalize_domain, read_url  # noqa: E402

VALID_METHODS = {"http", "seo", "cloak", "cloak-profile", "archive", "fallback"}


def _truthy(value: str) -> bool:
    return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _iter_urls(args: argparse.Namespace) -> Iterable[str]:
    for url in args.urls:
        value = url.strip()
        if value:
            yield value
    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                yield value


def _record(method: str, *, success_count: int = 1, detail: str = "source-discovery") -> dict[str, object]:
    if method not in VALID_METHODS:
        method = "fallback"
    return {"fetch_method": method, "success_count": success_count, "failure_count": 0, "detail": detail}


def discover(args: argparse.Namespace) -> dict[str, object]:
    cache_path = Path(args.cache)
    cache = _load_json_object(cache_path)
    results: list[dict[str, object]] = []
    updated = 0
    failed = 0

    for url in _iter_urls(args):
        domain = normalize_domain(url)
        if not domain:
            results.append({"url": url, "status": "error", "warning": "no domain"})
            failed += 1
            continue
        if domain in cache and not args.overwrite:
            results.append({"url": url, "domain": domain, "status": "skipped", "reason": "cache exists"})
            continue

        result = read_url(
            url,
            length=args.length,
            include_content=False,
            seo_fallback=not args.no_seo_fallback,
            strategy_cache_path=None,
            browser_fallback=args.browser_fallback,
            browser_profile_dir=args.browser_profile_dir,
            browser_post_load_wait_ms=args.browser_post_load_wait_ms,
            browser_max_concurrency=args.browser_max_concurrency,
            archive_fallback=not args.no_archive_fallback,
        )
        status = str(result.get("status") or "error")
        method = str(result.get("fetch_method") or "fallback")
        raw_warnings = result.get("warnings")
        warnings: list[object] = raw_warnings if isinstance(raw_warnings, list) else []
        summary_len = len(str(result.get("summary") or ""))
        ok = status in {"ok", "partial"} and summary_len >= args.min_summary_chars
        if ok:
            cache[domain] = _record(method, detail="source-discovery")
            updated += 1
        else:
            failed += 1
        results.append(
            {
                "url": url,
                "domain": domain,
                "status": status,
                "fetch_method": method,
                "summary_len": summary_len,
                "cached": ok,
                "warnings": warnings[:3],
            }
        )

    if not args.dry_run:
        _write_json_object(cache_path, cache)
    return {"updated": updated, "failed": failed, "total_cache_entries": len(cache), "dry_run": args.dry_run, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe URLs and update a metadata-only fetch strategy cache.")
    parser.add_argument("urls", nargs="*", help="Representative URLs to probe")
    parser.add_argument("--urls-file", default="", help="Optional newline-delimited URLs file")
    parser.add_argument("--cache", default="data/fetch-strategies.json", help="Strategy cache JSON path")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing domain strategies")
    parser.add_argument("--dry-run", action="store_true", help="Probe but do not write cache")
    parser.add_argument("--length", type=int, default=600)
    parser.add_argument("--min-summary-chars", type=int, default=80)
    parser.add_argument("--no-seo-fallback", action="store_true")
    parser.add_argument("--browser-fallback", default="1", help="Truth-y value enables CloakBrowser fallback")
    parser.add_argument("--browser-profile-dir", default="")
    parser.add_argument("--browser-post-load-wait-ms", type=int, default=10000)
    parser.add_argument("--browser-max-concurrency", type=int, default=1)
    parser.add_argument("--no-archive-fallback", action="store_true")
    args = parser.parse_args()
    args.browser_fallback = _truthy(str(args.browser_fallback))

    if not list(_iter_urls(args)):
        parser.error("provide at least one URL or --urls-file")

    print(json.dumps(discover(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
