"""Optional browser-rendered fetching for hostile/paywalled media.

The core package deliberately stays dependency-light. This module is only used
when the ``browser`` extra is installed and a caller explicitly enables browser
fallback. It follows a layered URL-reader pattern: try normal HTTP first, then
render with CloakBrowser for domains where HTTP/SEO see 403, DataDome, or JS
stubs.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import threading
from dataclasses import dataclass
from typing import Any


class BrowserFetchError(RuntimeError):
    """Raised when optional browser rendering cannot retrieve usable HTML."""


_SEMAPHORE_LOCK = threading.Lock()
_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}


def _browser_semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    limit = max(1, int(max_concurrency or 1))
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(limit)
            _SEMAPHORES[limit] = semaphore
        return semaphore


@dataclass(frozen=True)
class BrowserRenderedPage:
    final_url: str
    status_code: int
    html: str
    title: str | None = None
    method: str = "cloak"
    consent_action: str | None = None


CONSENT_WINDOW_SELECTORS = (
    "[role='dialog']",
    "[aria-modal='true']",
    "#cookieConsent",
    "#cookie-consent",
    ".cookie-consent",
    "[id*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='consent' i]",
    "[class*='consent' i]",
    "[id*='privacy' i]",
    "[class*='privacy' i]",
    "[data-testid*='consent' i]",
    "[data-testid*='privacy' i]",
)
CONSENT_WINDOW_MARKERS = re.compile(r"\b(cookie|cookies|consent|privacy|confidentialit[eé]|traceur|traceurs)\b", re.I)
SAFE_CONSENT_LABEL = re.compile(
    r"^(reject all|reject optional|decline all|deny|continue without accepting|continue without accept|"
    r"continuer sans accepter|continuer sans consentir|tout refuser|refuser|refuse all)$",
    re.I,
)


async def click_safe_consent_control(page: Any) -> str | None:
    """Click only explicit reject/continue-without-accept consent controls.

    This guard is deliberately conservative: it first requires a recognizable
    consent/cookie/privacy window and then clicks only a button whose visible
    label is an explicit refusal or continuation without accepting. It never
    clicks generic buttons or acceptance labels.
    """
    for selector in CONSENT_WINDOW_SELECTORS:
        try:
            windows = page.locator(selector)
            count = min(await windows.count(), 5)
        except Exception:
            continue
        for index in range(count):
            window = windows.nth(index)
            try:
                if hasattr(window, "is_visible") and not await window.is_visible():
                    continue
                text = await window.inner_text(timeout=500)
            except Exception:
                continue
            if not CONSENT_WINDOW_MARKERS.search(text or ""):
                continue
            try:
                buttons = window.locator("button, [role='button']")
                button_count = min(await buttons.count(), 12)
            except Exception:
                continue
            for button_index in range(button_count):
                button = buttons.nth(button_index)
                try:
                    if hasattr(button, "is_visible") and not await button.is_visible():
                        continue
                    label = " ".join((await button.inner_text(timeout=500)).split())
                except Exception:
                    continue
                if SAFE_CONSENT_LABEL.match(label):
                    try:
                        await button.click(timeout=1000)
                    except Exception:
                        # CMPs often reflow while hydrating.  The target is
                        # already restricted to a visible, explicit refusal
                        # control, so a forced retry remains within the guard.
                        try:
                            await button.click(timeout=1000, force=True)
                        except Exception:
                            continue
                    return label
    return None


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
        clicked_consent = await click_safe_consent_control(page)
        if clicked_consent:
            await page.wait_for_timeout(500)
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
            consent_action=clicked_consent,
        )
    finally:
        await context.close()


def fetch_with_cloak(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    post_load_wait_ms: int = 8000,
    profile_dir: str = "",
    max_concurrency: int = 1,
) -> BrowserRenderedPage:
    semaphore = _browser_semaphore(max_concurrency)
    acquired = semaphore.acquire(timeout=max(1.0, float(timeout_seconds)))
    if not acquired:
        raise BrowserFetchError(f"browser concurrency limit reached ({max_concurrency})")
    try:
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
    finally:
        semaphore.release()
