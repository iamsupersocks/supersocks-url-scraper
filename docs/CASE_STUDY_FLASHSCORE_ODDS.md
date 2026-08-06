# Case study #3: Flashscore 1X2 odds (fixture-only)

Public, share-safe example showing how `supersocks-url-scraper` exposes an
**optional, versioned API-recipe layer**, then how an agent consumes a compact
1X2 odds snapshot. **No live Flashscore connector is shipped or enabled.**

Flashscore Terms of Use prohibit burdening their servers with automated requests
and prohibit scraping / aggregating site content without express consent
(https://www.flashscore.com/terms-of-use/). This case study is therefore
**offline, fixture-driven, and non-network**. Synthetic odds values are
fictional and **not betting advice**.

Companion code:

- [`examples/flashscore_odds.py`](../examples/flashscore_odds.py)
- [`examples/flashscore_odds_comparison.py`](../examples/flashscore_odds_comparison.py) — base HTML scraper vs JSON recipe (offline, deterministic)
- [`src/supersocks_url_scraper/api_recipes/`](../src/supersocks_url_scraper/api_recipes/)
- Fixture: [`tests/fixtures/api_recipes/flashscore_odds_sample.json`](../tests/fixtures/api_recipes/flashscore_odds_sample.json)
- Fixture: [`tests/fixtures/api_recipes/flashscore_match_page.html`](../tests/fixtures/api_recipes/flashscore_match_page.html)
- Layer docs: [`docs/API_RECIPES.md`](API_RECIPES.md)

## Objectif

Demonstrate a bounded pattern an agent can reuse:

1. Match a public Flashscore-style match URL (`?mid=…`).
2. Extract the event id.
3. Normalize a small bookmaker set into `home` / `draw` / `away` (+ `opening` when present).
4. Emit JSON/Markdown with `captured_at`, provenance, and a non-advice disclaimer.
5. On recipe failure or network block, degrade to HTTP → SEO → Cloak → archive.

The example never opens a live socket. Live HTTPS GET for this recipe stays
impossible while `network.mode` is `fixture_only` (the shipped default).

## Parcours

### Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

### Offline agent example (default)

```bash
python examples/flashscore_odds.py
python examples/flashscore_odds.py --markdown
python examples/flashscore_odds.py --self-check
python examples/flashscore_odds.py --demo-fallback
```

Expected: structured JSON with `fetch_method: "api-recipe"`, several bookmaker
rows, `api_recipe.id = flashscore-odds`, and the disclaimer. `--demo-fallback`
shows that without an injected fetcher the recipe errors with a ToS/consent
warning and signals fallback (`_api_recipe_fallback`).

### Base HTML scraper vs JSON recipe (deterministic, offline)

```bash
python examples/flashscore_odds_comparison.py
python examples/flashscore_odds_comparison.py --markdown
python examples/flashscore_odds_comparison.py --markdown --show-fallback
```

This runs the **same synthetic match case** through two read paths and prints a
side-by-side comparison, with **no live benchmark**:

- **Base HTML scraper** (`extract_article` on `flashscore_match_page.html`):
  generic prose — the match title, team names, and odds appear only as inline
  text (`Betclic 2.10 3.25 3.40 …`), with no `captured_at`, no typed structure,
  and no provenance string.
- **JSON recipe** (`execute_recipe` with the injected offline fetcher):
  a typed, normalized 1X2 market — `home`/`draw`/`away` (+ `opening` when
  present) per bookmaker, plus `provenance`, `captured_at`, a disclaimer, and a
  fallback signal.

The comparison uses deterministic synthetic fixture values; only the generated
`captured_at` timestamp changes between runs. It shows the *shape* of the
difference (generic prose versus a typed, normalized market) rather than real
market data. `--show-fallback` additionally
proves that without an injected fetcher the recipe errors and signals fallback
(`_api_recipe_fallback = true`).

### Discovery from a local HAR (offline, opt-in)

The same adapter concept can be bootstrapped from a browser HAR capture with no
network. `--discover-har` keeps only public HTTPS GET JSON exchanges, excludes
auth/cookies/tokens / writes / private hosts / non-JSON / oversized bodies,
redacts sensitive params and headers, and emits a classified report plus a
**disabled** candidate recipe (`status: review_required`,
`network.mode: fixture_only`). It never executes or promotes the candidate. See
[`docs/API_RECIPES.md`](API_RECIPES.md) for the lifecycle
HAR → candidate → review → activation → execution → fallback.

### Opt-in reader flag (still no live Flashscore)

```bash
# API recipes are opt-in. Flashscore remains fixture_only — matching URLs
# will not perform live odds GETs; the reader falls through to the normal pipeline.
supersocks-url-scraper --api-recipes https://www.flashscore.com/match/football/demo/?mid=Ab12Cd34
```

Environment mirrors:

- `API_RECIPES=0` (default) — recipes off
- `API_RECIPE_PATHS` — extra recipe JSON files/dirs
- Live gates (not used by the shipped Flashscore recipe while `fixture_only`):
  - `API_RECIPE_LIVE_ALLOWLIST` — comma/space list of recipe ids
  - `API_RECIPE_LIVE_CONSENT=I_HAVE_EXPRESS_WRITTEN_PERMISSION`

### Historical endpoint shape (documentation only)

Older internal notes described a public GET form:

`https://global.ds.lsapp.eu/odds/pq_graphql?_hash=ope2&eventId=…&bookmakerId=…&betType=HOME_DRAW_AWAY&betScope=FULL_TIME`

That shape is undocumented and may change. **This repository does not benchmark
it live** and does not enable automated access by default. Any latency claims
from older experiments are hypotheses only and are intentionally omitted here.

## Limites

- Fixture values are synthetic; do not treat them as market prices.
- No login, cookies, tokens, Authorization headers, CAPTCHA bypass, or private hosts.
- StrategyCache still stores only `http` / `seo` / `cloak` / `archive` routes — never API recipes.
- Flashscore ToS: automated requests / scraping without express consent are prohibited.
- No live benchmark is published with this case study.

## Résultats (fixture)

Running `python examples/flashscore_odds.py --self-check` normalizes the synthetic
bookmaker payloads offline. A typical fixture run yields multiple 1X2 rows (empty
bookmaker stubs are skipped) plus:

- `structured_data.kind = flashscore_odds_1x2`
- `disclaimer` stating the snapshot is not betting advice
- `provenance` noting fixture-only / ToS constraints

Deterministic offline artifacts (committed under `docs/data/`):

- [`docs/data/flashscore_base_vs_recipe_compare.md`](data/flashscore_base_vs_recipe_compare.md) — the base-HTML-scraper vs JSON-recipe comparison from `examples/flashscore_odds_comparison.py --markdown`.
- [`docs/data/api-discovery-demo-*.md`](data/) — HAR discovery report and the disabled `review_required` candidate recipe for the synthetic HAR fixture.

## Reproduction for agents

```text
goal: obtain compact 1X2 context for a match URL without scraping Flashscore live
steps:
  1. Prefer examples/flashscore_odds.py (fixture fetcher)
  2. Or call execute_recipe(..., fetcher=offline_fetcher)
  3. Never enable live Flashscore GETs unless you have express written permission
     and you deliberately change network.mode away from fixture_only with
     allowlist + consent env vars
  4. Always surface disclaimer + captured_at; never present odds as a tip
```

## Sécurité (couche générique)

The shared API-recipe engine enforces:

- GET / HTTPS only
- host allowlists + private/loopback / dangerous-redirect blocks
- size / timeout / fanout / cadence bounds
- sensitive header scrubbing; blocked Authorization/Cookie on requests
- 401 / 403 / 429 surfaced without retry storms
- automatic degradation to the standard reader pipeline
