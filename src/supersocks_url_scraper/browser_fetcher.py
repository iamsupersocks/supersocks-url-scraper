"""Optional browser-rendered fetching for hostile/paywalled media.

The core package deliberately stays dependency-light. This module is only used
when the ``browser`` extra is installed and a caller explicitly enables browser
fallback. It mirrors the Celeste URL Reader pattern: try normal HTTP first, then
render with CloakBrowser for domains where HTTP/SEO see 403, DataDome, or JS
stubs.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
from dataclasses import dataclass
from typing import Any


class BrowserFetchError(RuntimeError):
    """Raised when optional browser rendering cannot retrieve usable HTML."""


@dataclass(frozen=True)
class BrowserRenderedPage:
    final_url: str
    status_code: int
    html: str
    title: str | None = None
    method: str = "cloak"


async def fetch_with_cloak_async(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    post_load_wait_ms: int = 8000,
    profile_dir: str = "",
) -> BrowserRenderedPage:
    os.environ.setdefault("CLOAKBROWSER_SUPPRESS_FONT_WARNING", "1")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from cloakbrowser import ensure_binary, launch_context_async, launch_persistent_context_async
    except Exception as exc:  # pragma: no cover - depends on optional extra
        raise BrowserFetchError("Install the browser extra: pip install 'supersocks-url-scraper[browser]'") from exc

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        await asyncio.to_thread(ensure_binary)
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "locale": "fr-FR",
        "timezone": "Europe/Paris",
        "humanize": True,
        "stealth_args": True,
        "viewport": {"width": 1366, "height": 768},
    }
    if profile_dir.strip():
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context: Any = await launch_persistent_context_async(profile_dir.strip(), **launch_kwargs)
        method = "cloak-profile"
    else:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context = await launch_context_async(**launch_kwargs)
        method = "cloak"
    try:
        page = await context.new_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        if post_load_wait_ms > 0:
            await page.wait_for_timeout(post_load_wait_ms)
        html = await page.content()
        if not html.strip():
            raise BrowserFetchError("cloak rendered an empty page")
        return BrowserRenderedPage(
            final_url=page.url,
            status_code=response.status if response is not None else 0,
            html=html,
            title=(await page.title()) or None,
            method=method,
        )
    finally:
        await context.close()


def fetch_with_cloak(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    post_load_wait_ms: int = 8000,
    profile_dir: str = "",
) -> BrowserRenderedPage:
    try:
        return asyncio.run(
            fetch_with_cloak_async(
                url,
                timeout_seconds=timeout_seconds,
                post_load_wait_ms=post_load_wait_ms,
                profile_dir=profile_dir,
            )
        )
    except BrowserFetchError:
        raise
    except RuntimeError as exc:
        # If a future embedding calls this while an event loop is already
        # running, fail clearly rather than deadlocking.
        raise BrowserFetchError(str(exc)) from exc
