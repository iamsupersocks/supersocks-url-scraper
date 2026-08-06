"""Flashscore match URL helpers and 1X2 odds normalization (fixture-oriented).

Historical public GET shape (undocumented, may change):
  https://global.ds.lsapp.eu/odds/pq_graphql
  ?_hash=ope2&eventId=…&bookmakerId=…&betType=HOME_DRAW_AWAY&betScope=FULL_TIME

Flashscore Terms of Use prohibit automated requests and scraping without express
consent (https://www.flashscore.com/terms-of-use/). The shipped recipe is
fixture-only; live network access is not enabled by default.

Odds are informational snapshots, never betting advice.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..social.domains import host_matches_root

FLASHSCORE_HOST_ROOTS = (
    "flashscore.com",
    "flashscore.fr",
    "flashscore.co.uk",
    "flashscore.es",
    "flashscore.de",
    "flashscore.it",
    "flashscore.pl",
    "flashscore.pt",
    "flashscore.nl",
    "flashscore.ro",
    "flashscore.sk",
    "flashscore.cz",
    "flashscore.hu",
    "flashscore.hr",
    "flashscore.si",
    "flashscore.gr",
    "flashscore.com.tr",
    "flashscore.com.br",
    "flashscore.com.ar",
    "flashscore.ua",
    "flashscore.se",
    "flashscore.no",
    "flashscore.dk",
    "flashscore.fi",
    "flashscore.be",
    "flashscore.cl",
    "flashscore.com.mx",
    "flashscore.com.co",
    "flashscore.com.pe",
    "flashscore.com.ve",
    "flashscore.com.uy",
    "flashscore.com.bo",
    "flashscore.com.ec",
    "flashscore.com.py",
)

# Bounded public bookmaker set used only by fixtures / consented operators.
DEFAULT_BOOKMAKERS: tuple[dict[str, Any], ...] = (
    {"id": 141, "name": "Betclic"},
    {"id": 264, "name": "Winamax"},
    {"id": 160, "name": "Unibet"},
    {"id": 484, "name": "ParionsSport"},
    {"id": 905, "name": "Betsson"},
    {"id": 129, "name": "bwin"},
)

ODDS_ENDPOINT_HOST = "global.ds.lsapp.eu"
ODDS_URL_TEMPLATE = (
    "https://global.ds.lsapp.eu/odds/pq_graphql"
    "?_hash=ope2&eventId={event_id}&bookmakerId={bookmaker_id}"
    "&betType=HOME_DRAW_AWAY&betScope=FULL_TIME"
)

EVENT_ID_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
MID_QUERY_RE = re.compile(r"(?:^|[?&])mid=([A-Za-z0-9]{4,32})(?:&|$)")
PATH_ID_RE = re.compile(r"/match/(?:[a-z0-9-]+/)*([A-Za-z0-9]{6,12})(?:/|$)", re.I)

DISCLAIMER = (
    "Odds are a dated public snapshot for research/agent context only. "
    "This is not betting advice and must not be presented as a tip or recommendation."
)
PROVENANCE = (
    "Pattern: historical Flashscore-related odds GraphQL GET shape "
    "(global.ds.lsapp.eu/odds/pq_graphql). Undocumented and unstable. "
    "Shipped example is fixture-only because Flashscore Terms of Use prohibit "
    "automated requests/scraping without express consent "
    "(https://www.flashscore.com/terms-of-use/)."
)


def is_flashscore_match_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname
    if not any(host_matches_root(host, root) for root in FLASHSCORE_HOST_ROOTS):
        return False
    path = parsed.path or ""
    return "/match" in path.lower() or "mid=" in (parsed.query or "").lower()


def extract_event_id(url: str) -> str | None:
    """Extract a Flashscore event/match id from a public match URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query or "")
    for key in ("mid", "eventId", "event_id"):
        values = qs.get(key) or []
        if values and EVENT_ID_RE.fullmatch(str(values[0]).strip()):
            return str(values[0]).strip()
    fragment = parsed.fragment or ""
    mid = MID_QUERY_RE.search(fragment)
    if mid and EVENT_ID_RE.fullmatch(mid.group(1)):
        return mid.group(1)
    mid = MID_QUERY_RE.search(url)
    if mid and EVENT_ID_RE.fullmatch(mid.group(1)):
        return mid.group(1)
    path_match = PATH_ID_RE.search(parsed.path or "")
    if path_match and EVENT_ID_RE.fullmatch(path_match.group(1)):
        return path_match.group(1)
    return None


def _as_float(value: object) -> float | None:
    if value is None or value is False:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > 1000:
        return None
    return round(number, 3)


def _outcome_block(raw: object) -> dict[str, float | None]:
    if not isinstance(raw, dict):
        return {"value": None, "opening": None}
    return {
        "value": _as_float(raw.get("value")),
        "opening": _as_float(raw.get("opening")),
    }


def normalize_prematch_1x2(payload: object, *, bookmaker_id: int, bookmaker_name: str) -> dict[str, Any] | None:
    """Normalize findPrematchOddsForBookmaker into home/draw/away (+ opening)."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if "data" in payload else payload
    if not isinstance(data, dict):
        return None
    odds = data.get("findPrematchOddsForBookmaker")
    if not isinstance(odds, dict) or not odds:
        return None
    home = _outcome_block(odds.get("home"))
    draw = _outcome_block(odds.get("draw"))
    away = _outcome_block(odds.get("away"))
    if home["value"] is None or draw["value"] is None or away["value"] is None:
        return None
    return {
        "bookmaker_id": int(bookmaker_id),
        "bookmaker": bookmaker_name,
        "market": "1X2",
        "scope": "FULL_TIME",
        "home": home["value"],
        "draw": draw["value"],
        "away": away["value"],
        "opening": {
            "home": home["opening"],
            "draw": draw["opening"],
            "away": away["opening"],
        },
    }


def build_structured_odds(
    *,
    match_url: str,
    event_id: str,
    bookmaker_rows: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "kind": "flashscore_odds_1x2",
        "schema": "flashscore_prematch_1x2_v1",
        "match_url": match_url,
        "event_id": event_id,
        "market": "HOME_DRAW_AWAY",
        "scope": "FULL_TIME",
        "captured_at": captured_at,
        "bookmakers": bookmaker_rows,
        "disclaimer": DISCLAIMER,
        "provenance": PROVENANCE,
        "warnings": list(warnings or []),
    }


def compact_odds_summary(structured: dict[str, Any]) -> str:
    event_id = structured.get("event_id") or "?"
    rows = structured.get("bookmakers") or []
    parts = [f"Flashscore 1X2 odds for event {event_id} (not betting advice)."]
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        name = row.get("bookmaker") or row.get("bookmaker_id")
        parts.append(f"{name}: {row.get('home')}/{row.get('draw')}/{row.get('away')}")
    if not rows:
        parts.append("No bookmaker odds normalized.")
    parts.append(f"Captured: {structured.get('captured_at') or 'unknown'}.")
    return " ".join(parts)
