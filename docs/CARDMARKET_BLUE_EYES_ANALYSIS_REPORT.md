# Cardmarket Blue-Eyes market analysis report

Public, bounded snapshot of Cardmarket prices for **Blue-Eyes White Dragon** /
**Dragon Blanc aux Yeux Bleus**, collected with
[`examples/generic_cardmarket_blue_eyes.py`](../examples/generic_cardmarket_blue_eyes.py)
and the same evidence posture as the RTX dossier: reproducible commands,
separated populations, no seller identities, no raw HTML in git.

Workflow docs:
[`GENERIC_CARDMARKET_BLUE_EYES_EXAMPLE.md`](GENERIC_CARDMARKET_BLUE_EYES_EXAMPLE.md).

## Run metadata

| Field | Value |
| --- | --- |
| Local time | **2026-08-04 20:44:41 CEST** (Europe/Paris) |
| UTC start | `2026-08-04T18:44:41.356387+00:00` |
| UTC end | `2026-08-04T18:45:39.287174+00:00` |
| Transport | CloakBrowser via `supersocks-url-scraper` (`x-fetch-method=cloak`) |
| Delay | 2.5 s between URLs |
| Post-load wait | 9 s |
| Login / CAPTCHA solve / consent accept | **not used** |
| Archive fallback | **disabled** |
| Private evidence dir | gitignored `runs/cardmarket-blue-eyes/` |

## Source URLs

1. `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions`
2. `https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon`
3. `https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare`
4. `https://www.cardmarket.com/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon-25th-Anniversary-Edition/Blue-Eyes-White-Dragon`
5. `https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Blue-Eyes-White-Destiny/Blue-Eyes-White-Dragon-V1-Common`

An exploratory plain HTTP/SEO probe used the translated non-canonical FR slug
`/fr/YuGiOh/Cards/Dragon-Blanc-aux-Yeux-Bleus` and got a hard 403 without usable
markup. That probe is **not** evidence about the canonical FR hub
`https://www.cardmarket.com/fr/YuGiOh/Cards/BlueEyes-White-Dragon`, which was
**not** fetched in this run and is excluded from the conclusions below.

## Volume and failure rate

| Metric | Value |
| --- | ---: |
| Pages requested | 5 |
| Pages with parseable public markup | 5 |
| Pages `error` / hard `blocked` | 0 |
| Page failure rate | **0%** (0/5) |
| Version floor records raw → net | 175 → **175** |
| Offer rows raw → net | 200 → **200** |
| Graded offers detected | 0 |

Honest transport caveat: CloakBrowser returned usable HTML for every URL above,
but the embedded HTTP status on four product/card responses was **403** and the
Versions response was **429**. Plain `fetch_url` without browser was consistently
403. No DataDome/captcha interstitial stopped the run under the strict challenge
detector (passive Cloudflare JSD beacons alone were ignored when offer markup
was present). Rate limiting and bot scoring remain a real bias: this is a
short polite sample, not a full inventory crawl.

## Population A — version floors (Versions page)

These are product-level **From** prices, one per version tile. They are **not**
offer rows and must not be averaged with live offers.

| Stat | EUR |
| --- | ---: |
| n | 175 |
| min | 0.02 |
| Q1 | 0.99 |
| median | **5.00** |
| Q3 | 19.995 |
| max | 1999.99 |
| Sum of displayed “Available” counts | 78,357 |

### Median From-price by parsed rarity

Unknown rarity (82 tiles whose slug/tooltip did not map cleanly) is excluded
from the chart so Common is not silently mixed with Starlight.

![Median version floor by rarity](assets/cardmarket-blue-eyes-breakdown.svg)

| Rarity | n | Median From (€) |
| --- | ---: | ---: |
| Starlight Rare | 3 | 69.99 |
| Ultimate Rare | 2 | 49.725 |
| Quarter Century Secret Rare | 13 | 40.00 |
| Rare | 11 | 12.00 |
| Secret Rare | 16 | 7.875 |
| Super Rare | 2 | 6.75 |
| Ultra Rare | 32 | 4.225 |
| Platinum Secret Rare | 3 | 2.00 |
| Common | 11 | 0.20 |
| unknown (excluded from SVG) | 82 | 3.87 |

## Population B — live offers (kept per source URL)

Each product page exposed 50 `article-row` offers (first result page only).
Seller names were redacted. Stats below are **not** merged across products.

### Rarity Collection 5 — V1 Ultra Rare

URL: product page (3) above.

