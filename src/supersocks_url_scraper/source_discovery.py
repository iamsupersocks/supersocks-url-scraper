"""Runtime source discovery registry for URLs seen by a URL-reader pipeline.

The registry stores only source/routing metadata. It intentionally does not store
fetched page content, cookies, tokens, browser sessions, credentials, prompts, or
raw user/private data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .reader import normalize_domain

BAD_WARNING_MARKERS = (
    "boilerplate",
    "cookie/consent",
    "domain-only stub",
    "domain/title-only stub",
    "subscriber-only teaser",
    "unsupported content type",
    "placeholder image description",
    "fetch failed",
    "captcha",
)


def classify_quality(response: dict[str, Any]) -> str:
    """Return ok/needs_review/failed from a `/summarize` response."""
    status = str(response.get("status") or "").lower()
    summary = str(response.get("summary") or "").strip()
    warnings = [str(w).lower() for w in response.get("warnings") or []]

    if status in {"error", "blocked"}:
        return "failed"
    if status != "ok" or not summary:
        return "needs_review"
    if any(any(marker in warning for marker in BAD_WARNING_MARKERS) for warning in warnings):
        return "needs_review"
    return "ok"


def update_source_discovery(path: Path, url: str, response: dict[str, Any]) -> dict[str, Any]:
    """Upsert a sanitized per-domain discovery record and return it."""
    domain = normalize_domain(url)
    if not domain:
        raise ValueError(f"could not normalize domain for {url!r}")

    data = _read_json_object(path)
    now = _now_iso()
    raw_existing = data.get(domain)
    existing = raw_existing if isinstance(raw_existing, dict) else {}
    seen_count = int(existing.get("seen_count") or 0) + 1
    first_seen_at = str(existing.get("first_seen_at") or now)

    warnings = [str(w)[:240] for w in (response.get("warnings") or [])[:8]]
    record = {
        "domain": domain,
        "first_seen_at": first_seen_at,
        "last_seen_at": now,
        "seen_count": seen_count,
        "last_url": url,
        "status": str(response.get("status") or ""),
        "quality": classify_quality(response),
        "content_type": str(response.get("content_type") or "unknown"),
        "fetch_method": str(response.get("fetch_method") or ""),
        "title": _safe_text(response.get("title"), 180),
        "summary_chars": len(str(response.get("summary") or "")),
        "warnings": warnings,
    }
    data[domain] = record
    _write_json_object(path, data)
    return record


def extract_strategy_detail(response: dict[str, Any]) -> str:
    """Extract optional strategy detail from summarize warnings."""
    for warning in response.get("warnings") or []:
        text = str(warning)
        for prefix in ("seo fallback used: ", "archive fallback used: ", "browser fallback used: "):
            if text.startswith(prefix):
                return text.removeprefix(prefix).strip()
    return "source-discovery"


def _safe_text(value: object, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
