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

## Agent-first route advice (recurrent needs)

Agents often hit the same site repeatedly. `read_url` can attach an optional,
machine-readable **`route_advice`** object that steers them toward a known
API recipe or toward **offline** discovery — without sniffing the browser,
capturing a HAR automatically, or activating anything.

```text
route_advice = {
  recommended: api_recipe | review_recipe | api_discovery | standard_pipeline,
  state: available_disabled | fixture_only | review_required | used | blocked | suggested,
  reason: string,
  recipe?: {id, version, network_mode, status, review_required},
  requires?: [string],
  next_command?: string,
  network_attempted: false | true
}
```

`recipe.review_required` is an explicit boolean mirroring `recipe.needs_review`:
`true` for `status=review_required`, `review_required=True`, or `disabled` recipes —
the agent must review before any execution.

The field is **absent** when there is no useful advice (default reads stay
unchanged). Advice matching is **offline** (local recipe files + URL match
only; no sockets).

**State priority** (first match wins): `used` → `review_required` → `blocked`
(recipe attempted but fell back) → `fixture_only` (`network.mode` in
`fixture_only`/`off`/`disabled`/`never`) → `available_disabled` (active recipe,
recipes off) → `suggested` (offline discovery).

| Situation | `state` / `recommended` | What the agent should do |
|-----------|-------------------------|--------------------------|
| Recipe produced the result | `used` / `api_recipe` | Prefer the structured recipe output |
| `status=review_required` / candidate | `review_required` / `review_recipe` | Do not execute; review offline, then activate deliberately |
| Recipe blocked (consent missing), pipeline fell back | `blocked` / `standard_pipeline` | Keep using fallback; set allowlist + consent if authorized |
| `network.mode=fixture_only` (or `off`/`disabled`/`never`) | `fixture_only` / `api_recipe` | Use fixture/demo or injected fetcher — **never** live; **no** enable command |
| Known active recipe, recipes disabled (`consent_required` / `open`, not fixture-only) | `available_disabled` / `api_recipe` | Enable explicitly (`--api-recipes` / `API_RECIPES=1` / `api_recipes:true`) |
| No recipe + `recurrent_need` or costly fetch (`cloak`/`archive`/…) (HTML-like) | `suggested` / `api_discovery` | Capture HAR **manually**, then `--discover-har` offline |
| PDF / image / social / non-HTML | *(no discovery advice)* | Stay on the standard pipeline |

Flags:

```bash
# Python
read_url(url, recurrent_need=True)
read_url(url, api_recipes=True)   # may attach used / fixture_only / blocked / …

# CLI
supersocks-url-scraper --recurrent https://example.com/app
API_RECIPE_PATHS=examples/recipes/flashscore_odds.v1.json \
  supersocks-url-scraper --api-recipes 'https://www.flashscore.com/match/…/?mid=…'

# HTTP JSON
{"url": "…", "recurrent_need": true}
```

