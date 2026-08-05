# Blue-Eyes White Dragon scraping method

This method shows how to use the existing `supersocks-url-scraper` HTTP → SEO →
browser fetch primitives to collect a public, anonymized price snapshot for
**Blue-Eyes White Dragon** / **Dragon Blanc aux Yeux Bleus**.

It is intentionally narrow:

- no seller names or user profile URLs in the output
- no raw HTML, cookies, tokens, or browser profiles written to the repository
- no CAPTCHA solving, login, consent acceptance, or access-challenge bypass
- version floor prices and live offer rows stay in separate populations
- edition / référence analysis prefers set label + public product path over
  global quartiles; official printed codes (`LOB-001`, …) are never invented

For a didactic walkthrough of a dated live snapshot (edition-first tables,
pipeline diagram, 403/429 caveats, segment tables, and reading guide), see
[`BLUE_EYES_WHITE_DRAGON_ANALYSIS_REPORT.md`](BLUE_EYES_WHITE_DRAGON_ANALYSIS_REPORT.md).
Do not duplicate market conclusions here — keep this file as the short operator
howto.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[full,browser,test]'
```

## Source et provenance

**Provider:** Cardmarket. Named here so provenance stays honest; titles, file
names, CLI description, and the main report narrative stay Dragon Blanc /
Blue-Eyes focused.

Use only public pages you are allowed to query:

| Role | URL |
| --- | --- |
| Card hub | `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon` |
| All versions | `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions` |
| FR canonical hub | `https://www.cardmarket.com/fr/YuGiOh/Cards/BlueEyes-White-Dragon` |
| Example product | `https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare` |
| Official products list | `https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_3.json` |
| Official price guide | `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_3.json` |

Prefer the English Versions page plus explicit product pages, with a polite
delay. Locale hubs can differ in anti-bot behaviour; do not infer site behaviour
from an untested URL.

`robots.txt` for `User-agent: *` currently allows `/` with
`Content-Signal: search=yes,ai-train=no,use=reference`. This example is for
bounded human/operator reference analysis, not model training corpora.

## Command

```bash
python examples/blue_eyes_white_dragon_analysis.py \
  --url 'https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions' \
  --url 'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare' \
  --url 'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Blue-Eyes-White-Destiny/Blue-Eyes-White-Dragon-V1-Common' \
  --delay-seconds 2.5 \
  --browser-post-load-wait-ms 9000
```

Keep `--url` count bounded (`1..20`). Add delay between pages. Do not point the
script at authenticated or anti-bot challenge flows and expect a bypass.

## Offline edition / référence export

Given anonymized collector stdout (or the gitignored ledger
`version_floors` + `offers` JSON), regenerate the public CSV without a live
crawl:

```bash
python examples/blue_eyes_white_dragon_analysis.py \
  --from-json runs/blue-eyes-white-dragon/anonymized-listings.json \
  --export-references-csv docs/data/blue-eyes-white-dragon-version-floors-2026-08-04.csv \
  --source-date 2026-08-04 \
  --quiet-json
```

Pure helpers used by that path:

- `aggregate_version_floors_by_expansion()` — min / median / max From by set
- `rank_version_floor_references()` — dearest / cheapest / near-median samples
- `export_version_floor_references_csv()` — deterministic sanitized CSV

CSV columns: `expansion`, `product_label`, `version`, `rarity`, `from_eur`,
`from_cents`, `available_count`, `public_product_path`, `source_date`.
`public_product_path` values are public marketplace paths, **not** printed card codes.
Seller names, offer/article ids, cookies, and HTML are excluded.

Published snapshot for the 2026-08-04 run:
[`docs/data/blue-eyes-white-dragon-version-floors-2026-08-04.csv`](data/blue-eyes-white-dragon-version-floors-2026-08-04.csv)
(header + 175 data rows).

## What is parsed

### Versions overview → `version_floor`

Tiles exposing `Available` + `From X €` become one record per product path.
These are **floor prices for a product version**, not individual offers.

### Product / card offer tables → `offer`

`article-row` blocks become anonymized offers with:

- hashed article id (not seller name)
- EUR price
- condition, language, rarity, edition when exposed via tooltip / `aria-label`
- expansion code when present
- graded flag when a PSA/BGS/CGC/SGC grade token is present

Seller columns are stripped before attribute extraction.

## Output contract

JSON on stdout:

- `status`: `ok` / `partial` / `error`
- `count_raw` / `count_net`
- `failure_rate` over requested pages
- `version_floors[]` and `offers[]` kept separate
- `populations` with quartiles **by source URL**, by a 5-field segment key
  `condition|language|rarity|edition|(graded|raw)` (last field is `graded` or
  `raw`), plus `version_floors.by_expansion` and `reference_ranks`
- `pages[]` with fetch method, HTTP status, bytes, parsed count
- `warnings[]`

Never treat a non-empty file alone as complete coverage. Read `status`,
`failure_rate`, and per-page `parsed` first. Prefer expansion / product-path
tables over global floor quartiles.

## Challenge handling

If markup matches a hard challenge (DataDome / captcha interstitial /
`cdn-cgi/challenge-platform/h/` / access denied), collection stops. Passive
Cloudflare `challenge-platform/scripts/jsd` beacons alone are not treated as a
block when offer or version markup is present.

## Limits

- Archive fallback is not used (stale snapshots must not mix with live prices).
- Plain HTTP often returns 403; browser fallback may still render usable HTML
  while reporting a Cloudflare 403/429 status — the report must keep both facts.
