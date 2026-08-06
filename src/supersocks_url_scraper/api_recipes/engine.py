"""Load, match, validate, and execute optional API recipes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..social.backend import redact_secrets
from ..social.domains import host_matches_root, is_safe_public_http_url, url_has_userinfo
from .consent import FLASHSCORE_TOS_WARNING, live_network_permitted, network_gate_message
from .flashscore_odds import (
    build_structured_odds,
    compact_odds_summary,
    extract_event_id,
    is_flashscore_match_url,
    normalize_prematch_1x2,
)
from .models import (
    ALLOWED_METHODS,
    ApiRecipe,
    RecipeEndpoint,
    RecipeMatch,
    RecipeNetworkPolicy,
    RecipeRunResult,
    REQUIRED_RECIPE_KEYS,
)
from .security import (
    DEFAULT_MAX_FANOUT,
    ApiRecipeSecurityError,
    RateLimiter,
    safe_get,
)

Fetcher = Callable[..., Any]


def _recipes_dir() -> Path:
    return Path(__file__).resolve().parent / "recipes"


def validate_recipe_dict(raw: dict[str, Any]) -> list[str]:
    """Return schema validation errors (empty when useful/valid)."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["recipe must be an object"]
    missing = REQUIRED_RECIPE_KEYS - set(raw)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    recipe_id = str(raw.get("id") or "").strip()
    if not recipe_id:
        errors.append("id is required")
    version = str(raw.get("version") or "").strip()
    if not version:
        errors.append("version is required")
    match = raw.get("match")
    if not isinstance(match, dict):
        errors.append("match must be an object")
    else:
        roots = match.get("host_roots")
        if not isinstance(roots, list) or not roots:
            errors.append("match.host_roots must be a non-empty list")
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, dict):
        errors.append("endpoint must be an object")
    else:
        method = str(endpoint.get("method") or "").upper()
        if method not in ALLOWED_METHODS:
            errors.append("endpoint.method must be GET")
        if str(endpoint.get("url_template") or "").strip().lower().startswith("http://"):
            errors.append("endpoint.url_template must be https")
        if "https://" not in str(endpoint.get("url_template") or "").lower():
            errors.append("endpoint.url_template must include https://")
        hosts = endpoint.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts:
            errors.append("endpoint.allowed_hosts must be a non-empty list")
        for key in ("timeout_seconds", "max_bytes", "max_fanout", "min_interval_ms", "max_redirects"):
            if key in endpoint and (not isinstance(endpoint[key], int) or endpoint[key] < 0):
                errors.append(f"endpoint.{key} must be a non-negative int")
        headers = endpoint.get("headers") or {}
        if headers and not isinstance(headers, dict):
            errors.append("endpoint.headers must be an object")
        elif isinstance(headers, dict):
            for name in headers:
                lowered = str(name).lower()
                if lowered in {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}:
                    errors.append(f"endpoint.headers must not include {name}")
    confidence = raw.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        errors.append("confidence must be between 0 and 1")
    ttl = raw.get("ttl_seconds", 300)
    if not isinstance(ttl, int) or ttl < 0:
        errors.append("ttl_seconds must be a non-negative int")
    network = raw.get("network")
    if network is not None:
        if not isinstance(network, dict):
            errors.append("network must be an object")
        else:
            mode = str(network.get("mode") or "").strip().lower()
            if mode and mode not in {
                "fixture_only",
                "off",
                "disabled",
                "never",
                "consent_required",
                "allowlist",
                "open",
                "allow",
            }:
                errors.append("network.mode is invalid")
    return errors


