from __future__ import annotations

import argparse
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .reader import read_url, to_markdown


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


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
    strategy_cache_path = os.environ.get("FETCH_STRATEGY_CACHE_PATH", "")
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
        },
        "fallbacks": {
            "seo_default": _truthy(os.environ.get("SEO_FALLBACK"), True),
            "archive_default": _truthy(os.environ.get("ARCHIVE_FALLBACK"), True),
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
            "strategy_cache_path": {"type": "string"},
            "summary_provider": {"type": "string", "enum": ["local", "extractive", "none", "http"], "default": os.environ.get("SUMMARY_PROVIDER", "local") or "local"},
            "summary_provider_url": {"type": "string", "format": "uri"},
            "summary_provider_token": {"type": "string", "description": "Optional bearer token for the caller's own summary provider; never required by default."},
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
            "fetch_method": {"type": "string", "enum": ["http", "seo", "cloak", "cloak-profile", "archive", "fallback"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "content": {"type": "string"},
            "image_url": {"type": "string"},
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
            summary_provider=str(payload.get("summary_provider") or os.environ.get("SUMMARY_PROVIDER") or "local"),
            summary_provider_url=str(payload.get("summary_provider_url") or os.environ.get("SUMMARY_PROVIDER_URL") or ""),
            summary_provider_token=str(payload.get("summary_provider_token") or os.environ.get("SUMMARY_PROVIDER_TOKEN") or ""),
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
    parser.add_argument("--summary-provider", default="local", choices=["local", "extractive", "none", "http"], help="Optional external summary provider. Default: local extractive summary")
    parser.add_argument("--summary-provider-url", default="", help="HTTP endpoint for --summary-provider=http")
    parser.add_argument("--summary-provider-token", default="", help="Optional bearer token for --summary-provider=http")
    parser.add_argument("--summary-provider-timeout", type=int, default=30, help="Timeout in seconds for the optional summary provider")
    parser.add_argument("--serve", action="store_true", help="Run HTTP server with /health, /summarize, /read, /markdown")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()

    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"supersocks-url-scraper listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
        return 0

    if not args.url:
        parser.error("provide a URL or use --serve")
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
        summary_provider=args.summary_provider,
        summary_provider_url=args.summary_provider_url,
        summary_provider_token=args.summary_provider_token,
        summary_provider_timeout=args.summary_provider_timeout,
    )
    if args.markdown:
        print(to_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
