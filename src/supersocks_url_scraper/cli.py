from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .reader import read_url, to_markdown

from .api_recipes.discovery import (
    DEFAULT_MAX_ENTRY_BYTES,
    DEFAULT_MAX_REPORT_CANDIDATES,
)


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _js_runtime_available() -> bool:
    return shutil.which("node") is not None or shutil.which("deno") is not None


def _path_status(raw_path: str, *, kind: str) -> dict:
    configured = bool(raw_path.strip())
    if not configured:
        return {"configured": False, "path": "", "exists": False, "writable": False}
    path = Path(raw_path).expanduser()
    check_path = path if kind == "dir" else path.parent
    return {
        "configured": True,
        "path": str(path),
        "exists": path.exists(),
        "writable": check_path.exists() and os.access(check_path, os.W_OK),
    }


def health_payload() -> dict:
    browser_profile_dir = os.environ.get("BROWSER_PROFILE_DIR", "")
    social_profile_dir = os.environ.get("SOCIAL_BROWSER_PROFILE_DIR", "") or browser_profile_dir
    strategy_cache_path = os.environ.get("FETCH_STRATEGY_CACHE_PATH", "")
    from .browser_fetcher import resolve_headless
    from .social import SOCIAL_PLATFORMS
    from .social.cloak_social import cloakbrowser_available, opencli_fallback_enabled
    from .social.opencli import probe_opencli
    from .social.reddit_rdt import rdt_cli_available, rdt_cli_fallback_enabled
    from .social.twitter_x import explicit_twitter_credentials_present, twitter_cli_available

    opencli = probe_opencli(timeout=3)
    return {
        "status": "ok",
        "version": "0.2.0",
        "service": "supersocks-url-scraper",
        "auth_required": bool(os.environ.get("API_BEARER_TOKEN", "").strip()),
        "browser": {
            "extra_installed": importlib.util.find_spec("cloakbrowser") is not None,
            "fallback_default": _truthy(os.environ.get("BROWSER_FALLBACK"), False),
            "profile_dir": _path_status(browser_profile_dir, kind="dir"),
            "post_load_wait_ms": _env_int("BROWSER_POST_LOAD_WAIT_MS", 8000),
            "max_concurrency": max(1, _env_int("BROWSER_MAX_CONCURRENCY", 1)),
            "headless_default": resolve_headless(None),
        },
        "fallbacks": {
            "seo_default": _truthy(os.environ.get("SEO_FALLBACK"), True),
            "archive_default": _truthy(os.environ.get("ARCHIVE_FALLBACK"), True),
            "jina_default": _truthy(os.environ.get("JINA_FALLBACK"), False),
        },
        "api_recipes": {
            "enabled_default": _truthy(os.environ.get("API_RECIPES"), False),
            "builtin": ["flashscore-odds@v1"],
            "methods": ["GET"],
            "flashscore_network_mode": "fixture_only",
            "route_advice": True,
            "recurrent_need_default": False,
            "notes": (
                "Opt-in structured HTTPS GET recipes with host allowlists; degrade to "
                "HTTP→SEO→Cloak→archive. Flashscore odds ships fixture-only (ToS). "
                "Live GETs require network.mode + API_RECIPE_LIVE_ALLOWLIST + API_RECIPE_LIVE_CONSENT. "
                "Never stores cookies/tokens; StrategyCache stays http/seo/cloak/archive only. "
                "Optional route_advice (offline) steers agents toward known recipes or manual HAR "
                "+ --discover-har when recurrent_need is set; never auto-sniffs or activates."
            ),
        },
        "social": {
            "youtube_extra_installed": importlib.util.find_spec("yt_dlp") is not None,
            "js_runtime_available": _js_runtime_available(),
            "platforms": list(SOCIAL_PLATFORMS),
            "jina_fallback_default": _truthy(os.environ.get("JINA_FALLBACK"), False),
            "twitter_cli_available": twitter_cli_available(),
            "twitter_explicit_credentials": explicit_twitter_credentials_present(),
            "cloak_first_platforms": ["reddit", "instagram", "facebook"],
            "cloakbrowser_available": cloakbrowser_available(),
            "social_profile_dir": _path_status(social_profile_dir, kind="dir"),
            "opencli_fallback_default": opencli_fallback_enabled(),
            "opencli_available": opencli.installed and not opencli.broken,
            "opencli_extension_connected": opencli.extension_connected,
            "rdt_cli_fallback_default": rdt_cli_fallback_enabled(),
            "rdt_cli_available": rdt_cli_available(),
        },
        "strategy_cache": _path_status(strategy_cache_path, kind="file"),
        "summary_provider": {
            "default": os.environ.get("SUMMARY_PROVIDER", "local") or "local",
            "url_configured": bool(os.environ.get("SUMMARY_PROVIDER_URL", "").strip()),
            "token_configured": bool(os.environ.get("SUMMARY_PROVIDER_TOKEN", "").strip()),
            "timeout_seconds": _env_int("SUMMARY_PROVIDER_TIMEOUT", 30),
        },
    }