def recipe_from_dict(raw: dict[str, Any]) -> ApiRecipe:
    errors = validate_recipe_dict(raw)
    if errors:
        raise ValueError("invalid recipe: " + "; ".join(errors))
    match_raw = raw["match"]
    endpoint_raw = raw["endpoint"]
    headers = {str(k): str(v) for k, v in (endpoint_raw.get("headers") or {}).items()}
    network_raw = raw.get("network") if isinstance(raw.get("network"), dict) else {}
    default_mode = "consent_required"
    network = RecipeNetworkPolicy(
        mode=str(network_raw.get("mode") or default_mode).strip().lower() or default_mode,
        consent_phrase=str(network_raw.get("consent_phrase") or RecipeNetworkPolicy().consent_phrase),
    )
    status = str(raw.get("status") or "active").strip().lower() or "active"
    review_required = bool(raw.get("review_required")) or status in {"review_required", "disabled"}
    return ApiRecipe(
        id=str(raw["id"]).strip(),
        version=str(raw["version"]).strip(),
        title=str(raw.get("title") or raw["id"]),
        match=RecipeMatch(
            host_roots=tuple(str(x) for x in match_raw.get("host_roots") or ()),
            path_regex=str(match_raw.get("path_regex") or ""),
            query_keys=tuple(str(x) for x in match_raw.get("query_keys") or ()),
        ),
        endpoint=RecipeEndpoint(
            method=str(endpoint_raw.get("method") or "GET").upper(),
            url_template=str(endpoint_raw["url_template"]),
            allowed_hosts=tuple(str(x) for x in endpoint_raw.get("allowed_hosts") or ()),
            timeout_seconds=int(endpoint_raw.get("timeout_seconds") or 8),
            max_bytes=int(endpoint_raw.get("max_bytes") or 256 * 1024),
            max_fanout=min(DEFAULT_MAX_FANOUT, int(endpoint_raw.get("max_fanout") or DEFAULT_MAX_FANOUT)),
            min_interval_ms=int(endpoint_raw.get("min_interval_ms") or 50),
            max_redirects=int(endpoint_raw.get("max_redirects") or 3),
            headers=headers,
        ),
        confidence=float(raw.get("confidence") or 0.5),
        ttl_seconds=int(raw.get("ttl_seconds") or 300),
        params=dict(raw.get("params") or {}),
        response=dict(raw.get("response") or {}),
        warnings=tuple(str(w) for w in (raw.get("warnings") or [])),
        fallback=str(raw.get("fallback") or "http_seo_cloak_archive"),
        schema=str(raw.get("schema") or (raw.get("response") or {}).get("schema") or ""),
        network=network,
        status=status,
        review_required=review_required,
    )


def load_recipe_file(path: Path) -> ApiRecipe:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return recipe_from_dict(raw)


def load_builtin_recipes() -> list[ApiRecipe]:
    recipes: list[ApiRecipe] = []
    directory = _recipes_dir()
    if not directory.is_dir():
        return recipes
    for path in sorted(directory.glob("*.json")):
        recipes.append(load_recipe_file(path))
    return recipes


def load_recipes(*, extra_paths: list[str] | None = None) -> list[ApiRecipe]:
    recipes = load_builtin_recipes()
    for raw_path in extra_paths or []:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            for child in sorted(path.glob("*.json")):
                recipes.append(load_recipe_file(child))
        elif path.is_file():
            recipes.append(load_recipe_file(path))
    seen: set[str] = set()
    unique: list[ApiRecipe] = []
    for recipe in recipes:
        if recipe.recipe_key in seen:
            continue
        seen.add(recipe.recipe_key)
        unique.append(recipe)
    return unique


