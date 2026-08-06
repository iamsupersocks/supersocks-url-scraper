"""Explicit consent / allowlist gates for network-using API recipes.

Live HTTPS GETs are controlled by each recipe's ``network.mode``:

- ``fixture_only`` / ``off`` / ``disabled`` / ``never`` — never live
- ``consent_required`` / ``allowlist`` — needs allowlist + consent phrase
- ``open`` / ``allow`` — permitted when the global API-recipes opt-in is on

Injected fetchers (unit tests / offline fixtures) never touch the network and
do not need consent.
"""

from __future__ import annotations

import os
from typing import Any

# Exact phrase required — never soft-match, never default-enable.
DEFAULT_CONSENT_PHRASE = "I_HAVE_EXPRESS_WRITTEN_PERMISSION"


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
        # Intentionally public-safe recipes still require the global API recipes
        # opt-in at the caller (api_recipes=true / --api-recipes / API_RECIPES=1).
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
    network = getattr(recipe, "network", None)
    mode = getattr(network, "mode", None) if network is not None else None
    if mode is None and isinstance(recipe, dict):
        mode = (recipe.get("network") or {}).get("mode")
    mode = str(mode or "consent_required").strip().lower()
    if mode in {"fixture_only", "off", "disabled", "never"}:
        return (
            f"Live network for recipe {recipe_id} is blocked by network.mode={mode}; "
            "use an injected fetcher or a fixture demo."
        )
    return (
        f"Live network for recipe {recipe_id} is blocked: set API_RECIPE_LIVE_ALLOWLIST "
        f"to include '{recipe_id}' and API_RECIPE_LIVE_CONSENT={DEFAULT_CONSENT_PHRASE}."
    )
