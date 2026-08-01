# RTX price analysis report

This is an aggregate snapshot produced with the generic example in
[`GENERIC_RTX_PRICE_EXAMPLE.md`](GENERIC_RTX_PRICE_EXAMPLE.md). The public
repository deliberately keeps the live source name, target URL, listing
identifiers, titles, cookies, tokens, and raw HTML out of Git.

Metrics below were **independently recomputed** from the private pass-1
artifact (unique priced RTX listings), not copied from collector summary
tables. Model medians and overall quartiles are **GPU-only**: titles flagged as
laptop, complete PC, bundle/lot, or multi-model are excluded so they cannot
silently contaminate card-level statistics.

## Snapshot

- Run date: 2026-08-01
- Result: `partial`
- 1 769 unique RTX-title records with an EUR price in the salvaged pass-1 set
- 1 016 of those carry at least one contamination flag (PC 997, laptop 193,
  bundle 22, multi-model 6; flags can overlap)
- 704 GPU-only records classified to an exact model; 137 remained other or
  ambiguous (no exact model token match)
- GPU-only observed prices: 1 € to 780 €
- GPU-only quartiles: Q1 230 €, median 300 €, Q3 450 €
- Pagination / shard coverage from the live collection was incomplete
  (operator stop before finishing exposed shards and before pass-2); this is
  not an exhaustive inventory

This is not an exhaustive inventory. It is the result of one bounded run, and
the collector intentionally stops at an access challenge instead of attempting
to bypass it.

## Classification exceptions (redacted counts)

| Bucket | Count |
| --- | ---: |
| Contaminated PC titles | 997 |
| Contaminated laptop titles | 193 |
| Contaminated bundle/lot titles | 22 |
| Multi-model titles | 6 |
| Ambiguous model token (RTX without digits) | 91 |
| No exact model match | 137 |
| Exact model match but contaminated (excluded from GPU-only medians) | 928 |

Private recomputation evidence (gitignored):  
`runs/rtx_live_audit_20260801T182240Z/recompute/`.

## Model-level view (GPU-only)

The graph shows the median displayed price for every exact RTX model among
GPU-only records. `n` is the number of GPU-only records for that model.
RTX 3090 is shown as a control row because no GPU-only title in this snapshot
exposed that exact model. Zero rows for other models mean no GPU-only hit after
contamination exclusion.

![Median price by RTX model](assets/rtx-model-breakdown.svg)

| Generation | Model | Records | Median |
| --- | --- | ---: | ---: |
| 20xx | RTX 2000 | 0 | — |
| 20xx | RTX 2050 | 0 | — |
| 20xx | RTX 2060 | 110 | 200 € |
| 20xx | RTX 2060 SUPER | 17 | 189 € |
| 20xx | RTX 2070 | 30 | 300 € |
| 20xx | RTX 2070 SUPER | 19 | 230 € |
| 20xx | RTX 2080 | 8 | 220 € |
| 20xx | RTX 2080 SUPER | 9 | 300 € |
| 20xx | RTX 2080 Ti | 4 | 283 € |
| 30xx | RTX 3050 | 71 | 330 € |
| 30xx | RTX 3060 | 104 | 350 € |
| 30xx | RTX 3060 Ti | 62 | 270 € |
| 30xx | RTX 3070 | 79 | 340 € |
| 30xx | RTX 3070 Ti | 32 | 363 € |
| 30xx | RTX 3080 | 59 | 420 € |
| 30xx | RTX 3080 Ti | 4 | 500 € |
| 30xx | RTX 3090 | 0 | — |
| 40xx | RTX 4000 | 1 | 300 € |
| 40xx | RTX 4050 | 3 | 600 € |
| 40xx | RTX 4060 | 42 | 295 € |
| 40xx | RTX 4060 Ti | 18 | 350 € |
| 40xx | RTX 4070 | 2 | 500 € |
| 40xx | RTX 4070 SUPER | 2 | 650 € |
| 40xx | RTX 4070 Ti | 2 | 670 € |
| 40xx | RTX 4070 Ti SUPER | 1 | 660 € |
| 40xx | RTX 4080 | 0 | — |
| 40xx | RTX 4090 | 1 | 1 € |
| 50xx | RTX 5050 | 1 | 260 € |
| 50xx | RTX 5060 | 10 | 340 € |
| 50xx | RTX 5060 Ti | 7 | 420 € |
| 50xx | RTX 5070 | 6 | 600 € |
| 50xx | RTX 5070 Ti | 0 | — |
| 50xx | RTX 5080 | 0 | — |
| 50xx | RTX 5090 | 0 | — |

## Reading the result

Model names are extracted from titles containing an RTX token, preferring the
most specific match (for example Ti SUPER before SUPER before Ti before the
base model). GPU-only statistics drop laptop, complete-PC, bundle/lot, and
multi-model titles. Displayed medians are rounded half-up to whole euros;
underlying quartiles use the raw EUR majors.

Caveats without overstating coverage:

- Coverage is partial: incomplete public shards and no second-pass
  reconciliation.
- Unpriced ads are out of scope.
- Title heuristics can both over-flag and under-flag (for example a desktop
  listing that never says “PC”).
- Singleton extremes such as RTX 4090 at 1 € remain visible; they are not
  removed by contamination rules and should not be read as a reliable market
  level.

The runtime uses the existing HTTP → SEO → browser path, converts configured
EUR values, deduplicates by listing identifier, and returns `ok`, `partial`, or
`error`. Consent handling only accepts an explicit refusal or continuation
without accepting; access challenges stop pagination.