def recipe_matches_url(recipe: ApiRecipe, url: str) -> bool:
    if not is_safe_public_http_url(url) or url_has_userinfo(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname
    if not any(host_matches_root(host, root) for root in recipe.match.host_roots):
        return False
    path = parsed.path or ""
    if recipe.match.path_regex:
        if re.search(recipe.match.path_regex, path, re.I) is None and recipe.match.path_regex.lower() not in path.lower():
            if not recipe.match.query_keys:
                return False
            query = (parsed.query or "").lower()
            if not any(f"{key.lower()}=" in query for key in recipe.match.query_keys):
                return False
    if recipe.id == "flashscore-odds":
        return is_flashscore_match_url(url) and extract_event_id(url) is not None
    return True


def find_matching_recipes(url: str, recipes: list[ApiRecipe] | None = None) -> list[ApiRecipe]:
    catalog = recipes if recipes is not None else load_recipes()
    return [recipe for recipe in catalog if recipe_matches_url(recipe, url)]


def _bookmakers(recipe: ApiRecipe) -> list[dict[str, Any]]:
    raw = recipe.params.get("bookmakers") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            bookmaker_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or bookmaker_id)
        out.append({"id": bookmaker_id, "name": name})
    return out[: recipe.endpoint.max_fanout]


def _can_use_live_fetcher(recipe: ApiRecipe, fetcher: Fetcher | None) -> bool:
    """Injected fetchers are offline; live safe_get requires consent/mode."""
    if fetcher is not None:
        return True
    return live_network_permitted(
        recipe.id,
        network_mode=recipe.network.mode,
        consent_phrase=recipe.network.consent_phrase,
    )


def run_flashscore_odds_recipe(
    url: str,
    recipe: ApiRecipe,
    *,
    length: int = 900,
    include_content: bool = False,
    fetcher: Fetcher | None = None,
    resolve_dns: bool = True,
) -> RecipeRunResult:
    warnings = [redact_secrets(w) for w in recipe.warnings]
    event_id = extract_event_id(url)
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _error(extra: str, *, structured: dict[str, Any] | None = None) -> RecipeRunResult:
        return RecipeRunResult(
            status="error",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=None,
            summary="",
            structured_data=structured or {},
            warnings=warnings + [extra],
            confidence=0.0,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )

    if not event_id:
        return _error("flashscore recipe: could not extract event id from URL")

    if not _can_use_live_fetcher(recipe, fetcher):
        # Never open a live socket for Flashscore without explicit authorization.
        msg = network_gate_message(recipe)
        if FLASHSCORE_TOS_WARNING not in warnings:
            warnings.append(FLASHSCORE_TOS_WARNING)
        return _error(
            msg,
            structured={
                "kind": "flashscore_odds_1x2",
                "schema": "flashscore_prematch_1x2_v1",
                "event_id": event_id,
                "match_url": url,
                "bookmakers": [],
                "network_blocked": True,
                "disclaimer": "Odds are not betting advice.",
                "provenance": "consent-gated by default; live blocked pending allowlist+consent",
            },
        )

    bookmakers = _bookmakers(recipe)
    if not bookmakers:
        return _error("flashscore recipe: no bookmakers configured")

    limiter = RateLimiter(recipe.endpoint.min_interval_ms)
    rows: list[dict[str, Any]] = []
    fetch = fetcher or safe_get
    rate_limited = False
    forbidden = False

    for bookmaker in bookmakers:
        limiter.wait()
        endpoint_url = recipe.endpoint.url_template.format(
            event_id=event_id,
            bookmaker_id=bookmaker["id"],
        )
        try:
            result = fetch(
                endpoint_url,
                timeout=recipe.endpoint.timeout_seconds,
                max_bytes=recipe.endpoint.max_bytes,
                max_redirects=recipe.endpoint.max_redirects,
                headers=dict(recipe.endpoint.headers),
                allowed_hosts=frozenset(recipe.endpoint.allowed_hosts),
                resolve_dns=resolve_dns,
            )
            payload = json.loads(result.content.decode("utf-8", errors="replace"))
            normalized = normalize_prematch_1x2(
                payload,
                bookmaker_id=int(bookmaker["id"]),
                bookmaker_name=str(bookmaker["name"]),
            )
            if normalized:
                rows.append(normalized)
        except ApiRecipeSecurityError as exc:
            message = redact_secrets(str(exc))
            if "429" in message:
                rate_limited = True
                warnings.append(f"bookmaker {bookmaker['name']}: {message}")
                break
            if "401" in message or "403" in message:
                forbidden = True
            warnings.append(f"bookmaker {bookmaker['name']}: {message}")
        except Exception as exc:  # noqa: BLE001 — keep recipe resilient
            warnings.append(f"bookmaker {bookmaker['name']}: {redact_secrets(type(exc).__name__)}")

    structured = build_structured_odds(
        match_url=url,
        event_id=event_id,
        bookmaker_rows=rows,
        warnings=warnings,
    )
    structured["captured_at"] = captured_at
    summary = compact_odds_summary(structured)
    title = f"Flashscore odds {event_id}"

    if rows:
        status = "ok"
        confidence = recipe.confidence
    elif rate_limited or forbidden:
        status = "error"
        confidence = 0.0
        summary = ""
    else:
        status = "partial"
        confidence = max(0.0, recipe.confidence * 0.25)
        warnings.append("flashscore recipe: no odds rows normalized; consider HTTP/SEO/Cloak/archive fallback")

    result = RecipeRunResult(
        status=status,
        url=url,
        recipe_id=recipe.id,
        recipe_version=recipe.version,
        fetch_method="api-recipe",
        title=title if rows else None,
        summary=summary,
        structured_data=(
            structured
            if rows or status != "error"
            else {
                "event_id": event_id,
                "bookmakers": [],
                "disclaimer": structured.get("disclaimer"),
                "provenance": structured.get("provenance"),
            }
        ),
        warnings=warnings,
        confidence=confidence,
        captured_at=captured_at,
        ttl_seconds=recipe.ttl_seconds,
        length=length,
    )
    _ = include_content
    return result