- Card hub pages can mix products; prefer product URLs and segment tables.
- Do not commit `runs/` artifacts, fetched HTML, or seller-bearing dumps.
- Do not invent official printed set numbers when they are absent from the
  ledger.

## Public coverage crawl

```bash
python examples/blue_eyes_white_dragon_analysis.py \
  --coverage-crawl \
  --coverage-budget 230 \
  --delay-seconds 2.5 \
  --export-coverage-csv docs/data/blue-eyes-white-dragon-coverage-2026-08-04.csv \
  --export-coverage-manifest docs/data/blue-eyes-white-dragon-coverage-manifest-2026-08-04.json \
  --write-private-ledger runs/blue-eyes-white-dragon-coverage/ledger.json \
  --quiet-json
```

Walks EN/FR Versions, Search `site=N`, then product-detail identity fields only
when HTML exposes them. Stops on empty/repeated search pages, announced totals,
budget exhaustion, or three consecutive hard challenges. Never logs in, solves
CAPTCHA, or uses private APIs. Published outputs stay sanitized under
`docs/data/`; raw ledgers stay gitignored under `runs/`.

## Deep enrichment (Search resume + public product metrics)

```bash
python examples/blue_eyes_white_dragon_analysis.py \
  --deep-enrichment \
  --deep-budget 40 \
  --delay-seconds 8 \
  --deep-search-start-site 8 \
  --coverage-corpus-csv docs/data/blue-eyes-white-dragon-coverage-2026-08-04.csv \
  --deep-checkpoint runs/blue-eyes-white-dragon-deep/ledger.json \
  --export-deep-csv docs/data/blue-eyes-white-dragon-deep-enrichment-2026-08-04.csv \
  --export-deep-manifest docs/data/blue-eyes-white-dragon-deep-enrichment-manifest-2026-08-04.json \
  --quiet-json
```

Seeds a deterministic queue of the exact **177** coverage paths (refuses
overcount and White-Phantom-Beast paths), resumes Search at `site=8`, then
attempts product-detail public metrics (`From`, available count, price trend,
1/7/30-day averages, explicit collector codes only). Atomic checkpoint after
each attempt; statuses `pending`/`ok`/`challenge`/`error`; `ok` rows are never
retried. Delay ≥ 8 s + jitter; stop after 2 consecutive hard challenges; if the
first access is challenged, one bounded cooldown then a second try, then stop.
No parallelism, login, CAPTCHA solve, proxy, or private API.

The 2026-08-04 deep window recorded **0** live product-detail successes and
**2** Search challenge navigations. CSV `from_cents` / `available_count` values
on still-pending rows are **baseline coverage seeds**, not newly extracted deep
metrics.

## Official catalog join (products_singles_3 + price_guide_3)

```bash
python examples/blue_eyes_white_dragon_analysis.py \
  --official-catalog-join \
  --html-coverage-csv docs/data/blue-eyes-white-dragon-coverage-2026-08-04.csv \
  --export-official-csv docs/data/blue-eyes-white-dragon-official-join-2026-08-04.csv \
  --export-official-manifest docs/data/blue-eyes-white-dragon-official-join-manifest-2026-08-04.json \
  --source-date 2026-08-04 \
  --quiet-json
```

Performs exactly one HTTPS GET to each canonical official URL (host listed in
[Source et provenance](#source-et-provenance)), validates schema/`createdAt`/unique `idProduct`,
filters by strict name equality `Blue-Eyes White Dragon`, joins the price guide
by `idProduct`, and publishes only the derived CSV + manifest. Raw catalogs are
never committed (optional offline paths under gitignored `runs/`).

Observed 2026-08-04: **86 255** singles → **177** exact / **15** contains-excluded;
**177/177** guide joins; **102** expansions; metacard **102062**. Decimal price
fields stay as provided. The manifest keeps the official `idProduct` corpus
separate from the HTML URL corpus when no verifiable mapping exists.

## Provider-string audit

Public presentation must stay Dragon Blanc / Blue-Eyes first. Cardmarket
mentions that remain after neutralization are intentional and fall into one of
the buckets below (counts are case-insensitive substring matches on the
restored lot, excluding this audit section’s explanatory prose).

| Bucket | Where | Why kept |
| --- | --- | --- |
| Bounded provenance prose | This file § Source et provenance; report § Source et provenance | Name the real provider once, honestly, without branding the whole dossier |
| Official / public URLs | Method & report provenance tables; reproduction commands; CSV `public_product_path` cells; coverage/official manifests | Reproducibility requires the literal public endpoints |
| Indispensable constants / helpers | `OFFICIAL_*_URL`, `OFFICIAL_CATALOG_HOST`, `VERSIONS_*`, `SEARCH_EN_URL`, `CARD_HUB_EN_URL`, `canonical_product_url(..., origin=...)`, `build_search_page_url` | Code must hit the real host; renaming the constant values would break fetches |
| Negative test guards | `tests/test_blue_eyes_white_dragon_analysis.py` asserts that over-broad “publicly observable Cardmarket …” wording stays out of published manifests | Regression guard for scope honesty, not product branding |

Not allowed outside those buckets: provider name in report title, sommaire framing,
table headers, SVG title/desc/visible captions, manifest `title` fields, CLI
`description=` / public help that names the brand as the product, or README
section title / lead paragraph.
