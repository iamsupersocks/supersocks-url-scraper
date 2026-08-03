"""Extensible social-platform routing for public URL reads.

Architectural inspiration: Agent Reach (MIT) — channel-style platform routing
with ordered backends. This package adapts that idea minimally for public
YouTube/LinkedIn reads without copying Agent Reach code, installing private
indexers, or enabling authenticated social scrapers.
"""

from __future__ import annotations

from .routing import detect_platform, try_social_read

__all__ = ["detect_platform", "try_social_read"]