def run_generic_get_recipe(
    url: str,
    recipe: ApiRecipe,
    *,
    length: int = 900,
    include_content: bool = False,
    fetcher: Fetcher | None = None,
    resolve_dns: bool = True,
) -> RecipeRunResult:
    """Single HTTPS GET recipe: return JSON body as structured_data when allowed."""
    warnings = [redact_secrets(w) for w in recipe.warnings]
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not _can_use_live_fetcher(recipe, fetcher):
        return RecipeRunResult(
            status="error",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=None,
            summary="",
            structured_data={"network_blocked": True},
            warnings=warnings + [network_gate_message(recipe)],
            confidence=0.0,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )

    fetch = fetcher or safe_get
    endpoint_url = recipe.endpoint.url_template
    # Minimal template substitution from the page URL host/path when present.
    parsed = urlparse(url)
    try:
        endpoint_url = endpoint_url.format(
            url=url,
            host=parsed.hostname or "",
            path=parsed.path or "",
        )
    except (KeyError, ValueError):
        pass

    try:
        result = fetch(
            endpoint_url,
            timeout=recipe.endpoint.timeout_seconds,
            max_bytes=recipe.endpoint.max_bytes,
            max_redirects=recipe.endpoint.max_redirects,
            headers=dict(recipe.endpoint.headers),
            allowed_hosts=frozenset(recipe.endpoint.allowed_hosts),
            resolve_dns=resolve_dns,
        )
        text = result.content.decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw_text": text[:4000]}
        structured = {
            "kind": "api_recipe_generic",
            "schema": recipe.schema or "generic_json_v1",
            "source_url": url,
            "endpoint_url": endpoint_url,
            "captured_at": captured_at,
            "payload": body,
            "warnings": warnings,
            "provenance": "Opt-in API recipe HTTPS GET (read-only). Undocumented endpoints may change.",
        }
        return RecipeRunResult(
            status="ok",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=recipe.title,
            summary=f"API recipe {recipe.recipe_key} returned structured JSON.",
            structured_data=structured,
            warnings=warnings,
            confidence=recipe.confidence,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )
    except ApiRecipeSecurityError as exc:
        return RecipeRunResult(
            status="error",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=None,
            summary="",
            structured_data={},
            warnings=warnings + [redact_secrets(str(exc))],
            confidence=0.0,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )
    finally:
        _ = include_content


