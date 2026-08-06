# API recipes — adapter generation, discovery, review, activation

This document explains the **API-recipe layer** of `supersocks-url-scraper`:
what it is, what it is *not*, how an endpoint is discovered from a HAR, how a
candidate becomes a reviewed, activated recipe, and how everything degrades
safely back to the normal reader pipeline.

## One sentence

**We do not create an API.** We generate a small, versioned, read-only
**adapter** that points at an endpoint that already exists, and we only ever run
it when an operator has explicitly reviewed and activated it.

---

## Adapter, not API provider

The recipe layer is not a scraper factory and not a way to "make an API" from a
website. A recipe is a **declarative adapter** for one existing public endpoint:

- it describes *how to match* a page URL (`match.host_roots`, `path_regex`),
- it describes *how to call* the endpoint (`endpoint.method = GET`,
  `endpoint.url_template`, `endpoint.allowed_hosts`),
- it describes *what to keep* (`response.schema`, normalization rules),
- it is versioned (`id@v1`), opt-in, and read-only.

A recipe never adds new capabilities to a site. It only structures data an
endpoint already returns. If the endpoint disappears or changes, the recipe
fails and the reader falls back to the normal pipeline — it never fakes data.

### Current vs future state

| Aspect | Current (this release) | Future (not shipped) |
|--------|------------------------|-----------------------|
| Shipped recipes | `flashscore-odds@v1` (fixture-only) | more opt-in adapters after review |
| Live network | blocked by default (`fixture_only`); never for Flashscore without consent | controlled per-recipe activation |
| Discovery | offline HAR classifier + disabled candidate | richer candidate field mapping |
| Activation | manual, review-gated | guided review tooling |
| Execution | via `--api-recipes` / `API_RECIPES=1` | same, plus per-recipe toggles |

---

## Lifecycle diagram

```text
  local .har (browser capture)
        │  (offline, opt-in)
        ▼
  HAR discovery  ──►  classified report (JSON + Markdown)
        │
        ▼
  candidate recipe (disabled, status=review_required, network.mode=fixture_only)
        │
        ▼
  review (human or agent reads the report; verifies endpoint is public &
          read-only, within the site's terms; no credentials needed)
        │
        ▼
  activation (operator selects an appropriate network mode after review;
              consent_required also needs allowlist + consent attestation)
        │
        ▼
  execution via read_url(api_recipes=True)  ──►  structured_data
        │
        ▼
  fallback: HTTP → SEO → Cloak → archive on any failure
```

No step after **discovery** is automatic. A candidate recipe is always written
disabled and can never execute or promote itself.

---

## Offline HAR discovery

`discover_from_har(path)` reads a local HAR file and classifies every exchange.
It never opens a socket.

### Keeping / exclusion rules

Kept (candidate) only when **all** hold:

- method is `GET` (no writes)
- scheme is `https`
- host is public (not private/loopback/link-local) and URL has no credentials
- request carries no `Authorization` / `Cookie` / token headers
- URL has no sensitive query params (token, api_key, auth, secret, session, …)
- response is JSON (content-type or JSON-looking body)
- response is not an error (HTTP < 400) and not a redirect
- response body ≤ `max_bytes` (default 512 KB)

Excluded otherwise, with a machine-readable reason:

- `excluded: non-GET method`
- `excluded: not https`
- `excluded: private/loopback/local host`
- `excluded: credentials in URL`
- `excluded: sensitive header present`
- `excluded: sensitive query param present`
- `excluded: response is not JSON`
- `excluded: response body too large`
- `excluded: HTTP >= 400`
- `excluded: redirect 3xx found`

### Redaction

Sensitive query-param values and sensitive headers are replaced with
`[REDACTED]` in the report and in any candidate recipe. A discovered URL is
never emitted with its raw token/secret values.

### Output

`render_report_json` / `render_report_markdown` produce a classified report:
candidate URLs (redacted), excluded exchanges with reasons, and a single
**candidate recipe** (the top candidate) that is always:

- `status: review_required`
- `network.mode: fixture_only` (blocked)
- `review_required: true`

`write_report(..., out_dir=...)` writes `<prefix>-<stamp>.json`,
`<prefix>-<stamp>.md`, and `<prefix>-candidate-recipe.v1.json`.

## Schema v1 and validation

