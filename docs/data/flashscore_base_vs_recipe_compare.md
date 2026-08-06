# Base HTML scraper vs JSON recipe (deterministic, offline)

URL: `https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34` — all values are synthetic fixtures, never live.
Recipe path: `examples/recipes/flashscore_odds.v1.json` (explicit load; not builtin).

### 1. Base HTML scraper (generic text)

- title: `Alpha vs Beta — Demo League`
- extract method: `trafilatura`
- provenance: Generic HTML → article text extraction (no API recipe).
- captured_at: `None`
- fallback_signal: `False`

**summary:** Alpha FC Beta United 2026-08-06 20:00 Betclic 2.10 3.25 3.40 Winamax 2.15 3.30 3.35 Unibet 2.08 3.20 3.45

### 2. JSON recipe (normalized 1X2)

- fetch_method: `api-recipe`
- summary: Flashscore 1X2 odds for event Ab12Cd34 (not betting advice). Betclic: 2.1/3.25/3.4 Winamax: 2.15/3.3/3.35 Unibet: 2.08/3.2/3.45 Betsson: 2.12/3.28/3.38 bwin: 2.05/3.4/3.5 Captured: 2026-08-06T17:50:27+00:00.
- provenance: Pattern: observed Flashscore-related odds GraphQL GET shape (2.ds.lsapp.eu/pq_graphql, _hash=ole2). Undocumented and unstable. Example-only (not builtin/supported). Operators must respect Flashscore Terms of Use (https://www.flashscore.com/terms-of-use/).
- captured_at: `2026-08-06T17:50:27+00:00`
- fallback_signal: `False`

**structured_data:**

```json
{
  "kind": "flashscore_odds_1x2",
  "schema": "flashscore_prematch_1x2_v1",
  "match_url": "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34",
  "event_id": "Ab12Cd34",
  "market": "HOME_DRAW_AWAY",
  "scope": "FULL_TIME",
  "captured_at": "2026-08-06T17:50:27+00:00",
  "bookmakers": [
    {
      "bookmaker_id": 141,
      "bookmaker": "Betclic",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.1,
      "draw": 3.25,
      "away": 3.4,
      "opening": {
        "home": 2.05,
        "draw": 3.2,
        "away": 3.5
      }
    },
    {
      "bookmaker_id": 264,
      "bookmaker": "Winamax",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.15,
      "draw": 3.3,
      "away": 3.35,
      "opening": {
        "home": 2.1,
        "draw": 3.25,
        "away": 3.4
      }
    },
    {
      "bookmaker_id": 160,
      "bookmaker": "Unibet",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.08,
      "draw": 3.2,
      "away": 3.45,
      "opening": {
        "home": null,
        "draw": null,
        "away": null
      }
    },
    {
      "bookmaker_id": 905,
      "bookmaker": "Betsson",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.12,
      "draw": 3.28,
      "away": 3.38,
      "opening": {
        "home": 2.0,
        "draw": 3.1,
        "away": 3.6
      }
    },
    {
      "bookmaker_id": 129,
      "bookmaker": "bwin",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.05,
      "draw": 3.4,
      "away": 3.5,
      "opening": {
        "home": 2.02,
        "draw": 3.3,
        "away": 3.55
      }
    }
  ],
  "disclaimer": "Odds are a dated public snapshot for research/agent context only. This is not betting advice and must not be presented as a tip or recommendation.",
  "provenance": "Pattern: observed Flashscore-related odds GraphQL GET shape (2.ds.lsapp.eu/pq_graphql, _hash=ole2). Undocumented and unstable. Example-only (not builtin/supported). Operators must respect Flashscore Terms of Use (https://www.flashscore.com/terms-of-use/).",
  "warnings": [
    "Example-only (not builtin/supported). Flashscore Terms of Use prohibit automated requests and scraping without express consent (https://www.flashscore.com/terms-of-use/). Prefer offline fixtures; live GETs are the operator's responsibility.",
    "Observed endpoint shape (2.ds.lsapp.eu /pq_graphql _hash=ole2) is undocumented/unstable and may change without notice.",
    "Odds are a dated snapshot for research/agent context only — not betting advice.",
    "Example-only (not builtin/supported). Flashscore Terms of Use prohibit automated requests and scraping without express consent (https://www.flashscore.com/terms-of-use/). Prefer offline fixtures; live GETs are the operator's responsibility.",
    "Observed endpoint shape (2.ds.lsapp.eu /pq_graphql _hash=ole2) is undocumented/unstable and may change without notice.",
    "Odds are a dated snapshot for research/agent context only — not betting advice."
  ]
}
```

**Markdown render of the recipe payload:**

# Flashscore odds Ab12Cd34

URL: https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34
Status: ok
Content type: application/json
Fetch method: api-recipe
API recipe: flashscore-odds@v1 (confidence=0.55, ttl=300s, captured=2026-08-06T17:50:27+00:00)

## Warnings

- Example-only (not builtin/supported). Flashscore Terms of Use prohibit automated requests and scraping without express consent (https://www.flashscore.com/terms-of-use/). Prefer offline fixtures; live GETs are the operator's responsibility.
- Observed endpoint shape (2.ds.lsapp.eu /pq_graphql _hash=ole2) is undocumented/unstable and may change without notice.
- Odds are a dated snapshot for research/agent context only — not betting advice.

## Summary

Flashscore 1X2 odds for event Ab12Cd34 (not betting advice). Betclic: 2.1/3.25/3.4 Winamax: 2.15/3.3/3.35 Unibet: 2.08/3.2/3.45 Betsson: 2.12/3.28/3.38 bwin: 2.05/3.4/3.5 Captured: 2026-08-06T17:50:27+00:00.

## Structured odds (not betting advice)

- Betclic: 2.1/3.25/3.4 (open 2.05/3.2/3.5)
- Winamax: 2.15/3.3/3.35 (open 2.1/3.25/3.4)
- Unibet: 2.08/3.2/3.45
- Betsson: 2.12/3.28/3.38 (open 2.0/3.1/3.6)
- bwin: 2.05/3.4/3.5 (open 2.02/3.3/3.55)

_Odds are a dated public snapshot for research/agent context only. This is not betting advice and must not be presented as a tip or recommendation._

## Content

{
  "kind": "flashscore_odds_1x2",
  "schema": "flashscore_prematch_1x2_v1",
  "match_url": "https://www.flashscore.com/match/football/demo-league/alpha-vs-beta/?mid=Ab12Cd34",
  "event_id": "Ab12Cd34",
  "market": "HOME_DRAW_AWAY",
  "scope": "FULL_TIME",
  "captured_at": "2026-08-06T17:50:27+00:00",
  "bookmakers": [
    {
      "bookmaker_id": 141,
      "bookmaker": "Betclic",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.1,
      "draw": 3.25,
      "away": 3.4,
      "opening": {
        "home": 2.05,
        "draw": 3.2,
        "away": 3.5
      }
    },
    {
      "bookmaker_id": 264,
      "bookmaker": "Winamax",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.15,
      "draw": 3.3,
      "away": 3.35,
      "opening": {
        "home": 2.1,
        "draw": 3.25,
        "away": 3.4
      }
    },
    {
      "bookmaker_id": 160,
      "bookmaker": "Unibet",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.08,
      "draw": 3.2,
      "away": 3.45,
      "opening": {
        "home": null,
        "draw": null,
        "away": null
      }
    },
    {
      "bookmaker_id": 905,
      "bookmaker": "Betsson",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.12,
      "draw": 3.28,
      "away": 3.38,
      "opening": {
        "home": 2.0,
        "draw": 3.1,
        "away": 3.6
      }
    },
    {
      "bookmaker_id": 129,
      "bookmaker": "bwin",
      "market": "1X2",
      "scope": "FULL_TIME",
      "home": 2.05,
      "draw": 3.4,
      "away": 3.5,
      "opening": {
        "home": 2.02,
        "draw": 3.3,
        "away": 3.55
      }
    }
  ],
  "disclaimer": "Odds are a dated public snapshot for research/agent context only. This is not betting advice and must not be presented as a tip or recommendation.",
  "provenance": "Pattern: observed Flashscore-related odds GraphQL GET shape (2.ds.lsapp.eu/pq_graphql, _hash=ole2). Undocumented and unstable. Example-only (not builtin/supported). Operators must respect Flashscore Terms of Use (https://www.flashscore.com/terms-of-use/).",
  "warnings": [
    "Example-only (not builtin/supported). Flashscore Terms of Use prohibit automated requests and scraping without express consent (https://www.flashscore.com/terms-of-use/). Prefer offline fixtures; live GETs are the operator's responsibility.",
    "Observed endpoint shape (2.ds.lsapp.eu /pq_graphql _hash=ole2) is undocumented/unstable and may change without notice.",
    "Odds are a dated snapshot for research/agent context only — not betting advice.",
    "Example-only (not builtin/supported). Flashscore Terms of Use prohibit automated requests and scraping without express consent (https://www.flashscore.com/terms-of-use/). Prefer offline fixtures; live GETs are the operator's responsibility.",
    "Observed endpoint shape (2.ds.lsapp.eu /pq_graphql _hash=ole2) is undocumented/unstable and may change without notice.",
    "Odds are a dated snapshot for research/agent context only — not betting advice."
  ]
}
