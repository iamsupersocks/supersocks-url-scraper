from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .reader import read_url


class Handler(BaseHTTPRequestHandler):
    server_version = "supersocks-url-scraper/0.1"

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(200, {"ok": True, "service": "supersocks-url-scraper"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/summarize":
            self._json(404, {"status": "error", "warnings": ["not found"]})
            return
        try:
            size = min(int(self.headers.get("content-length", "0") or 0), 65536)
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except Exception:
            self._json(400, {"status": "error", "warnings": ["invalid JSON"]})
            return

        result = read_url(
            str(payload.get("url") or ""),
            length=int(payload.get("length") or 900),
            include_content=bool(payload.get("include_content")),
        )
        self._json(200 if result.get("status") in {"ok", "partial"} else 502, result)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and summarize web URLs without JavaScript execution.")
    parser.add_argument("url", nargs="?", help="Fetch one URL and print JSON")
    parser.add_argument("--length", type=int, default=900, help="Maximum summary length for one-shot mode")
    parser.add_argument("--include-content", action="store_true", help="Include cleaned page content in one-shot mode")
    parser.add_argument("--serve", action="store_true", help="Run HTTP server with /health and /summarize")
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
    print(json.dumps(read_url(args.url, length=args.length, include_content=args.include_content), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
