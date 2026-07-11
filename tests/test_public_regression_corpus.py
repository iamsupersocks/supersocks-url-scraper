from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_ROUTES = {"http", "seo", "cloak", "cloak-profile", "archive", "fallback"}
ALLOWED_TYPES = {"article", "pdf", "image"}
SECRET_MARKERS = ("token", "cookie", "authorization", "bearer ", "api_key", "apikey", "password", "session")


def test_public_regression_corpus_schema_and_safety() -> None:
    path = Path(__file__).parent / "fixtures" / "public_regression_corpus.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    assert len(data) >= 20
    ids = [item["id"] for item in data]
    assert len(ids) == len(set(ids))
    assert len({item["domain"] for item in data}) >= 15
    assert any(item["content_type"] == "pdf" for item in data)
    assert any(item["content_type"] == "image" for item in data)
    assert any("cloak" in item["expected_routes"] or "cloak-profile" in item["expected_routes"] for item in data)
    assert any("archive" in item["expected_routes"] for item in data)

    raw = json.dumps(data).lower()
    assert not any(marker in raw for marker in SECRET_MARKERS)

    for item in data:
        parsed = urlparse(item["url"])
        assert parsed.scheme == "https"
        assert parsed.netloc
        assert item["domain"] in parsed.netloc
        assert item["content_type"] in ALLOWED_TYPES
        assert set(item["expected_routes"]).issubset(ALLOWED_ROUTES)
        assert item["priority"] in {"P0", "P1", "P2"}
        assert item["notes"].strip()
