"""Load, match, validate, and execute optional API recipes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ..social.backend import redact_secrets
from ..social.domains import host_matches_root, is_safe_public_http_url, url_has_userinfo
from .consent import live_network_permitted, network_gate_message
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

_FORMATTER = Formatter()
_BUILTIN_TEMPLATE_VARS = frozenset({"url", "host", "path"})


def _recipes_dir() -> Path:
    return Path(__file__).resolve().parent / "recipes"


def template_fields(template: str) -> frozenset[str]:
    """Return placeholder names referenced by a url_template."""
    names: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in _FORMATTER.parse(template):
        if field_name is None:
            continue
        root = field_name.split(".")[0].split("[")[0]
        if root:
            names.add(root)
    return frozenset(names)


def declared_template_vars(recipe: ApiRecipe) -> frozenset[str]:
    return template_fields(recipe.endpoint.url_template) - _BUILTIN_TEMPLATE_VARS


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


def _query_value(url: str, keys: tuple[str, ...] | list[str]) -> str | None:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query or "")
    for key in keys:
        values = qs.get(key) or []
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    return None


def _fanout_config(recipe: ApiRecipe) -> dict[str, Any]:
    params = recipe.params if isinstance(recipe.params, dict) else {}
    fanout = params.get("fanout")
    return fanout if isinstance(fanout, dict) else {}


def _fanout_var_names(recipe: ApiRecipe) -> frozenset[str]:
    fanout = _fanout_config(recipe)
    names: set[str] = set()
    for key in ("template_var", "name_var"):
        value = fanout.get(key)
        if value is not None and str(value).strip():
            names.add(str(value).strip())
    return frozenset(names)


def resolve_bindings(url: str, recipe: ApiRecipe) -> dict[str, str] | None:
    """Resolve declarative URL→template bindings from recipe.params.bindings only.

    Supported binding shapes (per template variable name):
      {"query": "mid"}
      {"query": ["mid", "eventId"]}
      {"query": [...], "pattern": "^[A-Za-z0-9]{4,32}$"}
    """
    bindings_raw = recipe.params.get("bindings") if isinstance(recipe.params, dict) else None
    if not isinstance(bindings_raw, dict) or not bindings_raw:
        return {}

    resolved: dict[str, str] = {}
    for name, spec in bindings_raw.items():
        if not isinstance(spec, dict):
            return None
        query_spec = spec.get("query")
        if isinstance(query_spec, str):
            keys: list[str] = [query_spec]
        elif isinstance(query_spec, list):
            keys = [str(k) for k in query_spec]
        else:
            return None
        value = _query_value(url, keys)
        if value is None:
            return None
        pattern = str(spec.get("pattern") or "").strip()
        if pattern and re.fullmatch(pattern, value) is None:
            return None
        resolved[str(name)] = value
    return resolved


def _fanout_items(recipe: ApiRecipe) -> list[dict[str, Any]]:
    """Return bounded fanout rows declared by params.fanout."""
    fanout = _fanout_config(recipe)
    if not fanout:
        return []

    params = recipe.params if isinstance(recipe.params, dict) else {}
    raw_items = fanout.get("items")
    if raw_items is None:
        items_from = fanout.get("items_from")
        if items_from is None:
            return []
        source = params.get(str(items_from))
        raw_items = source if isinstance(source, list) else []
    if not isinstance(raw_items, list):
        return []

    template_var = str(fanout["template_var"]).strip() if fanout.get("template_var") is not None else ""
    name_var = str(fanout["name_var"]).strip() if fanout.get("name_var") is not None else ""
    item_id_key = str(fanout.get("item_id_key") or "id")
    item_name_key = str(fanout.get("item_name_key") or "name")

    out: list[dict[str, Any]] = []
    for item in raw_items:
        row: dict[str, Any] = {}
        if isinstance(item, dict):
            for key, value in item.items():
                row[str(key)] = value
            if template_var and item_id_key in item:
                row[template_var] = item[item_id_key]
            if name_var and item_name_key in item:
                row[name_var] = item[item_name_key]
        elif isinstance(item, (str, int, float, bool)):
            if template_var:
                row[template_var] = item
            else:
                row["value"] = item
        else:
            continue
        if row:
            out.append(row)
    return out[: recipe.endpoint.max_fanout]


def _binding_var_names(recipe: ApiRecipe) -> frozenset[str]:
    params = recipe.params if isinstance(recipe.params, dict) else {}
    bindings = params.get("bindings")
    if not isinstance(bindings, dict):
        return frozenset()
    return frozenset(str(name) for name in bindings)


def _required_binding_vars(recipe: ApiRecipe) -> frozenset[str]:
    return declared_template_vars(recipe) - _fanout_var_names(recipe) - _BUILTIN_TEMPLATE_VARS


def recipe_matches_url(recipe: ApiRecipe, url: str) -> bool:
    if not is_safe_public_http_url(url) or url_has_userinfo(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname
    if not any(host_matches_root(host, root) for root in recipe.match.host_roots):
        return False
    path = parsed.path or ""
    query = (parsed.query or "").lower()
    path_ok = True
    if recipe.match.path_regex:
        path_ok = (
            re.search(recipe.match.path_regex, path, re.I) is not None
            or recipe.match.path_regex.lower() in path.lower()
        )
    query_ok = True
    if recipe.match.query_keys:
        query_ok = any(f"{key.lower()}=" in query for key in recipe.match.query_keys)
    if recipe.match.path_regex and recipe.match.query_keys:
        if not (path_ok or query_ok):
            return False
    elif recipe.match.path_regex and not path_ok:
        return False
    elif recipe.match.query_keys and not query_ok:
        return False

    required_bindings = _required_binding_vars(recipe)
    if required_bindings:
        resolved = resolve_bindings(url, recipe)
        if resolved is None or not required_bindings.issubset(resolved.keys()):
            return False
    return True


def find_matching_recipes(url: str, recipes: list[ApiRecipe] | None = None) -> list[ApiRecipe]:
    catalog = recipes if recipes is not None else load_recipes()
    return [recipe for recipe in catalog if recipe_matches_url(recipe, url)]


def _can_use_live_fetcher(recipe: ApiRecipe, fetcher: Fetcher | None) -> bool:
    """Injected fetchers are offline; live safe_get requires consent/mode."""
    if fetcher is not None:
        return True
    return live_network_permitted(
        recipe.id,
        network_mode=recipe.network.mode,
        consent_phrase=recipe.network.consent_phrase,
    )


def _format_endpoint(template: str, variables: dict[str, Any]) -> str:
    try:
        return template.format(**variables)
    except (KeyError, ValueError) as exc:
        raise ApiRecipeSecurityError(f"url_template substitution failed: {exc}") from exc


def _unresolved_template_vars(recipe: ApiRecipe, variables: dict[str, Any]) -> list[str]:
    missing = sorted(name for name in declared_template_vars(recipe) if name not in variables)
    return missing


def run_generic_get_recipe(
    url: str,
    recipe: ApiRecipe,
    *,
    length: int = 900,
    include_content: bool = False,
    fetcher: Fetcher | None = None,
    resolve_dns: bool = True,
) -> RecipeRunResult:
    """HTTPS GET recipe with optional URL bindings and bounded fanout."""
    warnings = [redact_secrets(w) for w in recipe.warnings]
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _blocked(extra: str, structured: dict[str, Any] | None = None) -> RecipeRunResult:
        return RecipeRunResult(
            status="error",
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=None,
            summary="",
            structured_data=structured or {"network_blocked": True},
            warnings=warnings + [extra],
            confidence=0.0,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )

    if not _can_use_live_fetcher(recipe, fetcher):
        return _blocked(network_gate_message(recipe))

    bindings = resolve_bindings(url, recipe)
    if bindings is None:
        return _blocked("recipe bindings could not be resolved from URL")

    required_bindings = _required_binding_vars(recipe)
    if required_bindings and not required_bindings.issubset((bindings or {}).keys()):
        return _blocked("recipe bindings could not be resolved from URL")

    parsed = urlparse(url)
    base_vars: dict[str, Any] = {
        "url": url,
        "host": parsed.hostname or "",
        "path": parsed.path or "",
    }
    if bindings:
        base_vars.update(bindings)

    fanout_rows = _fanout_items(recipe)
    fanout_vars = _fanout_var_names(recipe)
    needs_fanout = bool(fanout_rows) and bool(fanout_vars & declared_template_vars(recipe))

    fetch = fetcher or safe_get
    limiter = RateLimiter(recipe.endpoint.min_interval_ms)

    if needs_fanout:
        items: list[dict[str, Any]] = []
        rate_limited = False
        forbidden = False
        for row in fanout_rows:
            limiter.wait()
            variables = {**base_vars, **row}
            missing = _unresolved_template_vars(recipe, variables)
            if missing:
                message = f"unresolved template placeholders: {', '.join(missing)}"
                warnings.append(f"fanout item skipped: {message}")
                items.append({"vars": row, "payload": None, "ok": False, "error": message})
                continue
            try:
                endpoint_url = _format_endpoint(recipe.endpoint.url_template, variables)
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
                preserved = {k: row[k] for k in row if k in fanout_vars or k in _binding_var_names(recipe)}
                items.append(
                    {
                        "vars": preserved,
                        "endpoint_url": endpoint_url,
                        "payload": body,
                        "ok": True,
                    }
                )
            except ApiRecipeSecurityError as exc:
                message = redact_secrets(str(exc))
                item_label = next((row.get(name) for name in fanout_vars if name in row), row)
                if "429" in message:
                    rate_limited = True
                    warnings.append(f"fanout item {item_label}: {message}")
                    break
                if "401" in message or "403" in message:
                    forbidden = True
                warnings.append(f"fanout item {item_label}: {message}")
                items.append({"vars": row, "payload": None, "ok": False, "error": message})
            except Exception as exc:  # noqa: BLE001 — keep recipe resilient
                message = redact_secrets(type(exc).__name__)
                item_label = next((row.get(name) for name in fanout_vars if name in row), row)
                warnings.append(f"fanout item {item_label}: {message}")
                items.append({"vars": row, "payload": None, "ok": False, "error": message})

        ok_items = [item for item in items if item.get("ok")]
        structured = {
            "kind": "api_recipe_fanout",
            "schema": recipe.schema or "generic_fanout_v1",
            "source_url": url,
            "bindings": bindings or {},
            "items": items,
            "captured_at": captured_at,
            "warnings": warnings,
            "provenance": "Opt-in API recipe HTTPS GET fanout (read-only). Undocumented endpoints may change.",
        }
        if ok_items:
            status = "ok"
            confidence = recipe.confidence
            summary = f"API recipe {recipe.recipe_key} returned {len(ok_items)} fanout item(s)."
        elif rate_limited or forbidden:
            status = "error"
            confidence = 0.0
            summary = ""
        else:
            status = "partial"
            confidence = max(0.0, recipe.confidence * 0.25)
            summary = f"API recipe {recipe.recipe_key} fanout produced no successful items."
            warnings.append("fanout produced no successful items; consider HTTP/SEO/Cloak/archive fallback")
        _ = include_content
        return RecipeRunResult(
            status=status,
            url=url,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            fetch_method="api-recipe",
            title=recipe.title if ok_items else None,
            summary=summary,
            structured_data=structured if ok_items or status != "error" else {"bindings": bindings or {}, "items": []},
            warnings=warnings,
            confidence=confidence,
            captured_at=captured_at,
            ttl_seconds=recipe.ttl_seconds,
            length=length,
        )

    missing = _unresolved_template_vars(recipe, base_vars)
    if missing:
        return _blocked(
            f"unresolved template placeholders: {', '.join(missing)}",
            structured={"unresolved_placeholders": missing},
        )

    # Single GET
    try:
        endpoint_url = _format_endpoint(recipe.endpoint.url_template, base_vars)
    except ApiRecipeSecurityError as exc:
        return _blocked(redact_secrets(str(exc)))

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
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"raw_text": text[:4000]}
        structured = {
            "kind": "api_recipe_generic",
            "schema": recipe.schema or "generic_json_v1",
            "source_url": url,
            "endpoint_url": endpoint_url,
            "bindings": bindings or {},
            "captured_at": captured_at,
            "payload": body,
            "warnings": warnings,
            "provenance": "Opt-in API recipe HTTPS GET (read-only). Undocumented endpoints may change.",
        }
        _ = include_content
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
    elif run.status == "partial":
        items = (run.structured_data or {}).get("items")
        if isinstance(items, list) and not any(isinstance(i, dict) and i.get("ok") for i in items):
            payload["_api_recipe_fallback"] = True
        elif live_used:
            payload["_api_recipe_live_network"] = True
    elif run.status == "ok" and live_used:
        payload["_api_recipe_live_network"] = True
    return payload
