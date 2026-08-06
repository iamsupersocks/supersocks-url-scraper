"""Explicit consent / allowlist gates for network-using API recipes.

Flashscore Terms of Use (clause covering automated requests / scraping without
express consent — see https://www.flashscore.com/terms-of-use/) mean the
shipped Flashscore odds recipe uses ``network.mode=consent_required`` and stays
off by default. Live HTTPS GETs for consent-gated recipes require BOTH:

1. API_RECIPE_LIVE_ALLOWLIST including the recipe id (comma/space separated)
2. API_RECIPE_LIVE_CONSENT exactly equal to the required consent phrase

Injected fetchers (unit tests / offline fixtures) never touch the network and
do not need consent.
"""

from __future__ import annotations

import os
from typing import Any

# Exact phrase required — never soft-match, never default-enable.
DEFAULT_CONSENT_PHRASE = "I_HAVE_EXPRESS_WRITTEN_PERMISSION"

FLASHSCORE_TOS_WARNING = (
    "Flashscore Terms of Use prohibit automated requests and scraping without "
    "express consent (https://www.flashscore.com/terms-of-use/). Live network "
    "access for recipe flashscore-odds is off by default and consent-gated: set "
    "API_RECIPE_LIVE_ALLOWLIST to include flashscore-odds and "
    f"API_RECIPE_LIVE_CONSENT={DEFAULT_CONSENT_PHRASE} only when you possess "
    "express written permission per current Terms."
)


def _split_allowlist(raw: str | None) -> set[str]:
    if not raw:
        return set()
    parts = []
    for chunk in raw.replace(",", " ").split():
        item = chunk.strip()
        if item:
            parts.append(item)
    return set(parts)


def live_network_permitted(
    recipe_id: str,
    *,
    network_mode: str = "consent_required",
    consent_phrase: str = DEFAULT_CONSENT_PHRASE,
    env: dict[str, str] | None = None,
) -> bool:
    """Return True only when live outbound GETs are explicitly authorized."""
    mode = (network_mode or "consent_required").strip().lower()
    if mode in {"off", "disabled", "fixture_only", "never"}:
        return False
    if mode in {"open", "allow"}:
        # Reserved for non-Flashscore recipes that are intentionally public-safe.
        # Still requires the global API recipes opt-in at the caller.
        return True
    if mode not in {"consent_required", "allowlist"}:
        return False

    source: dict[str, str] = env if env is not None else dict(os.environ)
    allowlist = _split_allowlist(source.get("API_RECIPE_LIVE_ALLOWLIST"))
    if recipe_id not in allowlist:
        return False
    consent = (source.get("API_RECIPE_LIVE_CONSENT") or "").strip()
    required = (consent_phrase or DEFAULT_CONSENT_PHRASE).strip()
    return bool(required) and consent == required


def network_gate_message(recipe: Any) -> str:
    recipe_id = getattr(recipe, "id", None) or (recipe.get("id") if isinstance(recipe, dict) else "?")
    if str(recipe_id) == "flashscore-odds":
        return FLASHSCORE_TOS_WARNING
    return (
        f"Live network for recipe {recipe_id} is blocked: set API_RECIPE_LIVE_ALLOWLIST "
        f"to include '{recipe_id}' and API_RECIPE_LIVE_CONSENT={DEFAULT_CONSENT_PHRASE}."
    )
