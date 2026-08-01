# RTX price analysis report

This is a sanitized, aggregate example of what the generic workflow in
[`GENERIC_RTX_PRICE_EXAMPLE.md`](GENERIC_RTX_PRICE_EXAMPLE.md) can produce.
The public repository deliberately excludes the marketplace name, live target
URL, listing identifiers and titles, raw HTML, cookies, tokens, and browser
profiles.

## Reconciled snapshot

- Run date: 2026-08-01
- Result: `reconciled public snapshot`
- Scope: nationwide public graphics-card category search, with no geographic
  filter
- Price coverage: six non-overlapping shards (`0–312`, `313–625`, `626–1,250`,
  `1,251–2,500`, `2,501–5,000`, and `5,001–max` EUR)
- Pass 1: 129/129 pages valid, 3,396 unique priced RTX records
- Pass 2: 129/129 pages valid, 3,396 unique priced RTX records
- Pass 1 versus pass 2: 3,350 shared identifiers, 46 only in each pass
- Pass 3: 110/110 pages valid on the three moving shards
- Canonical snapshot: 3,404 unique priced RTX records, using pass 2 for stable
  shards and the later pass 3 for moving shards
- One transient browser network failure was retained in the audit ledger and
  retried successfully; no access challenge was detected
- 3,321 records remained after excluding titles flagged as complete PC, laptop,
  bundle/lot, or multi-model; 3,096 were classified to one exact RTX model
- GPU-only price quartiles: Q1 200 €, median 290 €, Q3 450 €

This is the complete reconciled snapshot of the public, priced, category-scoped
results exposed during the bounded run. It is not the marketplace's internal
inventory: unpriced, deleted while collecting, private, or miscategorized ads
cannot be guaranteed by a public search collector.

## What actually ran

This reconciliation run used a shared, local CloakBrowser context directly.
It did not claim that the generic HTTP → SEO → browser fallback was exercised:
the browser route was selected intentionally so every shard and pass used the
same transport and produced comparable saved pages.

1. Synthetic tests first covered a complete shard, probe failure, missing page,
   explicit zero-result shard, moving totals/identifiers, checkpoint resume,
   and the high-price queue.
2. A nationwide category query was split recursively by displayed EUR price
   until every shard fit within the public pagination limit.
3. Every expected page was rendered with an explicit two-second inter-page
   delay. Embedded JSON was extracted in memory, EUR prices were normalized,
   and records were deduplicated by listing identifier.
4. Raw pages, hashes, ledgers, checkpoints, IDs, and titles stayed under the
   gitignored `runs/` directory. Existing raw pages were never overwritten.
5. Two complete passes were compared by identifier and by shard. Because the
   live inventory changed, only the three divergent shards were read a third
   time; that last complete reading became canonical for those shards.
6. A separate recomputation rebuilt the canonical ID set from raw HTML, matched
   all 3,404 identifiers exactly, excluded contamination flags, recalculated
   quartiles and medians, generated the SVG twice, and verified byte-stable
   output plus table/SVG agreement.

No login, CAPTCHA solving, consent acceptance, archive fallback, or access-
challenge bypass was used. The collector is configured to stop rather than
work around an access challenge.

## Classification exceptions

Flags can overlap.

| Bucket | Count |
| --- | ---: |
| Any excluded contamination flag | 83 |
| Complete-PC token | 44 |
| Laptop token | 1 |
| Bundle/lot token | 31 |
| Multi-model title | 22 |
| Ambiguous RTX token | 160 |
| No exact model match | 233 |
| Exact model match but contaminated | 75 |

## Model-level view (GPU-only)

The graph shows the median displayed price for each exact RTX model after
excluding titles flagged as complete PCs, laptops, bundles/lots, or multiple
models. `n` is the number of retained records. A zero row means no retained
hit for that exact model during the snapshot.

![Median price by RTX model](assets/rtx-model-breakdown.svg)

| Generation | Model | Records (GPU-only) | Median |
| --- | --- | ---: | ---: |
| 20xx | RTX 2000 | 1 | 10 € |
| 20xx | RTX 2050 | 0 | — |
| 20xx | RTX 2060 | 303 | 150 € |
| 20xx | RTX 2060 SUPER | 66 | 160 € |
| 20xx | RTX 2070 | 96 | 180 € |
| 20xx | RTX 2070 SUPER | 91 | 195 € |
| 20xx | RTX 2080 | 47 | 220 € |
| 20xx | RTX 2080 SUPER | 46 | 220 € |
| 20xx | RTX 2080 Ti | 31 | 290 € |
| 30xx | RTX 3050 | 119 | 180 € |
| 30xx | RTX 3060 | 284 | 250 € |
| 30xx | RTX 3060 Ti | 269 | 250 € |
| 30xx | RTX 3070 | 397 | 270 € |
| 30xx | RTX 3070 Ti | 155 | 300 € |
| 30xx | RTX 3080 | 282 | 390 € |
| 30xx | RTX 3080 Ti | 89 | 500 € |
| 30xx | RTX 3090 | 105 | 950 € |
| 40xx | RTX 4000 | 18 | 230 € |
| 40xx | RTX 4050 | 0 | — |
| 40xx | RTX 4060 | 106 | 270 € |
| 40xx | RTX 4060 Ti | 88 | 330 € |
| 40xx | RTX 4070 | 46 | 470 € |
| 40xx | RTX 4070 SUPER | 28 | 550 € |
| 40xx | RTX 4070 Ti | 36 | 600 € |
| 40xx | RTX 4070 Ti SUPER | 23 | 750 € |
| 40xx | RTX 4080 | 62 | 850 € |
| 40xx | RTX 4090 | 51 | 1,800 € |
| 50xx | RTX 5050 | 9 | 240 € |
| 50xx | RTX 5060 | 33 | 320 € |
| 50xx | RTX 5060 Ti | 54 | 400 € |
| 50xx | RTX 5070 | 46 | 595 € |
| 50xx | RTX 5070 Ti | 47 | 900 € |
| 50xx | RTX 5080 | 38 | 1,250 € |
| 50xx | RTX 5090 | 30 | 3,500 € |

## Reading the result

Exact-model matching prefers the most specific token (`Ti SUPER`, then
`SUPER`, then `Ti`, then the base model). The classifier is intentionally
conservative, but title heuristics can still over-flag or under-flag. Prices
such as 1 € and other outliers remain in the dataset; medians reduce their
effect but do not turn a listing title into a verified standalone GPU.

All displayed medians are rounded half-up to whole euros. Quartiles are
calculated from raw EUR values. The public graph and table contain aggregates
only; the reproducible private evidence remains gitignored.
