from __future__ import annotations

import json
from pathlib import Path

from supersocks_url_scraper.source_discovery import classify_quality, update_source_discovery


def test_source_discovery_stores_only_sanitized_metadata(tmp_path: Path):
    path = tmp_path / "source-discovery.json"
    response = {
        "status": "ok",
        "content_type": "article",
        "fetch_method": "cloak-profile",
        "title": "A very useful article",
        "summary": "Readable summary from the page.",
        "content": "FULL CONTENT MUST NOT BE STORED",
        "warnings": ["browser fallback used: cloak-profile (initial_status=200)"],
    }

    record = update_source_discovery(path, "https://www.lepoint.fr/example", response)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    assert record["domain"] == "lepoint.fr"
    assert record["quality"] == "ok"
    assert data["lepoint.fr"]["summary_chars"] == len(response["summary"])
    assert "FULL CONTENT" not in raw
    assert "content" not in data["lepoint.fr"]


def test_source_discovery_classifies_bad_warnings_for_review():
    assert classify_quality({"status": "error", "summary": "x"}) == "failed"
    assert classify_quality({"status": "partial", "summary": "x"}) == "needs_review"
    assert classify_quality({"status": "ok", "summary": "", "warnings": []}) == "needs_review"
    assert classify_quality({"status": "ok", "summary": "x", "warnings": ["subscriber-only teaser detected"]}) == "needs_review"
    assert classify_quality({"status": "ok", "summary": "x", "warnings": ["local extractive summary"]}) == "ok"
