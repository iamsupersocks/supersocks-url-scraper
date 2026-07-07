from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .reader import read_url, to_markdown


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
            self._json(200, {"status": "ok", "version": "0.2.0", "service": "supersocks-url-scraper"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _read_payload(self) -> dict:
        size = min(int(self.headers.get("content-length", "0") or 0), 65536)
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _summarize(self) -> dict:
        payload = self._read_payload()
        return read_url(
            str(payload.get("url") or ""),
            length=int(payload.get("length") or 900),
            include_content=bool(payload.get("include_content")),
            seo_fallback=bool(payload.get("seo_fallback", True)),
            strategy_cache_path=payload.get("strategy_cache_path") or None,
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/summarize", "/read", "/markdown"}:
            self._json(404, {"status": "error", "warnings": ["not found"]})
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
    )
    if args.markdown:
        print(to_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
