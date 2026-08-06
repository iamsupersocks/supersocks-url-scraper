"""Optional, versioned, read-only API recipes for structured agent outputs.

Opt-in only. Recipes are HTTPS GET, schema-validated, host-allowlisted, and
degrade to the normal HTTP → SEO → Cloak → archive reader pipeline when they
fail. StrategyCache still stores only http/seo/cloak/archive routes.

The shipped Flashscore odds recipe is fixture-only by default (Flashscore ToS
prohibit automated requests/scraping without express consent).
"""

from __future__ import annotations

from .consent import DEFAULT_CONSENT_PHRASE, FLASHSCORE_TOS_WARNING, live_network_permitted
from .engine import (
    execute_recipe,
    find_matching_recipes,
    load_builtin_recipes,
    load_recipes,
    recipe_from_dict,
    try_api_recipe,
    validate_recipe_dict,
)
from .flashscore_odds import (
    DISCLAIMER,
    extract_event_id,
    is_flashscore_match_url,
    normalize_prematch_1x2,
)
from .models import ApiRecipe, RecipeRunResult
from .security import ApiRecipeSecurityError, scrub_headers, safe_get

__all__ = [
    "ApiRecipe",
    "ApiRecipeSecurityError",
    "DEFAULT_CONSENT_PHRASE",
    "DISCLAIMER",
    "FLASHSCORE_TOS_WARNING",
    "RecipeRunResult",
    "execute_recipe",
    "extract_event_id",
    "find_matching_recipes",
    "is_flashscore_match_url",
    "live_network_permitted",
    "load_builtin_recipes",
    "load_recipes",
    "normalize_prematch_1x2",
    "recipe_from_dict",
    "safe_get",
    "scrub_headers",
    "try_api_recipe",
    "validate_recipe_dict",
]