| Segment (`condition\|language\|rarity\|edition\|(graded\|raw)`) | n | min | Q1 | median | Q3 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Near Mint \| English \| unknown\* \| First Edition \| raw | 20 | 1.00 | 1.82 | **1.99** | 2.00 | 2.45 |
| Near Mint \| English \| unknown\* \| unknown \| raw | 9 | 1.40 | 1.50 | 2.00 | 2.00 | 2.41 |
| Near Mint \| French \| unknown\* \| First Edition \| raw | 8 | 1.50 | 2.00 | 2.00 | 2.10 | 2.45 |

\*Product page rarity icon sometimes omitted in the row tooltip even though the
URL is the Ultra Rare product; do not relabel without an explicit attribute.

Page-level median (mixed segments on that page): **2.00 €** (n=50).

### Legend of Blue-Eyes White Dragon — 25th Anniversary Edition

| Segment | n | min | Q1 | median | Q3 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Near Mint \| German \| unknown \| unknown \| raw | 21 | 22.00 | 27.95 | **35.50** | 41.64 | 50.00 |
| Near Mint \| English \| unknown \| unknown \| raw | 15 | 30.00 | 47.08 | **48.75** | 49.97 | 50.00 |
| Excellent \| English \| unknown \| unknown \| raw | 5 | 36.71 | 44.99 | 44.99 | 46.78 | 47.99 |

Page-level median: **40.82 €** (n=50). This population is incompatible with
Structure Deck commons or modern Ultra Rare reprints.

### Structure Deck: Blue-Eyes White Destiny — V1 Common

| Segment | n | min | median | max |
| --- | ---: | ---: | ---: | ---: |
| Near Mint \| French \| unknown \| First Edition \| raw | 17 | 0.02 | **0.02** | 0.02 |
| Near Mint \| English \| unknown \| First Edition \| raw | 10 | 0.02 | **0.02** | 0.02 |
| Near Mint \| German \| unknown \| First Edition \| raw | 8 | 0.02 | **0.02** | 0.02 |

Page-level median: **0.02 €** (n=50).

### Card hub offers page

URL (2) returned 50 offers. Dominant segment:

| Segment | n | median |
| --- | ---: | ---: |
| Near Mint \| English \| Ultra Rare \| First Edition \| raw | 19 | **1.99 €** |

Useful as a cross-check, but the hub can reshuffle mixed products; prefer
explicit product URLs for claims.

## What was verified offline

`tests/test_generic_cardmarket_blue_eyes.py` covers:

- EUR parsing / normalization
- offer parsing with seller redaction
- version-floor parsing and Blue-Eyes filtering
- deduplicated population summaries that refuse silent cross-product merges
- quartiles
- challenge detection (passive CF JSD vs hard interstitial)
- stop-on-challenge collection behaviour
- URL count bounds

Fixtures are synthetic HTML only.

## Biases and limits

1. **First page only** per URL (≤50 offers). Deeper pagination was not crawled.
2. **Cloudflare 403/429** on browser responses — content was present, but the
   edge is hostile; repeats may fail.
3. **Attribute gaps** — language/rarity/edition come from tooltips; missing
   tooltips stay `unknown` instead of being guessed from the URL alone for
   offers (version floors may infer rarity from the product slug).
4. **No graded cards** in this sample.
5. **Sorting / filters** on Cardmarket affect which 50 offers appear.
6. **Not the site’s full inventory** — private, deleted, or unpriced listings
   are out of scope.
7. **Legal/ToS** — operator must stay within Cardmarket terms, robots/rate
   expectations, and local law. This repository ships a scraper pattern, not a
   bypass service.

## Reproduction

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[full,browser,test]'
python -m pytest tests/test_generic_cardmarket_blue_eyes.py -q
python examples/generic_cardmarket_blue_eyes.py \
  --url 'https://www.cardmarket.com/en/YuGiOh/Cards/Blue-Eyes-White-Dragon/Versions' \
  --url 'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Rarity-Collection-5/Blue-Eyes-White-Dragon-V1-Ultra-Rare' \
  --url 'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Legend-of-Blue-Eyes-White-Dragon-25th-Anniversary-Edition/Blue-Eyes-White-Dragon' \
  --url 'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Blue-Eyes-White-Destiny/Blue-Eyes-White-Dragon-V1-Common' \
  --delay-seconds 2.5 \
  --browser-post-load-wait-ms 9000
```

Live totals will drift; compare structure and segment discipline, not exact
euro values. Keep any local run traces under gitignored `runs/`.