The shipped recipe format is described by an embedded JSON Schema
(`RECIPE_SCHEMA_V1`, also mirrored to `api_recipes/schemas/recipe.v1.json` and
included in the wheel). `validate_recipe_schema(raw)` checks a recipe document
against the schema; `validate_recipe_dict(raw)` in the engine enforces the same
rules at runtime. `supersocks-url-scraper --validate-recipe FILE …` validates
one or more recipe files offline against both.

## CLI (all offline)

```bash
# Discover from a local HAR, print JSON report + candidate recipe
supersocks-url-scraper --discover-har capture.har

# Write report files to a directory
supersocks-url-scraper --discover-har capture.har --discovery-out-dir ./out

# Validate a recipe file against schema v1 + runtime rules
supersocks-url-scraper --validate-recipe my-recipe.v1.json
```

## Recipe fields

| Field | Meaning |
|-------|---------|
| `id` / `version` | unique recipe key (`id@v1`) |
| `match.host_roots` | host roots matched (exact or subdomain) |
| `match.path_regex` / `query_keys` | further URL matching |
| `endpoint.method` | `GET` only |
| `endpoint.url_template` | `https://` endpoint (may use `{event_id}`-style placeholders) |
| `endpoint.allowed_hosts` | host allowlist enforced at request time |
| `endpoint.headers` | must never include Authorization/Cookie/token |
| `network.mode` | `fixture_only`/`off`/`disabled` (blocked) · `consent_required` · `open`/`allow` |
| `network.consent_phrase` | exact phrase required for consent-gated live use |
| `confidence` | 0..1, surfaced in output |
| `ttl_seconds` | freshness TTL |
| `response.schema` | structured-data schema id |
| `warnings` | surfaced to the caller |
| `fallback` | default `http_seo_cloak_archive` |
| `status` / `review_required` | gate markers (`review_required`, `disabled`, `active`) |

## Network modes

| Mode | Live GETs? | Meaning |
|------|-----------|---------|
| `fixture_only` / `off` / `disabled` / `never` | never | default; injected fetchers only |
| `consent_required` / `allowlist` | only with allowlist + exact consent phrase | for gated sites (e.g. Flashscore) |
| `open` / `allow` | yes (still opt-in) | for intentionally public endpoints |

For `consent_required` / `allowlist`, live access additionally requires
`API_RECIPE_LIVE_ALLOWLIST` to include the recipe id and
`API_RECIPE_LIVE_CONSENT` to equal the consent phrase. `open` / `allow` does not
use those two consent variables, but still requires the global recipes opt-in
and all HTTPS/GET/host/DNS/redirect safety gates. Candidate recipes are never
generated in an open mode. The shipped Flashscore recipe stays `fixture_only`.

`status` and `review_required` document the review state for humans and agents;
the hard runtime execution gate is `network.mode`. Discovery always combines
the review markers with `network.mode: fixture_only` so a fresh candidate
cannot perform a live request.

## Agent commands

Agents interact with the recipe layer through the same public API as the CLI:

- `discover_from_har(path)` → `DiscoveryReport`
- `classify_har_entry(entry)` → `CandidateEntry`
- `build_candidate_recipe(entry)` → disabled recipe dict
- `validate_recipe_schema(raw)` / `validate_recipe_dict(raw)` → error lists
- `load_builtin_recipes()` → shipped recipes
- `execute_recipe(url, recipe, fetcher=…, resolve_dns=False)` → `RecipeRunResult`
- `try_api_recipe(url, enabled=True)` → reader-shaped payload or `None`
- `read_url(url, api_recipes=True)` → reader result (structured recipe or fallback)

## Guardrails

- GET / HTTPS only; no Authorization / Cookie / token headers ever.
- Host allowlists + private/loopback blocks (name and DNS), each redirect hop validated.
- Size / timeout / fanout / cadence bounds.
- Sensitive headers and query params scrubbed from outputs.
- 401 / 403 / 429 surfaced without retry storms.
- Automatic degradation to HTTP → SEO → Cloak → archive on any failure.
- StrategyCache never stores an API-recipe route.
- Candidate recipes are always disabled (`review_required`, `fixture_only`).

## Flashscore example

See [`examples/flashscore_odds.py`](../examples/flashscore_odds.py) (fixture-only
recipe) and [`examples/flashscore_odds_comparison.py`](../examples/flashscore_odds_comparison.py)
(offline, deterministic base-HTML-scraper vs JSON-recipe comparison of the same
synthetic case). Flashscore ToS prohibit automated requests/scraping without
express consent; the shipped example never opens a live socket.