`recurrent_need` defaults to `false`. It never triggers browser sniffing or HAR
capture inside this package.

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
| Builtin recipes | none (empty catalog) | optional reviewed builtins after deliberate ship |
| External examples | `examples/recipes/` (e.g. Flashscore #3, not auto-loaded) | more example adapters |
| Live network | off by default (`API_RECIPES=0`); per-recipe `network.mode` | controlled per-recipe activation |
| Discovery | offline HAR classifier + disabled candidate | richer candidate field mapping |
| Activation | manual, review-gated, explicit path load | guided review tooling |
| Execution | via `--api-recipes` + optional `API_RECIPE_PATHS` | same, plus per-recipe toggles |

---

## Lifecycle diagram

```text
  base scrape (HTTP → SEO → Cloak → archive)
        │
        ▼
  route_advice? (offline; recurrent_need / costly / partial / loaded recipe)
        │
        ├─ available_disabled → enable --api-recipes (and load API_RECIPE_PATHS)
        ├─ review_required    → validate offline; never auto-execute
        ├─ suggested          → manual HAR → --discover-har
        └─ used / blocked     → keep structured result or fallback pipeline
        │
  local .har (browser capture)
        │  (offline, opt-in)
        ▼
  HAR discovery  ──►  classified report (JSON + Markdown)
        │
        ▼
  candidate recipe (disabled, status=review_required, network.mode=fixture_only)
        │
        ▼
  review (human or agent; verify endpoint is public & read-only; Terms OK)
        │
        ▼
  explicit load + activation
    API_RECIPE_PATHS=./recipe.v1.json  +  --api-recipes / API_RECIPES=1
    (open → global opt-in alone; consent_required → allowlist+consent; fixture_only → never live)
        │
        ▼
  execution via read_url(api_recipes=True)  ──►  structured_data
        │
        ▼
  fallback: HTTP → SEO → Cloak → archive on any failure
```

No step after **discovery** is automatic. A candidate recipe is always written
disabled and can never execute or promote itself. Site demos under `examples/`
are never auto-loaded builtins.

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
| `endpoint.url_template` | `https://` endpoint (may use `{event_id}` / fanout placeholders) |
| `endpoint.allowed_hosts` | host allowlist enforced at request time |
| `endpoint.headers` | must never include Authorization/Cookie/token |
| `params.bindings` | optional map of template vars from URL query keys (+ pattern) |
| `params.fanout` / `params.bookmakers` | optional bounded fanout list for multi-GET recipes |
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
| `fixture_only` / `off` / `disabled` / `never` | never | injected fetchers / demos only |
| `consent_required` / `allowlist` | only with allowlist + exact consent phrase | optional generic policy |
| `open` / `allow` | yes (still needs global opt-in) | intentionally public endpoints |

For `consent_required` / `allowlist`, live access additionally requires
`API_RECIPE_LIVE_ALLOWLIST` to include the recipe id and
`API_RECIPE_LIVE_CONSENT` to equal the consent phrase. `open` / `allow` does not
use those two consent variables, but still requires the global recipes opt-in
and all HTTPS/GET/host/DNS/redirect safety gates. Candidate recipes are never
generated in an open mode.

`status` and `review_required` document the review state for humans and agents;
the hard runtime execution gate is `network.mode` plus `review_required` /
`disabled` status. Discovery always combines the review markers with
`network.mode: fixture_only` so a fresh candidate cannot perform a live request.

## Agent commands

Agents interact with the recipe layer through the same public API as the CLI:

- `discover_from_har(path)` → `DiscoveryReport`
- `classify_har_entry(entry)` → `CandidateEntry`
- `build_candidate_recipe(entry)` → disabled recipe dict
- `validate_recipe_schema(raw)` / `validate_recipe_dict(raw)` → error lists
- `load_builtin_recipes()` → empty by default (no site builtins)
- `load_recipes(extra_paths=[…])` → builtins + explicitly loaded paths
- `execute_recipe(url, recipe, fetcher=…, resolve_dns=False)` → `RecipeRunResult`
- `try_api_recipe(url, enabled=True, extra_recipe_paths=[…])` → reader-shaped payload or `None`
- `read_url(url, api_recipes=True, api_recipe_paths=[…])` → reader result (structured recipe or fallback)
- `read_url(url, recurrent_need=True)` → may attach offline `route_advice` (api_discovery when suitable)
- `build_route_advice(...)` → discrete advice dict or `None` (no network)

## Guardrails

- GET / HTTPS only; no Authorization / Cookie / token headers ever.
- Host allowlists + private/loopback blocks (name and DNS), each redirect hop validated.
- Size / timeout / fanout / cadence bounds.
- Sensitive headers and query params scrubbed from outputs.
- 401 / 403 / 429 surfaced without retry storms.
- Automatic degradation to HTTP → SEO → Cloak → archive on any failure.
- StrategyCache never stores an API-recipe route.
- Candidate recipes are always disabled (`review_required`, `fixture_only`).
- `route_advice` never opens sockets, never captures HAR, never auto-activates.

## Example #3: Flashscore (not builtin / not supported)

Flashscore is a **case study only**. The recipe and odds normalization live under
`examples/` and are **not** imported or auto-loaded by the runtime.

```bash
# Offline fixture demo
python examples/flashscore_odds.py
python examples/flashscore_odds_comparison.py

# Explicit load (example recipe uses network.mode=open — global opt-in alone)
API_RECIPE_PATHS=examples/recipes/flashscore_odds.v1.json \
  supersocks-url-scraper --api-recipes 'https://www.flashscore.com/match/…/?mid=…'
```

Observed browser pattern encoded in the example recipe (undocumented/unstable):
host `2.ds.lsapp.eu`, `GET /pq_graphql`, `_hash=ole2`, `eventId` from query
`mid`, bounded `bookmakerId` fanout, `betType=HOME_DRAW_AWAY`,
`betScope=FULL_TIME`. Odds transforms stay in `examples/flashscore/`; core only
runs the declarative GET/fanout executor. Prefer fixtures; live GETs are the
operator's responsibility under Flashscore Terms of Use. See
[`docs/CASE_STUDY_FLASHSCORE_ODDS.md`](CASE_STUDY_FLASHSCORE_ODDS.md).