def execute_recipe(
    url: str,
    recipe: ApiRecipe,
    *,
    length: int = 900,
    include_content: bool = False,
    fetcher: Fetcher | None = None,
    resolve_dns: bool = True,
) -> RecipeRunResult:
    if recipe.endpoint.method not in ALLOWED_METHODS:
        raise ApiRecipeSecurityError("only GET recipes are supported")
    if recipe.id == "flashscore-odds":
        return run_flashscore_odds_recipe(
            url,
            recipe,
            length=length,
            include_content=include_content,
            fetcher=fetcher,
            resolve_dns=resolve_dns,
        )
    return run_generic_get_recipe(
        url,
        recipe,
        length=length,
        include_content=include_content,
        fetcher=fetcher,
        resolve_dns=resolve_dns,
    )


def try_api_recipe(
    url: str,
    *,
    enabled: bool = False,
    length: int = 900,
    include_content: bool = False,
    recipes: list[ApiRecipe] | None = None,
    extra_recipe_paths: list[str] | None = None,
    fetcher: Fetcher | None = None,
    resolve_dns: bool = True,
) -> dict[str, Any] | None:
    """Opt-in entrypoint: return a reader-shaped dict when a recipe succeeds.

    Returns None when recipes are disabled, no recipe matches, or the recipe
    fails hard enough that the caller should fall back to HTTP→SEO→Cloak→archive.

    Recipes with ``status=review_required`` / ``disabled`` never execute; a soft
    fallback payload is returned so the reader can continue the standard pipeline.
    """
    if not enabled:
        return None
    if not is_safe_public_http_url(url):
        return None
    catalog = recipes if recipes is not None else load_recipes(extra_paths=extra_recipe_paths)
    matches = find_matching_recipes(url, catalog)
    if not matches:
        return None
    recipe = matches[0]
    if recipe.needs_review:
        captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        run = RecipeRunResult(
            status="error",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=None,
            summary="",
            structured_data={"review_required": True, "execution_blocked": True},
            warnings=list(recipe.warnings)
            + [
                f"API recipe {recipe.recipe_key} is {recipe.status}; execution blocked pending review. "
                "Activate deliberately after offline validation — never auto-promote."
            ],
            confidence=0.0,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )
        payload = run.as_reader_dict(include_content=include_content)
        payload["_api_recipe_fallback"] = True
        payload["_api_recipe_review_blocked"] = True
        return payload
    # Skip network-blocked recipes early so reader falls through cleanly.
    if fetcher is None and not live_network_permitted(
        recipe.id,
        network_mode=recipe.network.mode,
        consent_phrase=recipe.network.consent_phrase,
    ):
        # Surface a soft signal then force fallback (no live attempt).
        run = execute_recipe(
            url,
            recipe,
            length=length,
            include_content=include_content,
            fetcher=None,
            resolve_dns=resolve_dns,
        )
        payload = run.as_reader_dict(include_content=include_content)
        payload["_api_recipe_fallback"] = True
        return payload

    run = execute_recipe(
        url,
        recipe,
        length=length,
        include_content=include_content,
        fetcher=fetcher,
        resolve_dns=resolve_dns,
    )
    payload = run.as_reader_dict(include_content=include_content)
    live_used = fetcher is None and live_network_permitted(
        recipe.id,
        network_mode=recipe.network.mode,
        consent_phrase=recipe.network.consent_phrase,
    )
    if run.status == "error":
        payload["_api_recipe_fallback"] = True
    elif run.status == "partial" and not (run.structured_data or {}).get("bookmakers"):
        # Flashscore partial-empty → fallback; generic partials keep payload.
        if recipe.id == "flashscore-odds":
            payload["_api_recipe_fallback"] = True
    elif run.status in {"ok", "partial"} and live_used:
        payload["_api_recipe_live_network"] = True
    return payload
