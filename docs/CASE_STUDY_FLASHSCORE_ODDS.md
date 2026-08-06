# Case study #3: Flashscore 1X2 odds (example only — not builtin)

This case study shows how the **general** API-recipe brick can adapt a
match-style URL into a compact 1X2 odds snapshot. **Flashscore is not a
builtin, not auto-loaded, and not a supported special integration** — the
recipe and odds normalization live under `examples/`.

Flashscore Terms of Use prohibit burdening their servers with automated requests
and scraping without express consent
(https://www.flashscore.com/terms-of-use/). Prefer offline fixtures. Live GETs
via explicit recipe load are the operator's responsibility under current Terms.
No live HAR or live odds data is committed.

## Artifacts

- [`examples/flashscore_odds.py`](../examples/flashscore_odds.py)
- [`examples/flashscore_odds_comparison.py`](../examples/flashscore_odds_comparison.py) — base HTML scraper vs JSON recipe (offline, deterministic)
- [`examples/recipes/flashscore_odds.v1.json`](../examples/recipes/flashscore_odds.v1.json) — explicit load only
- [`examples/flashscore/odds_normalize.py`](../examples/flashscore/odds_normalize.py) — example-side normalization (not core)
- Generic brick: [`src/supersocks_url_scraper/api_recipes/`](../src/supersocks_url_scraper/api_recipes/)
- Fixture: [`tests/fixtures/api_recipes/flashscore_odds_sample.json`](../tests/fixtures/api_recipes/flashscore_odds_sample.json)
- Fixture: [`tests/fixtures/api_recipes/flashscore_match_page.html`](../tests/fixtures/api_recipes/flashscore_match_page.html)
- Layer docs: [`docs/API_RECIPES.md`](API_RECIPES.md)

## Architecture (general)

```text
base scrape → route_advice → (optional) HAR discovery → review
  → explicit API_RECIPE_PATHS load + --api-recipes → API fanout → fallback
```

Core executes declarative HTTPS GET + bounded fanout (`params.bindings` /
`params.fanout`). Odds-specific transforms stay in the example.

## Observed browser pattern (example recipe)

- Host: `2.ds.lsapp.eu`
- Method/path: `GET /pq_graphql`
- Query: `_hash=ole2`, `eventId={event_id}` (declared in `params.bindings`), bounded
  `bookmakerId` fanout (`params.fanout` + example `bookmakers` list), `betType=HOME_DRAW_AWAY`,
  `betScope=FULL_TIME`
- `network.mode=open` — with global `api_recipes` opt-in alone (no allowlist /
  consent phrase). Still not auto-loaded.

## Offline demos

```bash
python examples/flashscore_odds.py
python examples/flashscore_odds.py --markdown
python examples/flashscore_odds.py --self-check
python examples/flashscore_odds.py --demo-fallback   # shows no builtin match

python examples/flashscore_odds_comparison.py
python examples/flashscore_odds_comparison.py --markdown
python examples/flashscore_odds_comparison.py --markdown --show-fallback
```

## Explicit activation (no auto-load)

```bash
# Standard read: no builtin Flashscore recipe → normal scrape pipeline
supersocks-url-scraper https://www.flashscore.com/match/football/demo/?mid=Ab12Cd34

# Explicit example load + global opt-in
API_RECIPE_PATHS=examples/recipes/flashscore_odds.v1.json \
  supersocks-url-scraper --api-recipes https://www.flashscore.com/match/football/demo/?mid=Ab12Cd34
```

Environment reminders:

- `API_RECIPES=0` (default) — recipes off
- `API_RECIPE_PATHS` — colon-separated external recipe files/dirs (required for this example)
- `network.mode=open` on the example recipe → no allowlist/consent phrase
- `fixture_only` / `consent_required` remain available as **generic** optional policies

## What the comparison shows

- **Base HTML scraper** (`extract_article` on `flashscore_match_page.html`):
  generic prose; odds appear only as inline text.
- **JSON recipe** (explicit example path + injected fetcher + example normalizer):
  typed `home`/`draw`/`away` (+ opening) per bookmaker, provenance, `captured_at`.

## Guardrails preserved

GET/HTTPS, `allowed_hosts`, SSRF/DNS/redirect checks, response caps, forbidden
header scrubbing, rate/fanout bounds, fallback to HTTP→SEO→Cloak→archive, no
secrets. StrategyCache never stores API-recipe routes. `review_required`
candidates remain a hard gate.
