#!/usr/bin/env python3
"""Example #3 — Flashscore 1X2 odds via explicit API recipe load (not builtin).

Reproducible agent-facing pattern (offline, deterministic):
  1. Load the synthetic fixture (no network)
  2. Load examples/recipes/flashscore_odds.v1.json explicitly
  3. Run the generic executor with an injected fetcher
  4. Normalize fanout payloads in this example (not in core)

Flashscore is example-only — never auto-loaded. Prefer:

  API_RECIPE_PATHS=examples/recipes/flashscore_odds.v1.json \\
    supersocks-url-scraper --api-recipes '<match-url>'

Flashscore Terms of Use prohibit automated requests/scraping without express
consent (https://www.flashscore.com/terms-of-use/). This example never opens a
live socket (injected fetcher only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    SRC = ROOT / "src"
    if SRC.exists():
        sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(ROOT / "examples"))

from flashscore.odds_normalize import (  # noqa: E402
    DISCLAIMER,
    FLASHSCORE_TOS_WARNING,
    compact_odds_summary,
    normalize_fanout_result,
    normalize_prematch_1x2,
)
from supersocks_url_scraper.api_recipes import (  # noqa: E402
    execute_recipe,
    load_builtin_recipes,
    load_recipe_file,
    try_api_recipe,
)
from supersocks_url_scraper.api_recipes.security import SafeGetResult  # noqa: E402
from supersocks_url_scraper.reader import to_markdown  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
RECIPE_PATH = ROOT / "examples" / "recipes" / "flashscore_odds.v1.json"
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


def _reader_payload_from_run(run, *, match_url: str) -> dict:
    fanout = run.structured_data or {}
    structured = normalize_fanout_result(
        match_url=match_url,
        fanout_structured=fanout,
        warnings=list(run.warnings),
    )
    summary = compact_odds_summary(structured)
    payload = run.as_reader_dict(include_content=True)
    payload["title"] = f"Flashscore odds {structured.get('event_id')}"
    payload["summary"] = summary
    payload["structured_data"] = structured
    payload["content"] = json.dumps(structured, ensure_ascii=False, indent=2)
    return payload


def run_fixture(fixture_path: Path, *, markdown: bool = False) -> dict:
    if any(r.id == "flashscore-odds" for r in load_builtin_recipes()):
        raise SystemExit("flashscore-odds must not be a builtin recipe")
    recipe = load_recipe_file(RECIPE_PATH)
    if recipe.network.mode != "open":
        print(f"# warning: expected network.mode=open, got {recipe.network.mode}", file=sys.stderr)
    demo_url = json.loads(fixture_path.read_text(encoding="utf-8")).get("match_url") or DEMO_URL
    run = execute_recipe(
        demo_url,
        recipe,
        include_content=True,
        fetcher=_fixture_fetcher_factory(fixture_path),
        resolve_dns=False,
    )
    assert run.status == "ok", run.warnings
    payload = _reader_payload_from_run(run, match_url=demo_url)
    if markdown:
        print(to_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n# note: {DISCLAIMER}", file=sys.stderr)
    print(f"# note: {FLASHSCORE_TOS_WARNING}", file=sys.stderr)
    print(
        f"# load: API_RECIPE_PATHS={RECIPE_PATH} supersocks-url-scraper --api-recipes <url>",
        file=sys.stderr,
    )
    return payload


def demo_no_builtin_match() -> dict:
    """Show that a standard Flashscore URL does not match any builtin recipe."""
    payload = try_api_recipe(DEMO_URL, enabled=True, include_content=False)
    assert payload is None
    out = {
        "demo": "no_builtin_flashscore_match",
        "matched": False,
        "hint": f"API_RECIPE_PATHS={RECIPE_PATH} supersocks-url-scraper --api-recipes <url>",
    }
    print(json.dumps(out, indent=2))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flashscore 1X2 odds example via explicit recipe path (not builtin)"
    )
    parser.add_argument("--fixture", default=str(FIXTURE), help="Deterministic fixture JSON (default)")
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON")
    parser.add_argument("--self-check", action="store_true", help="Normalize fixture payloads and exit")
    parser.add_argument(
        "--demo-fallback",
        action="store_true",
        help="Show that Flashscore is not a builtin match (still offline)",
    )
    args = parser.parse_args()

    if args.self_check:
        raw = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        ok = 0
        for bookmaker_id, body in (raw.get("bookmakers") or {}).items():
            row = normalize_prematch_1x2(body, bookmaker_id=int(bookmaker_id), bookmaker_name=str(bookmaker_id))
            if row:
                ok += 1
        print(json.dumps({"normalized_bookmakers": ok, "disclaimer": DISCLAIMER, "live": False, "builtin": False}, indent=2))
        return 0

    if args.demo_fallback:
        demo_no_builtin_match()
        return 0

    run_fixture(Path(args.fixture), markdown=args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
