#!/usr/bin/env python3
"""Example #3 — Flashscore 1X2 odds via API recipes (fixture-only).

Reproducible agent-facing pattern (offline, deterministic):
  1. Load the synthetic fixture (no network)
  2. Run the versioned flashscore-odds recipe with an injected fetcher
  3. Receive compact JSON/Markdown with home/draw/away (+ opening)
  4. When live network is blocked, read_url degrades to HTTP→SEO→Cloak→archive

Flashscore Terms of Use prohibit automated requests and scraping without express
consent (https://www.flashscore.com/terms-of-use/). This example never opens a
live socket. There is no --live flag.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    SRC = Path(__file__).resolve().parents[1] / "src"
    if SRC.exists():
        sys.path.insert(0, str(SRC))

from supersocks_url_scraper.api_recipes import (  # noqa: E402
    DISCLAIMER,
    FLASHSCORE_TOS_WARNING,
    execute_recipe,
    load_builtin_recipes,
    normalize_prematch_1x2,
    try_api_recipe,
)
from supersocks_url_scraper.api_recipes.security import SafeGetResult  # noqa: E402
from supersocks_url_scraper.reader import to_markdown  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
DEMO_URL = "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34"


def _fixture_fetcher_factory(fixture_path: Path):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    samples = payload.get("bookmakers") or {}

    def fetcher(url: str, **kwargs):  # noqa: ANN003
        bookmaker_id = "141"
        if "bookmakerId=" in url:
            bookmaker_id = url.split("bookmakerId=", 1)[1].split("&", 1)[0]
        body = samples.get(str(bookmaker_id)) or samples.get(int(bookmaker_id)) or {
            "data": {"findPrematchOddsForBookmaker": {}}
        }
        raw = json.dumps(body).encode("utf-8")
        return SafeGetResult(
            url=url,
            final_url=url,
            status_code=200,
            content=raw,
            content_type="application/json",
            headers={"content-type": "application/json"},
            elapsed_ms=1,
        )

    return fetcher


def run_fixture(fixture_path: Path, *, markdown: bool = False) -> dict:
    recipes = [r for r in load_builtin_recipes() if r.id == "flashscore-odds"]
    if not recipes:
        raise SystemExit("builtin flashscore-odds recipe missing")
    recipe = recipes[0]
    if recipe.network.mode not in {"fixture_only", "off", "disabled", "never"}:
        print(
            f"# warning: expected fixture_only network mode, got {recipe.network.mode}",
            file=sys.stderr,
        )
    demo_url = json.loads(fixture_path.read_text(encoding="utf-8")).get("match_url") or DEMO_URL
    run = execute_recipe(
        demo_url,
        recipe,
        include_content=True,
        fetcher=_fixture_fetcher_factory(fixture_path),
        resolve_dns=False,
    )
    payload = run.as_reader_dict(include_content=True)
    if markdown:
        print(to_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n# note: {DISCLAIMER}", file=sys.stderr)
    print(f"# note: {FLASHSCORE_TOS_WARNING}", file=sys.stderr)
    return payload


def demo_network_blocked_fallback(fixture_path: Path) -> dict:
    """Show that without an injected fetcher, live Flashscore is blocked and falls back."""
    demo_url = json.loads(fixture_path.read_text(encoding="utf-8")).get("match_url") or DEMO_URL
    payload = try_api_recipe(demo_url, enabled=True, include_content=False)
    assert payload is not None
    assert payload.get("_api_recipe_fallback") is True
    print(json.dumps({
        "demo": "network_blocked_triggers_fallback",
        "status": payload.get("status"),
        "fallback": True,
        "warnings_head": (payload.get("warnings") or [])[:2],
    }, indent=2))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flashscore 1X2 odds via opt-in API recipes (fixture-only; no live mode)"
    )
    parser.add_argument("--fixture", default=str(FIXTURE), help="Deterministic fixture JSON (default)")
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON")
    parser.add_argument("--self-check", action="store_true", help="Normalize fixture payloads and exit")
    parser.add_argument(
        "--demo-fallback",
        action="store_true",
        help="Show live network block → fallback signal (still offline)",
    )
    args = parser.parse_args()

    if args.self_check:
        raw = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        ok = 0
        for bookmaker_id, body in (raw.get("bookmakers") or {}).items():
            row = normalize_prematch_1x2(body, bookmaker_id=int(bookmaker_id), bookmaker_name=str(bookmaker_id))
            if row:
                ok += 1
        print(json.dumps({"normalized_bookmakers": ok, "disclaimer": DISCLAIMER, "live": False}, indent=2))
        return 0

    if args.demo_fallback:
        demo_network_blocked_fallback(Path(args.fixture))
        return 0

    run_fixture(Path(args.fixture), markdown=args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
