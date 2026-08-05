# Blue-Eyes White Dragon official catalog join manifest

- Generated: `2026-08-04T21:57:37.557852+00:00` (UTC)
- Source date: **2026-08-04**
- Fetched live: **True**

## Official sources

- Products URL: `https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_3.json`
- Price guide URL: `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_3.json`
- Products `createdAt` / version / SHA-256: **2026-08-04T11:20:15+0200** / **1** / `c854a01148559e596fb89eb0df9c6538174b43296123e672f3d1207bd88f58a9`
- Price guide `createdAt` / version / SHA-256: **2026-08-04T02:47:19+0200** / **1** / `3ad971694838ed8cd9f02a9392ed9bb7acb125adcce6d1174f47edcb0580f524`
- Singles before filter: **86255**
- Price guide rows before filter: **88626**

## Filter and join

- Exact name equality: `Blue-Eyes White Dragon`
- Exact / contains / excluded: **177** / **192** / **15**
- Join matched by `idProduct`: **177** (missing: **0**)

## Corpora (no invented URL↔idProduct mapping)

- Official corpus by `idProduct`: **177** (expansions **102**, metacard **[102062]**)
- HTML corpus by URL/path: **177**
- Mapping verified: **False** — Official productList/priceGuide payloads do not expose public product URLs. HTML coverage corpus is keyed by URL/path; official corpus is keyed by idProduct. Do not invent rank-to-rank or lexical URL↔idProduct mappings.

## Market statistics (Decimal, no float)

- Rows: **177**
- Median rule: Even-n Decimal median: sort ascending, take the arithmetic mean of the two central values as (a + b) / Decimal('2') with Decimal division only (no float). Odd-n median: the single middle value. Serialize with format_official_decimal (format(value, 'f')), never float().
- Serialization: Decimal strings via format_official_decimal; no float

| Metric | present | absent | min | median | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `avg` | 175 | 2 | 0.11 | 13.61 | 1249.98 |
| `low` | 175 | 2 | 0.02 | 5 | 1999.99 |
| `trend` | 176 | 1 | 0.02 | 14.07 | 994.38 |
| `avg1` | 176 | 1 | 0.03 | 11.99 | 1250 |
| `avg7` | 176 | 1 | 0.15 | 14.105 | 1081.99 |
| `avg30` | 176 | 1 | 0.11 | 14.685 | 1314.96 |
| `avg_foil` | 0 | 177 | None | None | None |
| `low_foil` | 0 | 177 | None | None | None |
| `trend_foil` | 176 | 1 | 0 | 0 | 421.64 |
| `avg1_foil` | 49 | 128 | 0.02 | 2.43 | 460 |
| `avg7_foil` | 49 | 128 | 0.06 | 2.72 | 289.28 |
| `avg30_foil` | 49 | 128 | 0.09 | 2.92 | 205.81 |

### Reading notes

- `low`: current offer floor (plancher courant) at price-guide snapshot time
- `trend`: Official guide trend indicator — distinct from avg / avg1 / avg7 / avg30
- `avg`: guide average — not interchangeable with temporal avg1/avg7/avg30 windows
- `avg1` / `avg7` / `avg30`: rolling temporal averages (1 / 7 / 30 day windows)
- Edition attribution: Official productList/priceGuide rows expose idExpansion but not expansion name, public URL, rarity, or set code. Official prices cannot be attributed to HTML edition slugs without a verified URL↔idProduct mapping.

### Top 5 by trend (idProduct / idExpansion / public metrics only)

| idProduct | idExpansion | trend | avg | low | avg1 | avg7 | avg30 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 102244 | 1039 | 994.38 | 1249.98 | 290 | 1250 | 1081.99 | 1314.96 |
| 735806 | 4919 | 878.39 | 1016.33 | 1250 | 1000 | 1064.14 | 694.33 |
| 578088 | 1064 | 550 | 550 | 50 | 550 | 375 | 375 |
| 255541 | 1379 | 549.25 | 499.99 | 1999.99 | 499.99 | 393 | 251.95 |
| 393872 | 2622 | 504.14 | 600 | 999 | 600 | 532 | 759.22 |

## Field presence

- `avg`: **175**
- `avg1`: **176**
- `avg1_foil`: **49**
- `avg30`: **176**
- `avg30_foil`: **49**
- `avg7`: **176**
- `avg7_foil`: **49**
- `category`: **177**
- `dateAdded`: **177**
- `idExpansion`: **177**
- `idMetacard`: **177**
- `idProduct`: **177**
- `low`: **175**
- `name`: **177**
- `trend`: **176**
- `trend_foil`: **176**

## Field absence

- `avg`: **2**
- `avg1`: **1**
- `avg1_foil`: **128**
- `avg30`: **1**
- `avg30_foil`: **128**
- `avg7`: **1**
- `avg7_foil`: **128**
- `avg_foil`: **177**
- `low`: **2**
- `low_foil`: **177**
- `trend`: **1**
- `trend_foil`: **1**
