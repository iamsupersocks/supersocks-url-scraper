"""Tests for offline HAR discovery, JSON Schema v1, CLI tooling, and the
base-HTML-vs-recipe comparison."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from supersocks_url_scraper.api_recipes.discovery import (
    DEFAULT_MAX_ENTRY_BYTES,
    REASON_ERROR_STATUS,
    REASON_NON_JSON,
    REASON_PRIVATE_HOST,
    REASON_SENSITIVE_HEADER,
    REASON_SENSITIVE_PARAM,
    REASON_TOO_LARGE,
    REASON_WRITE_METHOD,
    build_candidate_recipe,
    classify_har_entry,
    discover_from_har,
    infer_source_url_from_har,
    iter_har_entries,
    load_har,
    redact_query,
    render_report_json,
    render_report_markdown,
    write_report,
)
from supersocks_url_scraper.api_recipes.engine import validate_recipe_dict
from supersocks_url_scraper.api_recipes.schema import (
    RECIPE_SCHEMA_V1,
    load_schema,
    schema_file_contents,
    validate_recipe_schema,
)

HAR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "discovery_sample.har"
HTML_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "flashscore_match_page.html"
ODDS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_recipes" / "flashscore_odds_sample.json"
RECIPE_FILE = (
    Path(__file__).resolve().parents[1]
    / "examples" / "recipes" / "flashscore_odds.v1.json"
)
SCHEMA_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supersocks_url_scraper" / "api_recipes" / "schemas" / "recipe.v1.json"
)


def _entry(method: str = "GET", url: str = "https://api.example.com/x", *, status: int = 200, ctype: str = "application/json", body: str | None = "{}", headers: list[dict] | None = None, size: int | None = None) -> dict:
    return {
        "request": {"method": method, "url": url, "headers": headers or [{"name": "Accept", "value": "application/json"}]},
        "response": {
            "status": status,
            "headers": [{"name": "Content-Type", "value": ctype}],
            "content": {"size": size if size is not None else (len(body or "")), "mimeType": ctype, "text": body},
        },
    }


# --- Discovery filtering / classification ---


def test_load_har_accepts_valid_and_rejects_malformed() -> None:
    assert isinstance(load_har(HAR_FIXTURE), dict)
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".har", delete=False) as fh:
        fh.write('{"not-a-log": true}')
        bad = fh.name
    try:
        with pytest.raises(ValueError):
            load_har(bad)
    finally:
        Path(bad).unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".har", delete=False) as fh:
        fh.write("not json at all")
        malformed = fh.name
    try:
        with pytest.raises(ValueError):
            load_har(malformed)
    finally:
        Path(malformed).unlink(missing_ok=True)


def test_classify_keeps_public_https_get_json() -> None:
    entry = _entry(body='{"items": [1, 2]}', ctype="application/json")
    result = classify_har_entry(entry)
    assert result.classification == "candidate"
    assert result.json_keys == ("items",)
    assert result.reason == "candidate: public HTTPS GET JSON"


def test_classify_excludes_writes() -> None:
    assert classify_har_entry(_entry(method="POST")).reason == REASON_WRITE_METHOD
    assert classify_har_entry(_entry(method="PUT")).reason == REASON_WRITE_METHOD


def test_classify_excludes_non_https() -> None:
    assert classify_har_entry(_entry(url="http://api.example.com/x")).reason.startswith("excluded: not https")


def test_classify_excludes_private_host() -> None:
    result = classify_har_entry(_entry(url="https://127.0.0.1/x"))
    assert result.reason == REASON_PRIVATE_HOST
    assert classify_har_entry(_entry(url="https://localhost/x")).reason == REASON_PRIVATE_HOST


def test_classify_excludes_sensitive_query_param() -> None:
    result = classify_har_entry(_entry(url="https://api.example.com/x?token=abc&page=1"))
    assert result.reason == REASON_SENSITIVE_PARAM
    assert "token" in result.sensitive_query_keys
    # redacted URL must not leak the value
    assert "abc" not in result.redacted_url
    assert "[REDACTED]" in result.redacted_url

    mixed_case = classify_har_entry(_entry(url="https://api.example.com/x?Code=oauth-secret"))
    assert mixed_case.reason == REASON_SENSITIVE_PARAM
    assert "oauth-secret" not in mixed_case.redacted_url


def test_classify_excludes_sensitive_header() -> None:
    result = classify_har_entry(
        _entry(headers=[{"name": "Authorization", "value": "Bearer secret123"}])
    )
    assert result.reason == REASON_SENSITIVE_HEADER
    assert "authorization" in result.sensitive_headers


def test_classify_excludes_custom_token_header_and_har_cookie_field() -> None:
    custom = classify_har_entry(
        _entry(headers=[{"name": "X-Client-Secret-Token", "value": "opaque-value"}])
    )
    assert custom.reason == REASON_SENSITIVE_HEADER
    assert "x-client-secret-token" in custom.sensitive_headers

    cookie_entry = _entry()
    cookie_entry["request"]["cookies"] = [{"name": "session_id", "value": "secret"}]
    cookie = classify_har_entry(cookie_entry)
    assert cookie.reason == REASON_SENSITIVE_HEADER
    assert "cookie" in cookie.sensitive_headers


def test_classify_excludes_non_json() -> None:
    result = classify_har_entry(_entry(ctype="text/html", body="<html></html>"))
    assert result.reason == REASON_NON_JSON


def test_classify_excludes_too_large() -> None:
    result = classify_har_entry(_entry(size=DEFAULT_MAX_ENTRY_BYTES + 1))
    assert result.reason == REASON_TOO_LARGE


def test_classify_excludes_error_status() -> None:
    result = classify_har_entry(_entry(status=403))
    assert result.reason == REASON_ERROR_STATUS


def test_redact_query_removes_sensitive_values() -> None:
    url = "https://api.example.com/orders?api_key=ksecret&status=open&sig=abc#access_token=fragment-secret"
    redacted = redact_query(url)
    assert "ksecret" not in redacted
    assert "abc" not in redacted
    assert "[REDACTED]" in redacted
    assert "status=open" in redacted
    assert "fragment-secret" not in redacted
    assert "#" not in redacted

    credentialed = redact_query("https://alice:password@example.com/orders?page=1")
    assert "alice" not in credentialed
    assert "password" not in credentialed
    assert credentialed == "https://example.com/orders?page=1"


# --- Full discovery over the fixture HAR ---


def test_discover_from_har_fixture_classifies() -> None:
    report = discover_from_har(HAR_FIXTURE)
    assert report.total_entries == 10
    counts = report.counts()
    assert counts["candidates"] == 2
    assert counts["excluded"] == 8
    assert report.candidate_recipe is not None
    assert report.candidate_recipe["status"] == "review_required"
    assert report.candidate_recipe["network"]["mode"] == "fixture_only"
    assert report.candidate_recipe["review_required"] is True


def test_discover_excluded_reasons_complete() -> None:
    report = discover_from_har(HAR_FIXTURE)
    reasons = {entry.reason for entry in report.excluded}
    assert REASON_WRITE_METHOD in reasons
    assert "excluded: not https" in reasons
    assert REASON_PRIVATE_HOST in reasons
    assert REASON_SENSITIVE_PARAM in reasons
    assert REASON_SENSITIVE_HEADER in reasons
    assert REASON_NON_JSON in reasons
    assert REASON_TOO_LARGE in reasons
    assert REASON_ERROR_STATUS in reasons


def test_report_renders_json_and_markdown_without_secrets() -> None:
    report = discover_from_har(HAR_FIXTURE)
    json_text = render_report_json(report)
    md_text = render_report_markdown(report)
    assert "s3cr3t" not in json_text
    assert "Bearer abc123" not in json_text
    assert "review_required" in json_text
    assert "review_required" in md_text
    assert "fixture_only" in md_text


def test_write_report_writes_three_files(tmp_path: Path) -> None:
    report = discover_from_har(HAR_FIXTURE)
    written = write_report(report, out_dir=tmp_path, prefix="sample")
    assert "json" in written and "markdown" in written and "recipe" in written
    for path in written.values():
        assert Path(path).exists()
    recipe = json.loads(Path(written["recipe"]).read_text(encoding="utf-8"))
    assert recipe["status"] == "review_required"
    assert recipe["network"]["mode"] == "fixture_only"
    # candidate recipe must be valid against both schema and runtime validator
    assert validate_recipe_schema(recipe) == []
    assert validate_recipe_dict(recipe) == []


def test_candidate_recipe_never_has_auth_fields() -> None:
    report = discover_from_har(HAR_FIXTURE)
    recipe = report.candidate_recipe
    assert recipe is not None
    assert "authorization" not in {str(k).lower() for k in recipe["endpoint"]["headers"]}
    assert "cookie" not in {str(k).lower() for k in recipe["endpoint"]["headers"]}
    assert recipe["endpoint"]["method"] == "GET"


def test_build_candidate_recipe_from_classified() -> None:
    entry = classify_har_entry(_entry(body='{"a":1}'))
    recipe = build_candidate_recipe(entry)
    assert recipe["id"].startswith("har-")
    assert recipe["endpoint"]["url_template"].startswith("https://")
    assert recipe["match"]["host_roots"]
    assert recipe["status"] == "review_required"

    excluded = classify_har_entry(_entry(method="POST"))
    with pytest.raises(ValueError):
        build_candidate_recipe(excluded)


def test_candidate_match_root_is_exact_for_multi_part_tld() -> None:
    entry = classify_har_entry(_entry(url="https://api.example.co.uk/v1/items", body='{"a":1}'))
    recipe = build_candidate_recipe(entry)
    assert recipe["match"]["host_roots"] == ["api.example.co.uk"]
    assert "co.uk" not in recipe["match"]["host_roots"]


def test_iter_har_entries_handles_non_har() -> None:
    assert list(iter_har_entries({"log": {"entries": []}})) == []
    assert list(iter_har_entries({})) == []
    assert list(iter_har_entries({"log": {}})) == []


def test_ranking_prefers_source_linked_family_over_cookie_blob(tmp_path: Path) -> None:
    source_id = "SRC-7788"
    source_url = f"https://www.example.com/app?source_id={source_id}"
    cookie_body = json.dumps({"consent": True, "cookies": [{"name": f"c{i}", "value": "x" * 200} for i in range(40)]})
    har = {
        "log": {
            "version": "1.2",
            "entries": [
                _entry(
                    url="https://cdn.example.com/consent/cookie-banner.json",
                    body=cookie_body,
                    size=len(cookie_body),
                ),
                _entry(
                    url=f"https://api.example.com/v1/catalog?entity={source_id}&page=1",
                    body='{"items":[1]}',
                ),
                _entry(
                    url=f"https://api.example.com/v1/catalog?entity={source_id}&page=2",
                    body='{"items":[2]}',
                ),
            ],
        }
    }
    path = tmp_path / "ranking.har"
    path.write_text(json.dumps(har), encoding="utf-8")
    report = discover_from_har(path, source_url=source_url)
    assert report.candidates
    top = report.candidates[0]
    assert "catalog" in (top.redacted_url or top.url)
    assert top.score > report.candidates[-1].score
    assert any("source_value_match" in reason for reason in top.score_reasons)


def test_infer_source_url_from_har_document() -> None:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://www.example.com/page?source_id=ABC",
                    },
                    "response": {
                        "status": 200,
                        "content": {"mimeType": "text/html", "text": "<html></html>", "size": 13},
                    },
                    "_resourceType": "document",
                }
            ]
        }
    }
    assert infer_source_url_from_har(har) == "https://www.example.com/page?source_id=ABC"


# --- JSON Schema v1 ---


def test_schema_round_trips_and_matches_file() -> None:
    assert load_schema() is RECIPE_SCHEMA_V1
    file_schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert json.loads(schema_file_contents()) == file_schema
    assert file_schema["$id"] == "https://supersocks.local/schemas/recipe.v1.json"
    assert file_schema["properties"]["endpoint"]["properties"]["method"]["enum"] == ["GET"]


def test_validate_recipe_schema_accepts_example_flashscore() -> None:
    raw = json.loads(RECIPE_FILE.read_text(encoding="utf-8"))
    assert validate_recipe_schema(raw) == []
    assert validate_recipe_dict(raw) == []
    assert raw["network"]["mode"] == "open"
    assert "2.ds.lsapp.eu" in raw["endpoint"]["allowed_hosts"]


def test_validate_recipe_schema_rejects_bad_docs() -> None:
    assert validate_recipe_schema({}) != []
    assert validate_recipe_schema({"id": "x", "version": "1"}) != []
    bad_endpoint = {
        "id": "x",
        "version": "1",
        "match": {"host_roots": ["example.com"]},
        "endpoint": {"method": "POST", "url_template": "http://x", "allowed_hosts": ["x.com"]},
    }
    errors = validate_recipe_schema(bad_endpoint)
    assert any("GET" in e for e in errors)
    assert any("https" in e for e in errors)
    bad_headers = {
        "id": "x",
        "version": "1",
        "match": {"host_roots": ["example.com"]},
        "endpoint": {
            "method": "GET",
            "url_template": "https://x.com/y",
            "allowed_hosts": ["x.com"],
            "headers": {"Authorization": "Bearer x"},
        },
    }
    assert any("Authorization" in e for e in validate_recipe_schema(bad_headers))


def test_schema_and_runtime_validators_agree() -> None:
    """schema.py and engine.py must reject the same invalid documents."""
    bad_cases = [
        {"id": "", "version": "1", "match": {"host_roots": ["x"]}, "endpoint": {}},
        {"id": "x", "version": "1", "match": {"host_roots": []}, "endpoint": {"method": "GET", "url_template": "https://x", "allowed_hosts": ["x"]}},
        {"id": "x", "version": "1", "match": {"host_roots": ["x"]}, "endpoint": {"method": "DELETE", "url_template": "https://x", "allowed_hosts": ["x"]}},
        {"id": "x", "version": "1", "match": {"host_roots": ["x"]}, "endpoint": {"method": "GET", "url_template": "https://x", "allowed_hosts": ["x"], "headers": {"Cookie": "a=b"}}},
    ]
    for doc in bad_cases:
        assert validate_recipe_schema(doc) != [], doc
        assert validate_recipe_dict(doc) != [], doc


# --- CLI offline tooling ---


def _run_cli(*args: str):
    src = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "supersocks_url_scraper.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**dict(__import__("os").environ), "PYTHONPATH": src},
    )


def test_cli_discover_har_json() -> None:
    proc = _run_cli("--discover-har", str(HAR_FIXTURE))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["counts"]["candidates"] == 2
    assert payload["candidate_recipe"]["status"] == "review_required"
    assert "s3cr3t" not in proc.stdout


def test_cli_discover_har_out_dir(tmp_path: Path) -> None:
    proc = _run_cli(
        "--discover-har", str(HAR_FIXTURE),
        "--discovery-out-dir", str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    files = list(tmp_path.iterdir())
    assert len(files) >= 3
    names = [f.name for f in files]
    assert any(n.endswith(".json") for n in names)
    assert any(n.endswith(".md") for n in names)
    assert any("candidate-recipe" in n for n in names)


def test_cli_validate_recipe_ok_and_fail() -> None:
    ok = _run_cli("--validate-recipe", str(RECIPE_FILE))
    assert ok.returncode == 0
    assert "OK" in ok.stdout

    bad = tmp = Path("/tmp") / "bad-recipe.json"
    tmp.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    proc = _run_cli("--validate-recipe", str(bad))
    assert proc.returncode == 1
    assert "VALIDATION FAILED" in proc.stdout
    tmp.unlink(missing_ok=True)


def test_cli_validate_recipe_schema_file() -> None:
    proc = _run_cli("--validate-recipe", str(SCHEMA_FILE))
    # schema file is not a recipe document; validation fails (expected)
    assert proc.returncode == 1
    assert "VALIDATION FAILED" in proc.stdout


# --- Comparison example (offline, deterministic) ---


def test_comparison_example_runs_offline() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "flashscore_odds_comparison.py"
    proc = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["offline"] is True
    base = payload["paths"]["base_html_scraper"]
    recipe = payload["paths"]["json_recipe"]
    # base is generic prose, recipe is typed
    assert base["structured_data"] is None
    assert recipe["structured_data"]["kind"] == "flashscore_odds_1x2"
    assert "Betclic" in base["summary"]
    assert recipe["captured_at"] is not None
    assert "not betting advice" in payload["disclaimer"].lower()


def test_comparison_example_markdown_and_fallback() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "flashscore_odds_comparison.py"
    proc = subprocess.run(
        [sys.executable, str(example), "--markdown", "--show-fallback"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, proc.stderr
    assert "Base HTML scraper vs JSON recipe" in proc.stdout
    assert "no_builtin_flashscore_match" in proc.stdout
    assert "flashscore_match_page.html" not in proc.stdout  # no secret leakage
