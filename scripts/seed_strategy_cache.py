#!/usr/bin/env python3
"""Merge a domain strategy seed into a runtime fetch-strategy cache.

The cache stores only routing metadata by domain. It must never contain cookies,
fetched content, credentials, browser profiles, or article text.

Example:
    python3 scripts/seed_strategy_cache.py \
      --seed examples/fetch-strategies.media.seed.json \
      --cache data/fetch-strategies.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supersocks_url_scraper.reader import StrategyRecord, normalize_domain  # noqa: E402


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _record_to_mapping(record: StrategyRecord) -> dict[str, object]:
    out: dict[str, object] = {
        "fetch_method": record.fetch_method,
        "success_count": record.success_count,
        "failure_count": record.failure_count,
    }
    if record.detail:
        out["detail"] = record.detail
    if record.last_success_at:
        out["last_success_at"] = record.last_success_at
    if record.last_failure_at:
        out["last_failure_at"] = record.last_failure_at
    return out


def merge_seed(seed_path: Path, cache_path: Path, *, overwrite: bool = False) -> dict[str, int]:
    seed = _load_json_object(seed_path)
    cache = _load_json_object(cache_path)
    merged = 0
    skipped = 0

    for raw_domain, raw_record in seed.items():
        domain = normalize_domain(f"https://{raw_domain}")
        record = None
        if isinstance(raw_record, dict):
            method = str(raw_record.get("fetch_method") or "")
            if method in {"http", "seo", "cloak", "cloak-profile", "archive", "fallback"}:
                record = StrategyRecord(
                    fetch_method=method,
                    detail=str(raw_record.get("detail") or ""),
                    success_count=int(raw_record.get("success_count") or 0),
                    failure_count=int(raw_record.get("failure_count") or 0),
                    last_success_at=str(raw_record.get("last_success_at") or ""),
                    last_failure_at=str(raw_record.get("last_failure_at") or ""),
                )
        if not domain or record is None:
            raise ValueError(f"invalid strategy seed for {raw_domain!r}: {raw_record!r}")
        if domain in cache and not overwrite:
            skipped += 1
            continue
        cache[domain] = _record_to_mapping(record)
        merged += 1

    _write_json_object(cache_path, cache)
    return {"merged": merged, "skipped": skipped, "total": len(cache)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="examples/fetch-strategies.media.seed.json")
    parser.add_argument("--cache", default="data/fetch-strategies.json")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = merge_seed(Path(args.seed), Path(args.cache), overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
