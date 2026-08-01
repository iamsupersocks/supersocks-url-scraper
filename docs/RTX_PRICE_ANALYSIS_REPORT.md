# RTX price analysis report

This is an aggregate snapshot produced with the generic example in
[`GENERIC_RTX_PRICE_EXAMPLE.md`](GENERIC_RTX_PRICE_EXAMPLE.md). The public
repository deliberately keeps the live source name, target URL, listing
identifiers, titles, cookies, tokens, and raw HTML out of Git.

## Snapshot

- Run date: 2026-08-01
- Result: `partial`
- 217 unique RTX-title records with an EUR price
- 9 pages returned usable embedded data
- Pagination stopped at page 10 after an access challenge was detected
- Exact model parsing classified 203 records; 14 remained other or ambiguous
- Observed prices: 1 € to 3 900 €
- Quartiles: Q1 350 €, median 600 €, Q3 850 €

This is not an exhaustive inventory. It is the result of one bounded run, and
the collector intentionally stops at an access challenge instead of attempting
to bypass it.

## Model-level view

The graph shows the median displayed price for every exact RTX model detected at
least once. `n` is the number of records for that model. RTX 3090 is shown as a
control row because no title in this snapshot exposed that exact model.

![Median price by RTX model](assets/rtx-model-breakdown.svg)

| Generation | Model | Records | Median |
| --- | --- | ---: | ---: |
| 20xx | RTX 2000 | 1 | 1 250 € |
| 20xx | RTX 2050 | 1 | 550 € |
| 20xx | RTX 2060 | 15 | 250 € |
| 20xx | RTX 2060 SUPER | 3 | 189 € |
| 20xx | RTX 2070 | 5 | 500 € |
| 20xx | RTX 2070 SUPER | 4 | 215 € |
| 20xx | RTX 2080 | 2 | 325 € |
| 20xx | RTX 2080 SUPER | 2 | 525 € |
| 20xx | RTX 2080 Ti | 3 | 1 040 € |
| 30xx | RTX 3050 | 13 | 380 € |
| 30xx | RTX 3060 | 28 | 500 € |
| 30xx | RTX 3060 Ti | 16 | 575 € |
| 30xx | RTX 3070 | 13 | 650 € |
| 30xx | RTX 3070 Ti | 3 | 350 € |
| 30xx | RTX 3080 | 8 | 525 € |
| 30xx | RTX 3080 Ti | 2 | 765 € |
| 30xx | RTX 3090 | 0 | — |
| 40xx | RTX 4000 | 1 | 700 € |
| 40xx | RTX 4050 | 3 | 700 € |
| 40xx | RTX 4060 | 18 | 700 € |
| 40xx | RTX 4060 Ti | 5 | 800 € |
| 40xx | RTX 4070 | 11 | 950 € |
| 40xx | RTX 4070 SUPER | 2 | 1 200 € |
| 40xx | RTX 4070 Ti | 3 | 700 € |
| 40xx | RTX 4070 Ti SUPER | 1 | 660 € |
| 40xx | RTX 4080 | 1 | 2 400 € |
| 40xx | RTX 4090 | 2 | 3 195 € |
| 50xx | RTX 5050 | 3 | 500 € |
| 50xx | RTX 5060 | 13 | 850 € |
| 50xx | RTX 5060 Ti | 4 | 555 € |
| 50xx | RTX 5070 | 7 | 1 200 € |
| 50xx | RTX 5070 Ti | 3 | 2 100 € |
| 50xx | RTX 5080 | 6 | 2 100 € |
| 50xx | RTX 5090 | 1 | 3 500 € |

## Reading the result

Model names are extracted from titles containing an RTX token. The example
does not claim that every record is a standalone graphics card: laptops, PCs,
bundles, and imprecise titles can remain in the aggregate. A production report
would add a category-specific classifier and a second review pass.

The runtime uses the existing HTTP → SEO → browser path, converts configured
EUR values, deduplicates by listing identifier, and returns `ok`, `partial`, or
`error`. Consent handling only accepts an explicit refusal or continuation
without accepting; access challenges stop pagination.
