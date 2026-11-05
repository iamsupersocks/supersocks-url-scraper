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


def post_path(base: str, path: str, payload: dict):
    req = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=5) as response:
        return response.status, response.read().decode()


def test_service_uses_production_style_env_defaults(monkeypatch, service):
    base, calls = service
    monkeypatch.setenv("BROWSER_FALLBACK", "cloak")
    monkeypatch.setenv("ARCHIVE_FALLBACK", "latest")
    monkeypatch.setenv("BROWSER_PROFILE_DIR", "/profiles/publisher")
    monkeypatch.setenv("BROWSER_POST_LOAD_WAIT_MS", "15000")
    monkeypatch.setenv("BROWSER_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("FETCH_STRATEGY_CACHE_PATH", "/data/fetch-strategies.json")
    monkeypatch.setenv("DEFAULT_SUMMARY_LENGTH", "600")

    status, body = post_json(base, {"url": "https://example.com/article"})

    assert status == 200
    assert body["status"] == "ok"
    assert calls[-1]["browser_fallback"] is False
    assert calls[-1]["allow_browser"] is False
    assert calls[-1]["archive_fallback"] is True
    assert calls[-1]["browser_profile_dir"] == "/profiles/publisher"
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
    monkeypatch.setenv("JINA_FALLBACK", "0")
    monkeypatch.setenv("FETCH_STRATEGY_CACHE_PATH", str(cache_path))

    status, body = get_json(base, "/health")

    assert status == 200
    assert body["status"] == "ok"
    assert body["auth_required"] is True
    assert body["browser"]["fallback_default"] is True
    assert body["browser"]["service_browser_enabled"] is False
    assert body["browser"]["post_load_wait_ms"] == 12000
    assert body["browser"]["max_concurrency"] == 3
    assert body["browser"]["profile_dir"]["configured"] is True
    assert body["browser"]["profile_dir"]["exists"] is True
    assert body["strategy_cache"]["configured"] is True
    assert body["strategy_cache"]["writable"] is True
    assert body["fallbacks"]["jina_default"] is False
    assert body["social"]["platforms"] == ["youtube", "linkedin", "x", "instagram", "facebook", "reddit"]
    assert "twitter_cli_available" in body["social"]
    assert "opencli_extension_connected" in body["social"]
    assert body["social"]["cloak_first_platforms"] == ["reddit", "instagram", "facebook"]
    assert "TWITTER_AUTH_TOKEN" not in json.dumps(body)


def test_openapi_schema_exposes_public_contract(service):
    base, _ = service

    status, body = get_json(base, "/openapi.json")

    assert status == 200
    assert body["openapi"] == "3.1.0"
    assert "/summarize" in body["paths"]
    assert "/read" in body["paths"]
    assert "/markdown" in body["paths"]
    summarize_schema = body["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert set(summarize_schema["properties"]) == {
        "url",
        "length",
        "include_content",
        "seo_fallback",
        "browser_fallback",
        "browser_post_load_wait_ms",
        "archive_fallback",
        "jina_fallback",
        "document_max_pages",
    }
    for operator_key in (
        "browser_max_concurrency",
        "browser_profile_dir",
        "strategy_cache_path",
        "summary_provider",
        "summary_provider_url",
        "summary_provider_token",
        "summary_provider_timeout",
    ):
        assert operator_key not in summarize_schema["properties"]
    result_schema = body["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "yt-dlp" in result_schema["properties"]["fetch_method"]["enum"]
    assert "platform" in result_schema["properties"]


def test_service_browser_opt_in_restores_fallback(monkeypatch, service):
    base, calls = service
    monkeypatch.setenv("SERVICE_BROWSER_ENABLED", "1")
    monkeypatch.setenv("BROWSER_FALLBACK", "1")

    status, body = post_json(base, {"url": "https://example.com/article", "browser_fallback": True})

    assert status == 200
    assert calls[-1]["browser_fallback"] is True
    assert calls[-1]["allow_browser"] is True
    assert "browser fallback disabled in service mode" not in body["warnings"]


def test_forbidden_request_keys_are_ignored(monkeypatch, tmp_path, service):
    base, calls = service
    monkeypatch.delenv("FETCH_STRATEGY_CACHE_PATH", raising=False)
    monkeypatch.delenv("BROWSER_PROFILE_DIR", raising=False)
    monkeypatch.delenv("SUMMARY_PROVIDER_URL", raising=False)
    monkeypatch.delenv("SUMMARY_PROVIDER_TOKEN", raising=False)
    monkeypatch.setenv("SUMMARY_PROVIDER", "local")
    outside = tmp_path / "not-data" / "strategies.json"
    long_key = "k" * 80
    payload = {
        "url": "https://example.com/article",
        "strategy_cache_path": str(outside),
        "browser_profile_dir": "/profiles/from-request",
        "browser_max_concurrency": 9,
        "summary_provider": "http",
        "summary_provider_url": "https://summary.example/relay",
        "summary_provider_token": "not-a-real-token",
        "summary_provider_timeout": 5,
        long_key: "hidden-value",
    }

    status, body = post_json(base, payload)

    assert status == 200
    warnings = body["warnings"]
    for key in (
        "strategy_cache_path",
        "browser_profile_dir",
        "browser_max_concurrency",
        "summary_provider",
        "summary_provider_url",
        "summary_provider_token",
        "summary_provider_timeout",
    ):
        assert f"ignored request key: {key}" in warnings
    assert f"ignored request key: {'k' * 64}" in warnings
    rendered = json.dumps(body)
    assert "not-a-real-token" not in rendered
    assert "hidden-value" not in rendered
    assert "summary.example" not in rendered
    assert str(outside) not in rendered
    assert calls[-1]["strategy_cache_path"] is None
    assert calls[-1]["browser_profile_dir"] == ""
    assert calls[-1]["browser_max_concurrency"] == 1
    assert calls[-1]["summary_provider"] == "local"
    assert calls[-1]["summary_provider_url"] == ""
    assert calls[-1]["summary_provider_token"] == ""
    assert not outside.exists()


def test_markdown_includes_ignored_key_warning(monkeypatch, service):
    base, _ = service
    monkeypatch.delenv("FETCH_STRATEGY_CACHE_PATH", raising=False)

    status, body = post_path(base, "/markdown", {"url": "https://example.com/article", "strategy_cache_path": "/data/nope.json"})

    assert status == 200
    assert "ignored request key: strategy_cache_path" in body
    assert "/data/nope.json" not in body


def test_service_clamps_allowlisted_limits(monkeypatch, service):
    base, calls = service
    monkeypatch.setenv("DOCUMENT_MAX_PAGES", "50")
    monkeypatch.setenv("BROWSER_POST_LOAD_WAIT_MS_MAX", "15000")

    status, body = post_json(
        base,
        {
            "url": "https://example.com/article",
            "document_max_pages": 10**6,
            "browser_post_load_wait_ms": 10**9,
        },
    )

    assert status == 200
    assert body["status"] == "ok"
    assert calls[-1]["document_max_pages"] == 50
    assert calls[-1]["browser_post_load_wait_ms"] == 15000

    status, body = post_json(
        base,
        {"url": "https://example.com/article", "document_max_pages": 0, "browser_post_load_wait_ms": -5},
    )
    assert status == 200
    assert calls[-1]["document_max_pages"] == 1
    assert calls[-1]["browser_post_load_wait_ms"] == 0


def test_wattselse_request_body_is_accepted(service):
    base, calls = service
    payload = {
        "url": "https://example.com/article",
        "length": 900,
        "include_content": True,
        "browser_fallback": True,
        "archive_fallback": True,
        "browser_post_load_wait_ms": 8000,
    }

    status, body = post_json(base, payload)

    assert status == 200
    assert body["status"] == "ok"
    assert not any(str(warning).startswith("ignored request key:") for warning in body["warnings"])
    assert "browser fallback disabled in service mode" in body["warnings"]
    assert calls[-1]["url"] == payload["url"]
    assert calls[-1]["length"] == 900
    assert calls[-1]["include_content"] is True
    assert calls[-1]["browser_fallback"] is False
    assert calls[-1]["archive_fallback"] is True
    assert calls[-1]["browser_post_load_wait_ms"] == 8000


def test_invalid_allowlisted_type_is_rejected(service):
    base, _ = service
    with pytest.raises(HTTPError) as exc:
        post_json(base, {"url": "https://example.com/article", "length": "nope"})
    assert exc.value.code == 400


def test_service_refuses_negative_or_oversized_content_length() -> None:
    import io
    from supersocks_url_scraper import cli

    for value in ("-1", str(cli.MAX_REQUEST_BYTES + 1)):
        handler = cli.Handler.__new__(cli.Handler)
        handler.headers = {"content-length": value}
        handler.rfile = io.BytesIO(b"{}")
        try:
            handler._read_payload()
        except ValueError:
            pass
        else:
            raise AssertionError(f"content-length {value} was accepted")