def openapi_payload() -> dict:
    request_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "length": {"type": "integer", "default": _env_int("DEFAULT_SUMMARY_LENGTH", 900)},
            "include_content": {"type": "boolean", "default": False},
            "seo_fallback": {"type": "boolean", "default": _truthy(os.environ.get("SEO_FALLBACK"), True)},
            "browser_fallback": {"type": "boolean", "default": _truthy(os.environ.get("BROWSER_FALLBACK"), False)},
            "browser_profile_dir": {"type": "string"},
            "browser_post_load_wait_ms": {"type": "integer", "default": _env_int("BROWSER_POST_LOAD_WAIT_MS", 8000)},
            "browser_max_concurrency": {"type": "integer", "default": max(1, _env_int("BROWSER_MAX_CONCURRENCY", 1))},
            "archive_fallback": {"type": "boolean", "default": _truthy(os.environ.get("ARCHIVE_FALLBACK"), True)},
            "jina_fallback": {"type": "boolean", "default": _truthy(os.environ.get("JINA_FALLBACK"), False), "description": "Opt-in LinkedIn/public external reader fallback via Jina Reader. Disabled by default. Never used for credentialed, local, or private URLs."},
            "api_recipes": {
                "type": "boolean",
                "default": _truthy(os.environ.get("API_RECIPES"), False),
                "description": (
                    "Opt-in structured API recipes (HTTPS GET only, host-allowlisted). "
                    "Disabled by default. On failure, degrades to HTTP→SEO→Cloak→archive. "
                    "Never sends Authorization/Cookie headers. Shipped flashscore-odds is "
                    "fixture-only and will not perform live Flashscore GETs without an "
                    "explicit network-mode change plus allowlist/consent gates."
                ),
            },
            "api_recipe_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional extra recipe JSON files or directories (versioned, schema-validated).",
            },
            "recurrent_need": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true and no suitable recipe matches a HTML-like URL, attach discrete "
                    "route_advice recommending manual HAR capture then offline --discover-har. "
                    "Never auto-sniffs, never activates recipes. Ignored for PDF/image/social."
                ),
            },
            "strategy_cache_path": {"type": "string"},
            "summary_provider": {
                "type": "string",
                "enum": ["local", "extractive", "none", "http"],
                "default": os.environ.get("SUMMARY_PROVIDER", "local") or "local",
            },
            "summary_provider_url": {"type": "string", "format": "uri"},
            "summary_provider_token": {
                "type": "string",
                "description": "Optional bearer token for the caller's own summary provider; never required by default.",
            },
            "summary_provider_timeout": {"type": "integer", "default": _env_int("SUMMARY_PROVIDER_TIMEOUT", 30)},
        },
    }
    result_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ok", "partial", "error"]},
            "url": {"type": "string"},
            "content_type": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "length": {"type": "integer"},
            "fetch_method": {"type": "string", "enum": ["http", "seo", "cloak", "cloak-profile", "archive", "fallback", "yt-dlp", "jina", "twitter-cli", "opencli", "rdt-cli", "api-recipe"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "content": {"type": "string"},
            "image_url": {"type": "string"},
            "platform": {"type": "string", "enum": ["youtube", "linkedin", "x", "instagram", "facebook", "reddit"], "description": "Optional social platform tag when social routing matched."},
            "author": {"type": "string", "description": "Optional author/channel when available from social extractors."},
            "published_at": {"type": "string", "description": "Optional publish/upload date when available."},
            "duration": {"type": "integer", "description": "Optional media duration in seconds when available."},
            "transcript": {"type": "string", "description": "Optional subtitle/auto-caption text when available."},
            "transcript_source": {"type": "string", "description": "Origin of transcript text, e.g. manual or auto-captions."},
            "linkedin_page_type": {
                "type": "string",
                "enum": ["profile", "company", "school", "showcase", "job", "article", "post", "unknown"],
                "description": "LinkedIn public page classification when LinkedIn specialized extraction ran.",
            },
            "structured_data": {
                "type": "object",
                "description": "Optional sanitized structured payload (LinkedIn JSON-LD subset, or API recipe data such as Flashscore 1X2 odds).",
            },
            "api_recipe": {
                "type": "object",
                "description": "Present when an opt-in API recipe produced the result (id, version, confidence, ttl, captured_at).",
            },
            "route_advice": {
                "type": "object",
                "description": (
                    "Optional machine-readable agent guidance (absent when no useful advice). "
                    "Stable fields: recommended (api_recipe|review_recipe|api_discovery|standard_pipeline), "
                    "state (available_disabled|fixture_only|review_required|used|blocked|suggested), "
                    "reason, recipe?, requires?, next_command?, network_attempted. Computed offline. "
                    "recipe.review_required is an explicit boolean (true for candidate / review_required / disabled)."
                ),
                "properties": {
                    "recommended": {
                        "type": "string",
                        "enum": ["api_recipe", "review_recipe", "api_discovery", "standard_pipeline"],
                    },
                    "state": {
                        "type": "string",
                        "enum": [
                            "available_disabled",
                            "fixture_only",
                            "review_required",
                            "used",
                            "blocked",
                            "suggested",
                        ],
                    },
                    "reason": {"type": "string"},
                    "recipe": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "version": {"type": "string"},
                            "network_mode": {"type": "string"},
                            "status": {"type": "string"},
                            "review_required": {
                                "type": "boolean",
                                "description": "True when the recipe must be reviewed before any execution (candidate / review_required / disabled).",
                            },
                        },
                    },
                    "requires": {"type": "array", "items": {"type": "string"}},
                    "next_command": {"type": "string"},
                    "network_attempted": {"type": "boolean"},
                },
            },
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "supersocks-url-scraper", "version": "0.2.0"},
        "paths": {
            "/health": {"get": {"responses": {"200": {"description": "Runtime health/config metadata"}}}},
            "/summarize": {"post": {"requestBody": {"content": {"application/json": {"schema": request_schema}}}, "responses": {"200": {"description": "URL read result", "content": {"application/json": {"schema": result_schema}}}}}},
            "/read": {"post": {"requestBody": {"content": {"application/json": {"schema": request_schema}}}, "responses": {"200": {"description": "Alias of /summarize", "content": {"application/json": {"schema": result_schema}}}}}},
            "/markdown": {"post": {"requestBody": {"content": {"application/json": {"schema": request_schema}}}, "responses": {"200": {"description": "Markdown rendering", "content": {"text/markdown": {"schema": {"type": "string"}}}}}}},
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "supersocks-url-scraper/0.2"

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code: int, payload: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(200, health_payload())
            return
        if path == "/openapi.json":
            self._json(200, openapi_payload())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _read_payload(self) -> dict:
        size = min(int(self.headers.get("content-length", "0") or 0), 65536)
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _authorized(self) -> bool:
        token = os.environ.get("API_BEARER_TOKEN", "").strip()
        if not token:
            return True
        return self.headers.get("authorization", "") == f"Bearer {token}"

    def _summarize(self) -> dict:
        payload = self._read_payload()
        browser_fallback = _truthy(payload.get("browser_fallback"), _truthy(os.environ.get("BROWSER_FALLBACK"), False))
        archive_fallback = _truthy(payload.get("archive_fallback"), _truthy(os.environ.get("ARCHIVE_FALLBACK"), True))
        jina_fallback = _truthy(payload.get("jina_fallback"), _truthy(os.environ.get("JINA_FALLBACK"), False))
        api_recipes = _truthy(payload.get("api_recipes"), _truthy(os.environ.get("API_RECIPES"), False))
        recipe_paths_raw = payload.get("api_recipe_paths")
        if isinstance(recipe_paths_raw, str) and recipe_paths_raw.strip():
            api_recipe_paths = [recipe_paths_raw.strip()]
        elif isinstance(recipe_paths_raw, list):
            api_recipe_paths = [str(p) for p in recipe_paths_raw if str(p).strip()]
        else:
            env_paths = os.environ.get("API_RECIPE_PATHS", "").strip()
            api_recipe_paths = [p.strip() for p in env_paths.split(":") if p.strip()] if env_paths else None
        summary_provider = str(payload.get("summary_provider") or os.environ.get("SUMMARY_PROVIDER") or "local")
        summary_provider_url = str(payload.get("summary_provider_url") or os.environ.get("SUMMARY_PROVIDER_URL") or "")
        summary_provider_token = str(payload.get("summary_provider_token") or os.environ.get("SUMMARY_PROVIDER_TOKEN") or "")
        return read_url(
            str(payload.get("url") or ""),
            length=int(payload.get("length") or os.environ.get("DEFAULT_SUMMARY_LENGTH", 900)),
            include_content=bool(payload.get("include_content")),
            seo_fallback=_truthy(payload.get("seo_fallback"), _truthy(os.environ.get("SEO_FALLBACK"), True)),
            strategy_cache_path=payload.get("strategy_cache_path") or os.environ.get("FETCH_STRATEGY_CACHE_PATH") or None,
            browser_fallback=browser_fallback,
            browser_profile_dir=str(payload.get("browser_profile_dir") or os.environ.get("BROWSER_PROFILE_DIR") or ""),
            browser_post_load_wait_ms=int(payload.get("browser_post_load_wait_ms") or _env_int("BROWSER_POST_LOAD_WAIT_MS", 8000)),
            browser_max_concurrency=int(payload.get("browser_max_concurrency") or _env_int("BROWSER_MAX_CONCURRENCY", 1)),
            archive_fallback=archive_fallback,
            jina_fallback=jina_fallback,
            api_recipes=api_recipes,
            api_recipe_paths=api_recipe_paths,
            recurrent_need=_truthy(payload.get("recurrent_need"), False),
            summary_provider=summary_provider,
            summary_provider_url=summary_provider_url,
            summary_provider_token=summary_provider_token,
            summary_provider_timeout=int(payload.get("summary_provider_timeout") or _env_int("SUMMARY_PROVIDER_TIMEOUT", 30)),
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/summarize", "/read", "/markdown"}:
            self._json(404, {"status": "error", "warnings": ["not found"]})
            return
        if not self._authorized():
            self._json(401, {"status": "error", "warnings": ["unauthorized"]})
            return
        try:
            result = self._summarize()
        except Exception:
            self._json(400, {"status": "error", "warnings": ["invalid JSON or request"]})
            return
        code = 200 if result.get("status") in {"ok", "partial"} else 502
        if path == "/markdown":
            self._text(code, to_markdown(result), "text/markdown; charset=utf-8")
            return
        self._json(code, result)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_discover_har(args: argparse.Namespace) -> int:
    """Offline HAR discovery: classify exchanges and emit a disabled candidate recipe."""
    import sys

    from .api_recipes.discovery import (
        discover_from_har,
        render_report_json,
        render_report_markdown,
        write_report,
    )

    input_path = args.discover_har
    if not input_path:
        raise SystemExit("--discover-har requires a path to a local .har file")
    report = discover_from_har(
        input_path,
        max_bytes=args.discovery_max_bytes,
        max_candidates=args.discovery_max_candidates,
    )
    if args.discovery_out_dir:
        written = write_report(report, out_dir=args.discovery_out_dir, prefix=args.discovery_prefix)
        for kind in ("json", "markdown", "recipe"):
            if kind in written:
                print(f"{kind}: {written[kind]}")
        return 0
    if args.markdown:
        sys.stdout.write(render_report_markdown(report))
    else:
        sys.stdout.write(render_report_json(report) + "\n")
    return 0


def run_validate_recipe(args: argparse.Namespace) -> int:
    """Offline recipe validation: check a recipe file against the v1 schema + runtime rules."""
    import sys

    from .api_recipes.engine import validate_recipe_dict
    from .api_recipes.schema import validate_recipe_schema

    paths = args.validate_recipe or []
    if not paths:
        raise SystemExit("--validate-recipe requires at least one recipe JSON path")
    all_ok = True
    for raw_path in paths:
        import json
        from pathlib import Path

        path = Path(raw_path).expanduser()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"{raw_path}: VALIDATION FAILED (unreadable): {exc}")
            all_ok = False
            continue
        schema_errors = validate_recipe_schema(raw)
        runtime_errors = validate_recipe_dict(raw)
        errors = schema_errors + [e for e in runtime_errors if e not in schema_errors]
        if errors:
            all_ok = False
            print(f"{raw_path}: VALIDATION FAILED")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"{raw_path}: OK")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and summarize web URLs without JavaScript execution.")
    parser.add_argument("url", nargs="?", help="Fetch one URL and print JSON")
    parser.add_argument("--length", type=int, default=900, help="Maximum summary length for one-shot mode")
    parser.add_argument("--include-content", action="store_true", help="Include extracted page content in one-shot mode")
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON in one-shot mode")
    parser.add_argument("--no-seo-fallback", action="store_true", help="Disable SEO-style HTTP fallback variants")
    parser.add_argument("--strategy-cache", default="", help="Optional JSON file storing successful per-domain fetch strategy metadata")
    parser.add_argument("--browser-fallback", action="store_true", help="Enable optional CloakBrowser fallback after HTTP/SEO failures")
    parser.add_argument("--browser-profile-dir", default="", help="Optional persistent CloakBrowser profile directory for logged-in/paywalled sites")
    parser.add_argument("--browser-post-load-wait-ms", type=int, default=8000, help="Extra wait after DOMContentLoaded for browser fallback")
    parser.add_argument("--browser-max-concurrency", type=int, default=1, help="Maximum concurrent CloakBrowser renders in this process")
    parser.add_argument("--no-archive-fallback", action="store_true", help="Disable public archive/cache fallback after HTTP/SEO/browser failures or paywall teasers")
    parser.add_argument("--jina-fallback", action="store_true", help="Opt-in Jina Reader fallback for LinkedIn/public pages after the generic pipeline returns error/partial (disabled by default)")
    parser.add_argument(
        "--api-recipes",
        action="store_true",
        help=(
            "Opt-in structured API recipes (HTTPS GET only). Disabled by default; "
            "degrades to HTTP→SEO→Cloak→archive on failure. Shipped flashscore-odds "
            "is fixture-only (no live Flashscore GETs by default)"
        ),
    )
    parser.add_argument(
        "--api-recipe-path",
        action="append",
        default=[],
        help="Optional extra recipe JSON file or directory (repeatable)",
    )
    parser.add_argument(
        "--recurrent",
        action="store_true",
        help=(
            "Flag a recurrent agent need: when no recipe matches a suitable HTML URL, "
            "attach route_advice suggesting manual HAR capture then offline --discover-har "
            "(never auto-sniffs or activates). No effect for PDF/image/social."
        ),
    )
    parser.add_argument(
        "--summary-provider",
        default=os.environ.get("SUMMARY_PROVIDER", "local") or "local",
        choices=["local", "extractive", "none", "http"],
        help="Optional external summary provider after extraction. Default: local extractive summary",
    )
    parser.add_argument("--summary-provider-url", default="", help="HTTP endpoint for --summary-provider=http")
    parser.add_argument("--summary-provider-token", default="", help="Optional bearer token for --summary-provider=http")
    parser.add_argument("--summary-provider-timeout", type=int, default=_env_int("SUMMARY_PROVIDER_TIMEOUT", 30), help="Timeout in seconds for the optional summary provider")
    parser.add_argument("--serve", action="store_true", help="Run HTTP service with /health, /summarize, /read, /markdown")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument(
        "--discover-har",
        default="",
        help="Offline API discovery from a local .har file (no network). Emits a classified report and a disabled review_required candidate recipe.",
    )
    parser.add_argument("--discovery-out-dir", default="", help="When set, write JSON/Markdown report + candidate recipe to this directory")
    parser.add_argument("--discovery-prefix", default="discovery", help="Output filename prefix for --discovery-out-dir")
    parser.add_argument("--discovery-max-bytes", type=int, default=DEFAULT_MAX_ENTRY_BYTES, help="Max response body bytes to keep as a candidate")
    parser.add_argument("--discovery-max-candidates", type=int, default=DEFAULT_MAX_REPORT_CANDIDATES, help="Max candidate entries in the report")
    parser.add_argument(
        "--validate-recipe",
        action="append",
        default=[],
        help="Validate a recipe JSON file against the v1 schema and runtime rules (repeatable, offline)",
    )
    args = parser.parse_args()

    if args.discover_har:
        return run_discover_har(args)
    if args.validate_recipe:
        return run_validate_recipe(args)

    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"supersocks-url-scraper listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
        return 0

    if not args.url:
        parser.error("provide a URL or use --serve")
    env_recipe_paths = [p.strip() for p in os.environ.get("API_RECIPE_PATHS", "").split(":") if p.strip()]
    result = read_url(
        args.url,
        length=args.length,
        include_content=args.include_content,
        seo_fallback=not args.no_seo_fallback,
        strategy_cache_path=args.strategy_cache or None,
        browser_fallback=args.browser_fallback,
        browser_profile_dir=args.browser_profile_dir,
        browser_post_load_wait_ms=args.browser_post_load_wait_ms,
        browser_max_concurrency=args.browser_max_concurrency,
        archive_fallback=not args.no_archive_fallback,
        jina_fallback=args.jina_fallback or _truthy(os.environ.get("JINA_FALLBACK"), False),
        api_recipes=args.api_recipes or _truthy(os.environ.get("API_RECIPES"), False),
        api_recipe_paths=(args.api_recipe_path or env_recipe_paths or None),
        recurrent_need=bool(args.recurrent),
        summary_provider=args.summary_provider,
        summary_provider_url=args.summary_provider_url or os.environ.get("SUMMARY_PROVIDER_URL") or "",
        summary_provider_token=args.summary_provider_token or os.environ.get("SUMMARY_PROVIDER_TOKEN") or "",
        summary_provider_timeout=args.summary_provider_timeout,
    )
    if args.markdown:
        print(to_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
