"""Typed models for versioned API recipes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .consent import DEFAULT_CONSENT_PHRASE

ALLOWED_METHODS = frozenset({"GET"})
REQUIRED_RECIPE_KEYS = frozenset({"id", "version", "match", "endpoint"})


@dataclass(frozen=True)
class RecipeMatch:
    host_roots: tuple[str, ...]
    path_regex: str = ""
    query_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipeEndpoint:
    method: str
    url_template: str
    allowed_hosts: tuple[str, ...]
    timeout_seconds: int = 8
    max_bytes: int = 256 * 1024
    max_fanout: int = 8
    min_interval_ms: int = 50
    max_redirects: int = 3
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RecipeNetworkPolicy:
    """Controls whether the recipe may perform live HTTPS GETs.

    Modes:
      - fixture_only / off: never live (injected fetcher only)
      - consent_required: needs API_RECIPE_LIVE_ALLOWLIST + consent phrase
      - open: allowed when API recipes are opt-in (non-restricted hosts)
    """

    mode: str = "consent_required"
    consent_phrase: str = DEFAULT_CONSENT_PHRASE


@dataclass(frozen=True)
class ApiRecipe:
    id: str
    version: str
    title: str
    match: RecipeMatch
    endpoint: RecipeEndpoint
    confidence: float = 0.5
    ttl_seconds: int = 300
    params: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallback: str = "http_seo_cloak_archive"
    schema: str = ""
    network: RecipeNetworkPolicy = field(default_factory=RecipeNetworkPolicy)

    @property
    def recipe_key(self) -> str:
        return f"{self.id}@v{self.version}"


@dataclass(frozen=True)
class RecipeRunResult:
    status: str
    url: str
    recipe_id: str
    recipe_version: str
    fetch_method: str
    title: str | None
    summary: str
    structured_data: dict[str, Any]
    warnings: list[str]
    confidence: float
    captured_at: str
    ttl_seconds: int
    content_type: str = "application/json"
    length: int = 900

    def as_reader_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "url": self.url,
            "content_type": self.content_type,
            "title": self.title,
            "summary": self.summary,
            "length": self.length,
            "fetch_method": self.fetch_method,
            "warnings": list(self.warnings),
            "structured_data": self.structured_data,
            "api_recipe": {
                "id": self.recipe_id,
                "version": self.recipe_version,
                "confidence": self.confidence,
                "ttl_seconds": self.ttl_seconds,
                "captured_at": self.captured_at,
            },
        }
        if include_content:
            import json

            payload["content"] = json.dumps(self.structured_data, ensure_ascii=False, indent=2)
        return payload
