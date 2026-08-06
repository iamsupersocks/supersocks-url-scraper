"""Optional, versioned, read-only API recipes for structured agent outputs.

Opt-in only. Recipes are HTTPS GET, schema-validated, host-allowlisted, and
degrade to the normal HTTP → SEO → Cloak → archive reader pipeline when they
fail. StrategyCache still stores only http/seo/cloak/archive routes.

The shipped Flashscore odds recipe is fixture-only by default (Flashscore ToS
prohibit automated requests/scraping without express consent).
"""

from __future__ import annotations

from .consent import DEFAULT_CONSENT_PHRASE, FLASHSCORE_TOS_WARNING, live_network_permitted
from .discovery import (
    CandidateEntry,
    DiscoveryReport,
    build_candidate_recipe,
    classify_har_entry,
    discover_from_har,
    iter_har_entries,
    load_har,
    redact_query,
    render_report_json,
    render_report_markdown,
    write_report,
)
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
from .schema import (
    RECIPE_SCHEMA_V1,
    load_schema,
    schema_file_contents,
    validate_recipe_schema,
)
from .security import ApiRecipeSecurityError, scrub_headers, safe_get

__all__ = [
    "ApiRecipe",
    "ApiRecipeSecurityError",
    "CandidateEntry",
    "DEFAULT_CONSENT_PHRASE",
    "DISCLAIMER",
    "DiscoveryReport",
    "FLASHSCORE_TOS_WARNING",
    "RECIPE_SCHEMA_V1",
    "RecipeRunResult",
    "build_candidate_recipe",
    "classify_har_entry",
    "discover_from_har",
    "execute_recipe",
    "extract_event_id",
    "find_matching_recipes",
    "is_flashscore_match_url",
    "iter_har_entries",
    "live_network_permitted",
    "load_builtin_recipes",
    "load_recipes",
    "load_schema",
    "normalize_prematch_1x2",
    "recipe_from_dict",
    "redact_query",
    "render_report_json",
    "render_report_markdown",
    "safe_get",
    "schema_file_contents",
    "scrub_headers",
    "try_api_recipe",
    "validate_recipe_dict",
    "validate_recipe_schema",
    "write_report",
]
