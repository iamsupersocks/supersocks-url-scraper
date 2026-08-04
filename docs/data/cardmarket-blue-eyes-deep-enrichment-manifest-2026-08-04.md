# Cardmarket Blue-Eyes public deep-enrichment manifest

- Generated: `2026-08-04T21:39:49.000771+00:00` (UTC)
- Stop reason: **first_access_hard_challenge_after_cooldown**
- Corpus total: **177**
- Attempted / succeeded / product-queue challenges / pending / errors: **0** / **0** / **0** / **177** / **0**
- Navigations used: **2** / **40**
- Search last site: **8**

## Scope qualification

- Versions complete: **True** (177/177 prior coverage).
- Details enriched partially: **False** (false when live successes are 0).
- Live product-detail progress: **0/177**.
- Product-queue challenges (not Search): **0**.
- Live Search challenge navigations (separate from product queue): **2**.
- Offers non-exhaustive: **True**.

## Baseline reuse (not live deep extractions)

- `from_cents` / `available_count` on seeded pending rows: **175** / **175** — from_cents / available_count values on pending rows are reused from the prior coverage CSV baseline seed; they were not newly extracted by live product-detail fetches in this deep-enrichment window (live detail successes=0/177).

## Field presence

- `available_count`: **175**
- `from_cents`: **175**

Queue status snapshot:
- `pending`: **177**
- `ok`: **0**
- `challenge`: **0**
- `error`: **0**
- `total`: **177**
