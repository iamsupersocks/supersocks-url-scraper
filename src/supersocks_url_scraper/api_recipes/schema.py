"""Embedded JSON Schema v1 for API recipe documents.

This is the canonical, shipped schema for a v1 recipe file. It is deliberately
aligned with the runtime validator in engine.validate_recipe_dict: anything the
runtime rejects, the schema rejects, and vice-versa for the documented fields.

The schema is embedded as a dict (no external dependency) and also mirrored to a
JSON file shipped in the wheel under ``api_recipes/schemas/recipe.v1.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECIPE_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://supersocks.local/schemas/recipe.v1.json",
    "title": "Supersocks API Recipe v1",
    "description": (
        "A versioned, read-only HTTPS GET API recipe. Never carries credentials. "
        "network.mode controls whether live outbound GETs are permitted; "
        "fixture_only/off/disabled are always blocked."
    ),
    "type": "object",
    "required": ["id", "version", "match", "endpoint"],
    "additionalProperties": True,
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        "schema": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "ttl_seconds": {"type": "integer", "minimum": 0},
        "status": {"type": "string", "enum": ["active", "review_required", "disabled"]},
        "review_required": {"type": "boolean"},
        "match": {
            "type": "object",
            "required": ["host_roots"],
            "properties": {
                "host_roots": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "path_regex": {"type": "string"},
                "query_keys": {"type": "array", "items": {"type": "string"}},
            },
        },
        "endpoint": {
            "type": "object",
            "required": ["method", "url_template", "allowed_hosts"],
            "properties": {
                "method": {"type": "string", "enum": ["GET"]},
                "url_template": {"type": "string", "pattern": "^https://"},
                "allowed_hosts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "timeout_seconds": {"type": "integer", "minimum": 0},
                "max_bytes": {"type": "integer", "minimum": 0},
                "max_fanout": {"type": "integer", "minimum": 0},
                "min_interval_ms": {"type": "integer", "minimum": 0},
                "max_redirects": {"type": "integer", "minimum": 0},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": False,
        },
        "params": {"type": "object"},
        "response": {"type": "object"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "fallback": {"type": "string"},
        "network": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "fixture_only",
                        "off",
                        "disabled",
                        "never",
                        "consent_required",
                        "allowlist",
                        "open",
                        "allow",
                    ],
                },
                "consent_phrase": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "discovery": {"type": "object"},
    },
}


def schema_file_path() -> Path:
    return Path(__file__).resolve().parent / "schemas" / "recipe.v1.json"


def load_schema() -> dict[str, Any]:
    """Return the shipped JSON Schema (embedded dict, no file IO dependency)."""
    return RECIPE_SCHEMA_V1


def schema_file_contents() -> str:
    """Return the shipped schema as JSON text (from the embedded dict)."""
    return json.dumps(RECIPE_SCHEMA_V1, ensure_ascii=False, indent=2)


def validate_recipe_schema(raw: dict[str, Any]) -> list[str]:
    """Validate a recipe dict against the embedded JSON Schema.

    Returns a list of human-readable errors (empty when the document conforms).
    This is a lightweight, dependency-free validator covering the documented
    constraints; it is kept consistent with engine.validate_recipe_dict.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["recipe must be an object"]

    for key in ("id", "version"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be a non-empty string")

    for key in ("match", "endpoint"):
        if not isinstance(raw.get(key), dict):
            errors.append(f"{key} must be an object")

    match = raw.get("match")
    if isinstance(match, dict):
        roots = match.get("host_roots")
        if not isinstance(roots, list) or not roots or not all(isinstance(x, str) and x for x in roots):
            errors.append("match.host_roots must be a non-empty list of strings")

    endpoint = raw.get("endpoint")
    if isinstance(endpoint, dict):
        method = str(endpoint.get("method") or "").upper()
        if method != "GET":
            errors.append("endpoint.method must be GET")
        template = str(endpoint.get("url_template") or "")
        if not template.startswith("https://"):
            errors.append("endpoint.url_template must be an https URL")
        hosts = endpoint.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts or not all(isinstance(x, str) and x for x in hosts):
            errors.append("endpoint.allowed_hosts must be a non-empty list of strings")
        headers = endpoint.get("headers") or {}
        if headers and not isinstance(headers, dict):
            errors.append("endpoint.headers must be an object")
        elif isinstance(headers, dict):
            for name in headers:
                lowered = str(name).lower()
                if lowered in {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}:
                    errors.append(f"endpoint.headers must not include {name}")
        for key in ("timeout_seconds", "max_bytes", "max_fanout", "min_interval_ms", "max_redirects"):
            if key in endpoint and (not isinstance(endpoint[key], int) or endpoint[key] < 0):
                errors.append(f"endpoint.{key} must be a non-negative int")

    confidence = raw.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0)
    ):
        errors.append("confidence must be between 0 and 1")

    ttl = raw.get("ttl_seconds")
    if ttl is not None and (not isinstance(ttl, int) or ttl < 0):
        errors.append("ttl_seconds must be a non-negative int")

    network = raw.get("network")
    if network is not None:
        if not isinstance(network, dict):
            errors.append("network must be an object")
        else:
            mode = str(network.get("mode") or "").strip().lower()
            if mode and mode not in {
                "fixture_only",
                "off",
                "disabled",
                "never",
                "consent_required",
                "allowlist",
                "open",
                "allow",
            }:
                errors.append("network.mode is invalid")

    # Round-trip the schema doc to guarantee it is always valid JSON.
    try:
        json.dumps(RECIPE_SCHEMA_V1)
    except (TypeError, ValueError) as exc:  # pragma: no cover
        errors.append(f"embedded schema is not valid JSON: {exc}")

    return errors