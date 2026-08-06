#!/usr/bin/env python3
"""Example #3b — Base HTML scraper vs JSON recipe (deterministic, offline).

Runs the SAME synthetic Flashscore match case through two read paths and prints
a side-by-side comparison:

  1. Base HTML scraper   → generic page text (extract_article on a fixture page)
  2. JSON recipe         → normalized 1X2 odds (flashscore-odds, injected offline fetcher)

Both are fixture-driven and never touch the network. There is no live benchmark:
every market value comes from a local synthetic fixture, so the data comparison
is deterministic and reproducible (the capture timestamp changes per run). It
shows the *shape* of the difference — generic
prose versus a typed, normalized 1X2 market with provenance / captured_at /
fallback — not real market data.

Flashscore Terms of Use prohibit automated requests/scraping without express
consent (https://www.flashscore.com/terms-of-use/). This example never opens a
live socket.
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
    execute_recipe,
    load_builtin_recipes,
    try_api_recipe,
)
from supersocks_url_scraper.api_recipes.security import SafeGetResult  # noqa: E402
from supersocks_url_scraper.reader import extract_article, to_markdown  # noqa: E402

FIXTURE_JSON = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
)
FIXTURE_HTML = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "api_recipes" / "flashscore_match_page.html"
)
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


def run_json_recipe() -> dict:
    """Run the flashscore-odds JSON recipe with an injected offline fetcher."""
    recipes = [r for r in load_builtin_recipes() if r.id == "flashscore-odds"]
    if not recipes:
        raise SystemExit("builtin flashscore-odds recipe missing")
    recipe = recipes[0]
    run = execute_recipe(
        DEMO_URL,
        recipe,
        include_content=True,
        fetcher=_fixture_fetcher_factory(FIXTURE_JSON),
        resolve_dns=False,
    )
    payload = run.as_reader_dict(include_content=True)
    fallback = bool(payload.pop("_api_recipe_fallback", False))
    return {
        "path": "json_recipe",
        "fetch_method": payload.get("fetch_method"),
        "title": payload.get("title"),
        "summary": payload.get("summary"),
        "method": "api-recipe",
        "provenance": payload.get("structured_data", {}).get("provenance"),
        "captured_at": (payload.get("api_recipe") or {}).get("captured_at"),
        "structured_data": payload.get("structured_data"),
        "fallback_signal": fallback,
        "note": "Typed, normalized 1X2 market with home/draw/away (+ opening) per bookmaker.",
    }


def compare(*, markdown: bool = False) -> dict:
    base = run_base_html_scraper()
    recipe = run_json_recipe()
    comparison = {
        "demo": "flashscore_base_html_vs_json_recipe",
        "url": DEMO_URL,
        "offline": True,
        "paths": {
            "base_html_scraper": base,
            "json_recipe": recipe,
        },
        "difference": {
            "base_html_scraper": "generic prose with odds as inline text",
            "json_recipe": "typed 1X2 market (home/draw/away + opening) with provenance/captured_at/fallback",
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
        # Render the recipe reader payload through to_markdown too for the odds table.
        recipe_payload = _recipe_reader_payload()
        if recipe_payload is not None:
            lines += ["**Markdown render of the recipe payload:**", ""]
            lines += [to_markdown(recipe_payload).rstrip(), ""]
        print("\n".join(lines), end="")
    else:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return comparison


def _recipe_reader_payload():
    recipes = [r for r in load_builtin_recipes() if r.id == "flashscore-odds"]
    if not recipes:
        return None
    recipe = recipes[0]
    run = execute_recipe(
        DEMO_URL,
        recipe,
        include_content=True,
        fetcher=_fixture_fetcher_factory(FIXTURE_JSON),
        resolve_dns=False,
    )
    return run.as_reader_dict(include_content=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic offline comparison: base HTML scraper vs JSON recipe for the same synthetic Flashscore case"
    )
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON")
    parser.add_argument(
        "--show-fallback",
        action="store_true",
        help="Also show that without an injected fetcher the recipe errors and signals fallback",
    )
    args = parser.parse_args()

    comparison = compare(markdown=args.markdown)

    if args.show_fallback:
        print("\n--- fallback demo (no injected fetcher) ---")
        payload = try_api_recipe(DEMO_URL, enabled=True, include_content=False)
        assert payload is not None and payload.get("_api_recipe_fallback") is True
        print(
            json.dumps(
                {
                    "demo": "network_blocked_triggers_fallback",
                    "status": payload.get("status"),
                    "fallback": True,
                    "warnings_head": (payload.get("warnings") or [])[:2],
                },
                indent=2,
            )
        )
    print(f"\n# note: {DISCLAIMER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
