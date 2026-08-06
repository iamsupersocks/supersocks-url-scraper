#!/usr/bin/env python3
"""Example #3b — Base HTML scraper vs JSON recipe (deterministic, offline).

Runs the SAME synthetic Flashscore match case through two read paths and prints
a side-by-side comparison:

  1. Base HTML scraper   → generic page text (extract_article on a fixture page)
  2. JSON recipe         → normalized 1X2 odds via explicit example recipe + injected fetcher

Both are fixture-driven and never touch the network. Flashscore is example-only
(not builtin/supported). Load deliberately:

  API_RECIPE_PATHS=examples/recipes/flashscore_odds.v1.json \\
    supersocks-url-scraper --api-recipes '<match-url>'
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
    compact_odds_summary,
    normalize_fanout_result,
)
from supersocks_url_scraper.api_recipes import (  # noqa: E402
    execute_recipe,
    load_builtin_recipes,
    load_recipe_file,
    try_api_recipe,
)
from supersocks_url_scraper.api_recipes.security import SafeGetResult  # noqa: E402
from supersocks_url_scraper.reader import extract_article, to_markdown  # noqa: E402

FIXTURE_JSON = ROOT / "tests" / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
FIXTURE_HTML = ROOT / "tests" / "fixtures" / "api_recipes" / "flashscore_match_page.html"
RECIPE_PATH = ROOT / "examples" / "recipes" / "flashscore_odds.v1.json"
DEMO_URL = "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34"


def _fixture_fetcher_factory(fixture_path: Path):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    samples = payload.get("bookmakers") or {}

    def fetcher(url: str, **kwargs):  # noqa: ANN003
        bookmaker_id = "141"
        if "bookmakerId=" in url:
            bookmaker_id = url.split("bookmakerId=", 1)[1].split("&", 1)[0]
        body = samples.get(str(bookmaker_id)) or {
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


def run_base_html_scraper() -> dict:
    """Run the generic HTML extraction path on the synthetic match page."""
    markup = FIXTURE_HTML.read_text(encoding="utf-8")
    article = extract_article(markup, DEMO_URL)
    return {
        "path": "base_html_scraper",
        "fetch_method": "http",
        "title": article.title,
        "summary": " ".join(article.text.split())[:400],
        "method": article.method,
        "provenance": "Generic HTML → article text extraction (no API recipe).",
        "captured_at": None,
        "structured_data": None,
        "fallback_signal": False,
        "note": "Generic prose; odds appear only as inline text, not a typed 1X2 market.",
    }


def _normalized_recipe_payload() -> dict:
    if any(r.id == "flashscore-odds" for r in load_builtin_recipes()):
        raise SystemExit("flashscore-odds must not be a builtin recipe")
    recipe = load_recipe_file(RECIPE_PATH)
    run = execute_recipe(
        DEMO_URL,
        recipe,
        include_content=True,
        fetcher=_fixture_fetcher_factory(FIXTURE_JSON),
        resolve_dns=False,
    )
    structured = normalize_fanout_result(
        match_url=DEMO_URL,
        fanout_structured=run.structured_data or {},
        warnings=list(run.warnings),
    )
    payload = run.as_reader_dict(include_content=True)
    payload["title"] = f"Flashscore odds {structured.get('event_id')}"
    payload["summary"] = compact_odds_summary(structured)
    payload["structured_data"] = structured
    payload["content"] = json.dumps(structured, ensure_ascii=False, indent=2)
    return payload


def run_json_recipe() -> dict:
    """Run the example flashscore-odds JSON recipe with an injected offline fetcher."""
    payload = _normalized_recipe_payload()
    return {
        "path": "json_recipe",
        "fetch_method": payload.get("fetch_method"),
        "title": payload.get("title"),
        "summary": payload.get("summary"),
        "method": "api-recipe",
        "provenance": payload.get("structured_data", {}).get("provenance"),
        "captured_at": (payload.get("api_recipe") or {}).get("captured_at"),
        "structured_data": payload.get("structured_data"),
        "fallback_signal": False,
        "note": "Typed, normalized 1X2 market with home/draw/away (+ opening) per bookmaker.",
    }


def compare(*, markdown: bool = False) -> dict:
    base = run_base_html_scraper()
    recipe = run_json_recipe()
    comparison = {
        "demo": "flashscore_base_html_vs_json_recipe",
        "url": DEMO_URL,
        "offline": True,
        "builtin": False,
        "recipe_path": str(RECIPE_PATH.relative_to(ROOT)),
        "paths": {
            "base_html_scraper": base,
            "json_recipe": recipe,
        },
        "difference": {
            "base_html_scraper": "generic prose with odds as inline text",
            "json_recipe": "typed 1X2 market (home/draw/away + opening) with provenance/captured_at",
        },
        "disclaimer": DISCLAIMER,
    }

    if markdown:
        left = base
        right = recipe
        lines = [
            "# Base HTML scraper vs JSON recipe (deterministic, offline)",
            "",
            f"URL: `{DEMO_URL}` — all values are synthetic fixtures, never live.",
            f"Recipe path: `{RECIPE_PATH.relative_to(ROOT)}` (explicit load; not builtin).",
            "",
            "### 1. Base HTML scraper (generic text)",
            "",
            f"- title: `{left['title']}`",
            f"- extract method: `{left['method']}`",
            f"- provenance: {left['provenance']}",
            f"- captured_at: `{left['captured_at']}`",
            f"- fallback_signal: `{left['fallback_signal']}`",
            "",
            f"**summary:** {left['summary']}",
            "",
            "### 2. JSON recipe (normalized 1X2)",
            "",
            f"- fetch_method: `{right['fetch_method']}`",
            f"- summary: {right['summary']}",
            f"- provenance: {right['provenance']}",
            f"- captured_at: `{right['captured_at']}`",
            f"- fallback_signal: `{right['fallback_signal']}`",
            "",
            "**structured_data:**",
            "",
            "```json",
            json.dumps(right["structured_data"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        recipe_payload = _normalized_recipe_payload()
        lines += ["**Markdown render of the recipe payload:**", ""]
        lines += [to_markdown(recipe_payload).rstrip(), ""]
        print("\n".join(lines), end="")
    else:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic offline comparison: base HTML vs explicit example recipe"
    )
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON")
    parser.add_argument(
        "--show-fallback",
        action="store_true",
        help="Also show that without an explicit recipe path there is no builtin match",
    )
    args = parser.parse_args()

    compare(markdown=args.markdown)

    if args.show_fallback:
        print("\n--- no builtin match demo ---")
        payload = try_api_recipe(DEMO_URL, enabled=True, include_content=False)
        assert payload is None
        print(
            json.dumps(
                {
                    "demo": "no_builtin_flashscore_match",
                    "matched": False,
                    "hint": f"API_RECIPE_PATHS={RECIPE_PATH} supersocks-url-scraper --api-recipes <url>",
                },
                indent=2,
            )
        )
    print(f"\n# note: {DISCLAIMER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
