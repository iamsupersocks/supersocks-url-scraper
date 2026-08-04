# Generic Cardmarket Blue-Eyes example

This example shows how to use the existing `supersocks-url-scraper` HTTP → SEO →
browser fetch primitives to collect a public, anonymized snapshot of
Cardmarket prices for **Blue-Eyes White Dragon** / **Dragon Blanc aux Yeux
Bleus**.

It is intentionally narrow:

- no seller names or user profile URLs in the output
- no raw HTML, cookies, tokens, or browser profiles written to the repository
- no CAPTCHA solving, login, consent acceptance, or access-challenge bypass
- version floor prices and live offer rows stay in separate populations
- edition / référence analysis prefers set label + public product path over
  global quartiles; official printed codes (`LOB-001`, …) are never invented

For a didactic walkthrough of a dated live snapshot (edition-first tables,
pipeline diagram, 403/429 caveats, segment tables, and reading guide), see
[`CARDMARKET_BLUE_EYES_ANALYSIS_REPORT.md`](CARDMARKET_BLUE_EYES_ANALYSIS_REPORT.md).
Do not duplicate market conclusions here — keep this file as the short operator
howto.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[full,browser,test]'
```

## Canonical public URLs

Use only public Cardmarket pages you are allowed to query:

| Role | URL |
| --- | --- |
| Card hub | `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon` |
| All versions | `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions` |
| FR canonical hub | `https://www.cardmarket.com/fr/YuGiOh/Cards/BlueEyes-White-Dragon` |
| Example product | `https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare` |

Prefer the English Versions page plus explicit product pages, with a polite
delay. Locale hubs can differ in anti-bot behaviour; do not infer site behaviour
from an untested URL.

`robots.txt` for `User-agent: *` currently allows `/` with
`Content-Signal: search=yes,ai-train=no,use=reference`. This example is for
bounded human/operator reference analysis, not model training corpora.

## Command

```bash
python examples/generic_cardmarket_blue_eyes.py \
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
python examples/generic_cardmarket_blue_eyes.py \
  --from-json runs/cardmarket-blue-eyes/anonymized-listings.json \
  --export-references-csv docs/data/cardmarket-blue-eyes-version-floors-2026-08-04.csv \
  --source-date 2026-08-04 \
  --quiet-json
```

Pure helpers used by that path:

- `aggregate_version_floors_by_expansion()` — min / median / max From by set
- `rank_version_floor_references()` — dearest / cheapest / near-median samples
- `export_version_floor_references_csv()` — deterministic sanitized CSV

CSV columns: `expansion`, `product_label`, `version`, `rarity`, `from_eur`,
`from_cents`, `available_count`, `public_product_path`, `source_date`.
`public_product_path` values are Cardmarket paths, **not** printed card codes.
Seller names, offer/article ids, cookies, and HTML are excluded.

Published snapshot for the 2026-08-04 run:
[`docs/data/cardmarket-blue-eyes-version-floors-2026-08-04.csv`](data/cardmarket-blue-eyes-version-floors-2026-08-04.csv)
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
python examples/generic_cardmarket_blue_eyes.py \
  --coverage-crawl \
  --coverage-budget 230 \
  --delay-seconds 2.5 \
  --export-coverage-csv docs/data/cardmarket-blue-eyes-coverage-2026-08-04.csv \
  --export-coverage-manifest docs/data/cardmarket-blue-eyes-coverage-manifest-2026-08-04.json \
  --write-private-ledger runs/cardmarket-blue-eyes-coverage/ledger.json \
  --quiet-json
```

Walks EN/FR Versions, Search `site=N`, then product-detail identity fields only
when HTML exposes them. Stops on empty/repeated search pages, announced totals,
budget exhaustion, or three consecutive hard challenges. Never logs in, solves
CAPTCHA, or uses private APIs. Published outputs stay sanitized under
`docs/data/`; raw ledgers stay gitignored under `runs/`.
