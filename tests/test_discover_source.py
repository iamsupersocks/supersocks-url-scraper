from __future__ import annotations

import json
from pathlib import Path

import scripts.discover_source as discover_source


def test_discover_source_records_registry_and_strategy(monkeypatch, tmp_path: Path):
    def fake_call_summarize(*_args, **_kwargs):
        return {
            "status": "ok",
            "content_type": "article",
            "fetch_method": "seo",
            "title": "Example article",
            "summary": "A useful extracted summary from the page.",
            "content": "MUST NOT BE WRITTEN",
            "warnings": ["seo fallback used: referer-google"],
        }

    monkeypatch.setattr(discover_source, "call_summarize", fake_call_summarize)
    discovery = tmp_path / "source-discovery.json"
    cache = tmp_path / "fetch-strategies.json"

    result = discover_source.discover_url(
        "https://www.example.com/article",
        base_url="http://127.0.0.1:8768",
        discovery_path=discovery,
        strategy_cache_path=cache,
    )

    assert result["strategy_cache_updated"] is True
    assert json.loads(discovery.read_text(encoding="utf-8"))["example.com"]["quality"] == "ok"
    assert json.loads(cache.read_text(encoding="utf-8"))["example.com"]["fetch_method"] == "seo"
    assert "MUST NOT BE WRITTEN" not in discovery.read_text(encoding="utf-8")
    assert "MUST NOT BE WRITTEN" not in cache.read_text(encoding="utf-8")


def test_discover_source_does_not_cache_needs_review(monkeypatch, tmp_path: Path):
    def fake_call_summarize(*_args, **_kwargs):
        return {
            "status": "ok",
            "content_type": "article",
            "fetch_method": "cloak",
            "summary": "Too suspicious to cache",
            "warnings": ["subscriber-only teaser detected"],
        }

    monkeypatch.setattr(discover_source, "call_summarize", fake_call_summarize)
    discovery = tmp_path / "source-discovery.json"
    cache = tmp_path / "fetch-strategies.json"

    result = discover_source.discover_url(
        "https://www.example.com/article",
        base_url="http://127.0.0.1:8768",
        discovery_path=discovery,
        strategy_cache_path=cache,
    )

    assert result["record"]["quality"] == "needs_review"
    assert result["strategy_cache_updated"] is False
    assert not cache.exists()
