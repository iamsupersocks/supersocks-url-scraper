from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from supersocks_url_scraper import cli


@pytest.fixture()
def service(monkeypatch):
    calls: list[dict] = []

    def fake_read_url(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return {"status": "ok", "url": url, "summary": "ok", "fetch_method": "http", "warnings": []}

    monkeypatch.setattr(cli, "read_url", fake_read_url)
    server = ThreadingHTTPServer(("127.0.0.1", 0), cli.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", calls
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def post_json(base: str, payload: dict, *, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"{base}/summarize", data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def get_json(base: str, path: str):
    with urlopen(f"{base}{path}", timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def test_service_uses_production_style_env_defaults(monkeypatch, service):
    base, calls = service
    monkeypatch.setenv("BROWSER_FALLBACK", "cloak")
    monkeypatch.setenv("ARCHIVE_FALLBACK", "latest")
    monkeypatch.setenv("BROWSER_PROFILE_DIR", "/profiles/lepoint")
    monkeypatch.setenv("BROWSER_POST_LOAD_WAIT_MS", "15000")
    monkeypatch.setenv("BROWSER_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("FETCH_STRATEGY_CACHE_PATH", "/data/fetch-strategies.json")
    monkeypatch.setenv("DEFAULT_SUMMARY_LENGTH", "600")

    status, body = post_json(base, {"url": "https://example.com/article"})

    assert status == 200
    assert body["status"] == "ok"
    assert calls[-1]["browser_fallback"] is True
    assert calls[-1]["archive_fallback"] is True
    assert calls[-1]["browser_profile_dir"] == "/profiles/lepoint"
    assert calls[-1]["browser_post_load_wait_ms"] == 15000
    assert calls[-1]["browser_max_concurrency"] == 2
    assert calls[-1]["strategy_cache_path"] == "/data/fetch-strategies.json"
    assert calls[-1]["length"] == 600


def test_service_optional_bearer_token(monkeypatch, service):
    base, _ = service
    monkeypatch.setenv("API_BEARER_TOKEN", "secret-token")

    with pytest.raises(HTTPError) as exc:
        post_json(base, {"url": "https://example.com/article"})
    assert exc.value.code == 401

    status, body = post_json(base, {"url": "https://example.com/article"}, token="secret-token")
    assert status == 200
    assert body["status"] == "ok"


def test_health_reports_runtime_configuration(monkeypatch, tmp_path, service):
    base, _ = service
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    cache_path = tmp_path / "data" / "fetch-strategies.json"
    cache_path.parent.mkdir()
    monkeypatch.setenv("API_BEARER_TOKEN", "secret-token")
    monkeypatch.setenv("BROWSER_FALLBACK", "1")
    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.setenv("BROWSER_POST_LOAD_WAIT_MS", "12000")
    monkeypatch.setenv("BROWSER_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("ARCHIVE_FALLBACK", "1")
    monkeypatch.setenv("FETCH_STRATEGY_CACHE_PATH", str(cache_path))

    status, body = get_json(base, "/health")

    assert status == 200
    assert body["status"] == "ok"
    assert body["auth_required"] is True
    assert body["browser"]["fallback_default"] is True
    assert body["browser"]["post_load_wait_ms"] == 12000
    assert body["browser"]["max_concurrency"] == 3
    assert body["browser"]["profile_dir"]["configured"] is True
    assert body["browser"]["profile_dir"]["exists"] is True
    assert body["strategy_cache"]["configured"] is True
    assert body["strategy_cache"]["writable"] is True


def test_openapi_schema_exposes_public_contract(service):
    base, _ = service

    status, body = get_json(base, "/openapi.json")

    assert status == 200
    assert body["openapi"] == "3.1.0"
    assert "/summarize" in body["paths"]
    assert "/read" in body["paths"]
    assert "/markdown" in body["paths"]
    summarize_schema = body["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert "browser_max_concurrency" in summarize_schema["properties"]
