from __future__ import annotations

import json
from argparse import Namespace

import scripts.discover_strategy as discover_strategy


def test_discover_strategy_writes_only_metadata(monkeypatch, tmp_path):
    calls = []

    def fake_read_url(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return {
            "status": "ok",
            "url": url,
            "title": "Title",
            "summary": "A sufficiently long extracted summary for route discovery.",
            "fetch_method": "cloak-profile",
            "content": "this must never be written",
            "warnings": ["browser fallback used"],
        }

    monkeypatch.setattr(discover_strategy, "read_url", fake_read_url)
    cache = tmp_path / "fetch-strategies.json"
    args = Namespace(
        urls=["https://www.lepoint.fr/example"],
        urls_file="",
        cache=str(cache),
        overwrite=False,
        dry_run=False,
        length=600,
        min_summary_chars=20,
        no_seo_fallback=False,
        browser_fallback=True,
        browser_profile_dir="/profiles/default",
        browser_post_load_wait_ms=10000,
        browser_max_concurrency=1,
        no_archive_fallback=False,
    )

    result = discover_strategy.discover(args)
    data = json.loads(cache.read_text(encoding="utf-8"))

    assert result["updated"] == 1
    assert data == {
        "lepoint.fr": {
            "fetch_method": "cloak-profile",
            "success_count": 1,
            "failure_count": 0,
            "detail": "source-discovery",
        }
    }
    assert "content" not in cache.read_text(encoding="utf-8")
    assert calls[-1]["browser_fallback"] is True
    assert calls[-1]["browser_profile_dir"] == "/profiles/default"


def test_discover_strategy_skips_existing_without_overwrite(monkeypatch, tmp_path):
    def fail_read_url(*_args, **_kwargs):
        raise AssertionError("should not probe cached domain")

    monkeypatch.setattr(discover_strategy, "read_url", fail_read_url)
    cache = tmp_path / "fetch-strategies.json"
    cache.write_text(json.dumps({"example.com": {"fetch_method": "http"}}), encoding="utf-8")
    args = Namespace(
        urls=["https://example.com/article"],
        urls_file="",
        cache=str(cache),
        overwrite=False,
        dry_run=False,
        length=600,
        min_summary_chars=20,
        no_seo_fallback=False,
        browser_fallback=True,
        browser_profile_dir="",
        browser_post_load_wait_ms=10000,
        browser_max_concurrency=1,
        no_archive_fallback=False,
    )

    result = discover_strategy.discover(args)

    assert result["updated"] == 0
    results = result["results"]
    assert isinstance(results, list)
    assert results[0]["status"] == "skipped"
