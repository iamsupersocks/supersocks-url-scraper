"""Offline, machine-readable route advice for agents (no network).

``route_advice`` steers agents toward known API recipes or offline HAR
discovery when a need is recurrent / costly — without sniffing, capturing,
activating, or executing anything automatically.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..social.domains import detect_platform
from .models import ApiRecipe
from .consent import DEFAULT_CONSENT_PHRASE

# Stable contract values (documented + tested).
RECOMMENDED_API_RECIPE = "api_recipe"
RECOMMENDED_REVIEW_RECIPE = "review_recipe"
RECOMMENDED_API_DISCOVERY = "api_discovery"
RECOMMENDED_STANDARD_PIPELINE = "standard_pipeline"

STATE_AVAILABLE_DISABLED = "available_disabled"
STATE_FIXTURE_ONLY = "fixture_only"
STATE_REVIEW_REQUIRED = "review_required"
STATE_USED = "used"
STATE_BLOCKED = "blocked"
STATE_SUGGESTED = "suggested"

_BLOCKED_NETWORK_MODES = frozenset({"fixture_only", "off", "disabled", "never"})
_COSTLY_FETCH_METHODS = frozenset({"cloak", "cloak-profile", "fallback", "archive"})
_NON_HTML_CONTENT = frozenset({"pdf", "image", "unknown"})
_BINARY_SUFFIXES = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",
    ".mp4",
    ".mp3",
    ".zip",
    ".gz",
    ".tgz",
    ".rar",
    ".7z",
    ".woff",
    ".woff2",
    ".ttf",
)


def recipe_advice_meta(recipe: ApiRecipe) -> dict[str, Any]:
    return {
        "id": recipe.id,
        "version": recipe.version,
        "network_mode": recipe.network.mode,
        "status": recipe.status,
        "review_required": recipe.needs_review,
    }


def unsuitable_for_api_discovery(
    url: str,
    *,
    content_type: str | None = None,
    platform: str | None = None,
) -> bool:
    """True for PDF/image/social and other routes where HAR→JSON discovery is a poor fit."""
    if platform or detect_platform(url):
        return True
    ctype = (content_type or "").strip().lower()
    if ctype in _NON_HTML_CONTENT:
        return True
    if "pdf" in ctype or ctype.startswith("image/"):
        return True
    path = (urlparse(url).path or "").lower()
    return any(path.endswith(suffix) for suffix in _BINARY_SUFFIXES)


def _is_costly_or_partial(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    method = str(result.get("fetch_method") or "")
    if method in _COSTLY_FETCH_METHODS:
        return True
    return str(result.get("status") or "") == "partial"


def _discovery_command() -> str:
    return (
        "# 1) Capture a HAR manually in your browser DevTools (no auto-sniff).\n"
        "# 2) Classify offline (never opens a socket):\n"
        "supersocks-url-scraper --discover-har capture.har --discovery-source-url <url>\n"
        "# 3) Review the disabled candidate, then load deliberately:\n"
        "# API_RECIPE_PATHS=./candidate-recipe.v1.json supersocks-url-scraper --api-recipes <url>\n"
    )


def _enable_command() -> str:
    return (
        "supersocks-url-scraper --api-recipes <url>   # or API_RECIPES=1 / api_recipes:true\n"
        "# Load external recipes explicitly via API_RECIPE_PATHS=… or --api-recipe-path"
    )


def _consent_required_requires(recipe: ApiRecipe) -> list[str]:
    phrase = (recipe.network.consent_phrase or DEFAULT_CONSENT_PHRASE).strip()
    return [
        "api_recipes=true",
        f"API_RECIPE_LIVE_ALLOWLIST includes {recipe.id}",
        f"API_RECIPE_LIVE_CONSENT={phrase}",
        "express_written_permission_per_site_ToS",
    ]


def _consent_activation_command(recipe: ApiRecipe) -> str:
    phrase = (recipe.network.consent_phrase or DEFAULT_CONSENT_PHRASE).strip()
    return (
        f"API_RECIPE_LIVE_ALLOWLIST={recipe.id} "
        f"API_RECIPE_LIVE_CONSENT={phrase} "
        "supersocks-url-scraper --api-recipes <url>"
    )


def _fixture_command(recipe: ApiRecipe) -> str:
    return (
        f"# Recipe {recipe.recipe_key} is fixture_only — use an injected fetcher in tests/demos; "
        "do not attempt live GETs. Load external recipes via API_RECIPE_PATHS / --api-recipe-path."
    )


def build_route_advice(
    url: str,
    *,
    matched: ApiRecipe | None = None,
    api_recipes_enabled: bool = False,
    recipe_used: bool = False,
    recipe_blocked_fallback: bool = False,
    block_reason: str | None = None,
    recurrent_need: bool = False,
    result: dict[str, Any] | None = None,
    network_attempted: bool | None = None,
) -> dict[str, Any] | None:
    """Return a discrete ``route_advice`` dict, or None when no useful advice exists.

    Matching and advice are offline — no sockets, no HAR capture, no activation.
    """
    content_type = None
    platform = None
    if isinstance(result, dict):
        content_type = result.get("content_type")
        platform = result.get("platform")

    if recipe_used and matched is not None:
        attempted = (
            network_attempted
            if network_attempted is not None
            else matched.network.mode not in _BLOCKED_NETWORK_MODES
        )
        return {
            "recommended": RECOMMENDED_API_RECIPE,
            "state": STATE_USED,
            "reason": f"Opt-in API recipe {matched.recipe_key} produced the structured result.",
            "recipe": recipe_advice_meta(matched),
            "network_attempted": attempted,
        }

    if matched is not None and matched.needs_review:
        return {
            "recommended": RECOMMENDED_REVIEW_RECIPE,
            "state": STATE_REVIEW_REQUIRED,
            "reason": (
                f"Recipe {matched.recipe_key} is marked {matched.status} "
                "(review_required). Do not execute it; review the candidate offline, "
                "then activate deliberately."
            ),
            "recipe": recipe_advice_meta(matched),
            "requires": ["human_or_agent_review", "explicit_activation"],
            "next_command": (
                "supersocks-url-scraper --validate-recipe <candidate-recipe.v1.json>  "
                "# then edit status/network.mode only after review"
            ),
            "network_attempted": False,
        }

    if recipe_blocked_fallback and matched is not None:
        reason = block_reason or (
            f"Recipe {matched.recipe_key} was blocked; fell back to "
            f"{matched.fallback or 'http_seo_cloak_archive'}."
        )
        consent_blocked = matched.network.mode in {"consent_required", "allowlist"}
        return {
            "recommended": RECOMMENDED_STANDARD_PIPELINE,
            "state": STATE_BLOCKED,
            "reason": reason,
            "recipe": recipe_advice_meta(matched),
            "requires": _consent_required_requires(matched) if consent_blocked else [],
            "next_command": (
                _consent_activation_command(matched)
                if consent_blocked
                else (
                    f"# Fallback pipeline already used ({matched.fallback}). "
                    "Do not auto-enable live network."
                )
            ),
            "network_attempted": False,
        }

    if matched is not None and matched.network.mode in _BLOCKED_NETWORK_MODES:
        # fixture_only / off / disabled / never — never advise live enablement.
        return {
            "recommended": RECOMMENDED_API_RECIPE,
            "state": STATE_FIXTURE_ONLY,
            "reason": (
                f"Recipe {matched.recipe_key} is {matched.network.mode}: use fixture/demo "
                "or an injected fetcher. Do not attempt live network access."
            ),
            "recipe": recipe_advice_meta(matched),
            "requires": ["fixture_or_injected_fetcher"],
            "next_command": _fixture_command(matched),
            "network_attempted": False,
        }

    if matched is not None and not api_recipes_enabled:
        return {
            "recommended": RECOMMENDED_API_RECIPE,
            "state": STATE_AVAILABLE_DISABLED,
            "reason": (
                f"Known API recipe {matched.recipe_key} matches this URL but recipes are "
                "disabled. Enable them explicitly to use the structured adapter."
            ),
            "recipe": recipe_advice_meta(matched),
            "requires": ["api_recipes=true", "API_RECIPES=1", "--api-recipes"],
            "next_command": _enable_command(),
            "network_attempted": False,
        }

    # No useful recipe path — maybe suggest offline discovery.
    if matched is None:
        want_discovery = recurrent_need or _is_costly_or_partial(result)
        if not want_discovery:
            return None
        if unsuitable_for_api_discovery(url, content_type=content_type, platform=platform):
            return None
        why = (
            "Recurrent need flagged and no loaded recipe matches this URL."
            if recurrent_need
            else "Route looked costly or partial; a stable JSON adapter may help if one exists."
        )
        return {
            "recommended": RECOMMENDED_API_DISCOVERY,
            "state": STATE_SUGGESTED,
            "reason": (
                f"{why} Capture a HAR manually in the browser, then run offline "
                "`--discover-har` (no auto-sniff, no live activation)."
            ),
            "requires": ["manual_har_capture", "offline_discover_har", "human_review"],
            "next_command": _discovery_command(),
            "network_attempted": False,
        }

    return None


def attach_route_advice(result: dict[str, Any], advice: dict[str, Any] | None) -> dict[str, Any]:
    """Attach advice when present; omit the key when advice is None (discrete default)."""
    if advice:
        result["route_advice"] = advice
    else:
        result.pop("route_advice", None)
    return result


def format_route_advice_markdown(advice: dict[str, Any] | None) -> list[str]:
    """Concise Markdown lines for ``to_markdown`` (empty when no advice)."""
    if not isinstance(advice, dict) or not advice:
        return []
    lines = [
        "",
        "## Route advice",
        "",
        f"- recommended: `{advice.get('recommended')}`",
        f"- state: `{advice.get('state')}`",
        f"- reason: {advice.get('reason')}",
        f"- network_attempted: {advice.get('network_attempted', False)}",
    ]
    recipe = advice.get("recipe")
    if isinstance(recipe, dict) and recipe:
        lines.append(
            "- recipe: "
            f"`{recipe.get('id')}@v{recipe.get('version')}` "
            f"(network_mode={recipe.get('network_mode')}, status={recipe.get('status')})"
        )
    requires = advice.get("requires")
    if isinstance(requires, list) and requires:
        lines.append("- requires: " + ", ".join(f"`{r}`" for r in requires))
    next_cmd = advice.get("next_command")
    if next_cmd:
        lines += ["", "```", str(next_cmd).rstrip(), "```"]
    return lines
