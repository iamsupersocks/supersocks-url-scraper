"""Extensible social-platform routing for public URL reads.

Architectural inspiration: Agent Reach (MIT) — channel-style platform routing
with ordered backends. This package adapts that idea minimally for:

- public YouTube / LinkedIn reads
- Cloak-first Reddit / Instagram / Facebook HTML extraction
- opt-in local X reads via upstream twitter-cli (explicit env credentials only)
- opt-in desktop Instagram / Facebook OpenCLI fallback (never automatic)
- opt-in Reddit rdt-cli fallback (never automatic, never auto-cookie)

It does not copy Agent Reach code, install private indexers, auto-read cookies,
or ship tokens/profiles.
"""

from __future__ import annotations

from .routing import SOCIAL_PLATFORMS, detect_platform, try_social_read

__all__ = ["SOCIAL_PLATFORMS", "detect_platform", "try_social_read"]
