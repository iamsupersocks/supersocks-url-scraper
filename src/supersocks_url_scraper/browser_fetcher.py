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
import enum
import io
import os
import re
import threading
from dataclasses import dataclass
from typing import Any


class BrowserFetchError(RuntimeError):
    """Raised when optional browser rendering cannot retrieve usable HTML."""


class BrowserChallengeError(BrowserFetchError):
    """Raised when browser rendering is blocked by an automatic anti-bot challenge.

    Carries structured, machine-readable fields so callers can return a
    partial/error result with an actionable warning and a compatible reason
    instead of surfacing the challenge page as usable content.
    """

    def __init__(
        self,
        url: str,
        *,
        kind: ChallengeKind,
        reason: str,
        attempts: int,
        retried: bool,
        warning: str,
    ) -> None:
        self.url = url
        self.kind = kind
        self.reason = reason
        self.attempts = attempts
        self.retried = retried
        self.warning = warning
        super().__init__(f"browser blocked by automatic challenge ({kind.value}): {reason}")


class ChallengeKind(enum.Enum):
    """Structured, conservative category for an automatic anti-bot challenge.

    These are deliberately generic categories, not names of any real site or
    vendor brand: the classifier signals *what kind of obstacle* a page is, so
    callers can warn and stop without trying to solve it.
    """

    NONE = "none"
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    ACCESS_DENIED = "access_denied"
    GENERIC_CHALLENGE = "generic_challenge"


@dataclass(frozen=True)
class ChallengeResult:
    """Result of classifying a rendered page as an automatic challenge or not."""

    kind: ChallengeKind
    reason: str
    matched: tuple[str, ...] = ()

    @property
    def is_challenge(self) -> bool:
        return self.kind is not ChallengeKind.NONE

    @property
    def label(self) -> str:
        return _CHALLENGE_LABELS.get(self.kind, self.kind.value)


_CHALLENGE_LABELS: dict[ChallengeKind, str] = {
    ChallengeKind.CAPTCHA: "CAPTCHA / anti-bot verification",
    ChallengeKind.CLOUDFLARE: "Cloudflare-like browser check",
    ChallengeKind.ACCESS_DENIED: "access denied / forbidden",
    ChallengeKind.GENERIC_CHALLENGE: "generic bot challenge",
}

# Marker strings are technical, generic signals (script ids, standard phrases,
# challenge-platform paths). They do not name any real site. Classifiers are
# conservative: a page is only classified as a challenge when at least one of
# these markers is present in the rendered title, visible text, final URL or
# HTML. Order within the tuple is not significant; order of the dict is the
# priority (most specific / actionable kind wins when multiple match).
_CHALLENGE_MARKERS: dict[ChallengeKind, tuple[str, ...]] = {
    ChallengeKind.CAPTCHA: (
        "datadome",
        "captcha-delivery.com",
        "data dome",
        "are you a robot",
        "verify you are a human",
        "enter the characters you see",
        "i am not a robot",
        "i'm not a robot",
    ),
    ChallengeKind.CLOUDFLARE: (
        "cf-challenge",
        "cf-browser-verification",
        "cdn-cgi/challenge-platform",
        "challenge-platform",
        "checking your browser before accessing",
        "just a moment",
        "enable javascript and cookies",
        "cloudflare ray id",
        "verify you are a human",
    ),
    ChallengeKind.ACCESS_DENIED: (
        "access denied",
        "access is denied",
        "you don't have permission",
        "you do not have permission",
        "access forbidden",
        "403 forbidden",
        "forbidden (403)",
    ),
    ChallengeKind.GENERIC_CHALLENGE: (
        "unusual traffic",
        "verify you are human",
        "are you a human",
        "automated access has been blocked",
        "anti-bot",
        "anti bot",
        "security check",
        "challenge detected",
    ),
}

# Status codes that commonly accompany an automatic block. Used only as a weak
# tie-breaker to prefer ACCESS_DENIED when a 403/429/503 carries denial wording;
# never sufficient on their own to classify.
_CHALLENGE_BLOCK_STATUSES = (403, 429, 503)


def _normalize(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def classify_challenge(
    *,
    status_code: int,
    title: str | None,
    final_url: str | None,
    visible_text: str | None,
    html: str | None,
) -> ChallengeResult:
    """Conservative, structured classification of an automatic challenge page.

    Uses the HTTP status, rendered title, final URL, visible text and rendered
    HTML together. Only returns a non-NONE kind when a specific technical marker
    is found. No CAPTCHA solving, no clicking, no proxy, no fingerprint rotation
    is ever attempted here or by the caller that consumes this result.
    """
    title_n = _normalize(title)
    text_n = _normalize(visible_text)
    url_n = _normalize(final_url)
    html_n = _normalize(html)
    body = f"{title_n} {text_n} {url_n}"

    for kind, markers in _CHALLENGE_MARKERS.items():
        matched = tuple(m for m in markers if m in body or m in html_n)
        if not matched:
            continue
        # Prefer the more specific denial wording when a block status code is
        # present alongside a generic marker; otherwise keep the marker hit.
        if (
            kind is ChallengeKind.GENERIC_CHALLENGE
            and int(status_code or 0) in _CHALLENGE_BLOCK_STATUSES
            and any(m in body or m in html_n for m in _CHALLENGE_MARKERS[ChallengeKind.ACCESS_DENIED])
        ):
            return ChallengeResult(
                kind=ChallengeKind.ACCESS_DENIED,
                reason=f"block status {status_code} with access-denied wording",
                matched=_CHALLENGE_MARKERS[ChallengeKind.ACCESS_DENIED][:1],
            )
        reason = _CHALLENGE_LABELS[kind]
        if matched:
            reason = f"{reason} (matched: {matched[0]})"
        return ChallengeResult(kind=kind, reason=reason, matched=matched)

    return ChallengeResult(kind=ChallengeKind.NONE, reason="no automatic challenge detected")


_SEMAPHORE_LOCK = threading.Lock()
_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}

_PROFILE_LOCK_GUARD = threading.Lock()
_PROFILE_LOCKS: dict[str, threading.Lock] = {}


def _profile_lock(profile_dir: str) -> threading.Lock:
    """Return a per-directory lock serializing in-process use of the same profile.

    A persistent browser profile directory cannot be opened by two concurrent
    Cloak contexts in the same process without corrupting its cookie/storage
    state, so the same profile must never be used concurrently here. Locking is
    purely in-process (no cross-process file lock is taken).
    """
    key = os.path.abspath(profile_dir)
    with _PROFILE_LOCK_GUARD:
        lock = _PROFILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROFILE_LOCKS[key] = lock
        return lock


def _browser_semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    limit = max(1, int(max_concurrency or 1))
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(limit)
            _SEMAPHORES[limit] = semaphore
        return semaphore


def _truthy_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "none"}


def resolve_headless(headless: bool | None = None) -> bool:
    """Resolve headless vs headed Cloak launch.

    Precedence: explicit argument → ``CLOAK_HEADLESS`` / ``BROWSER_HEADLESS`` →
    default headless True. Headed mode never installs or starts Xvfb; the
    operator must already expose ``DISPLAY`` or ``WAYLAND_DISPLAY``.
    """
    if headless is not None:
        return bool(headless)
    for key in ("CLOAK_HEADLESS", "BROWSER_HEADLESS"):
        parsed = _truthy_env(key)
        if parsed is not None:
            # Treat explicit headed aliases as False even if truthy-parsing would not.
            raw = str(os.environ.get(key) or "").strip().lower()
            if raw in {"headed", "headful"}:
                return False
            return parsed
    return True


def _ensure_display_for_headed(headless: bool) -> None:
    if headless:
        return
    if os.environ.get("DISPLAY", "").strip() or os.environ.get("WAYLAND_DISPLAY", "").strip():
        return
    raise BrowserFetchError(
        "headed CloakBrowser requires DISPLAY or WAYLAND_DISPLAY; "
        "attach an existing X11/Wayland/Xvfb session yourself "
        "(this package never installs or starts Xvfb)"
    )


@dataclass(frozen=True)
class BrowserRenderedPage:
    final_url: str
    status_code: int
    html: str
    title: str | None = None
    method: str = "cloak"
    consent_action: str | None = None


_CONSENT_REJECTION_LABELS = (
    "Continuer sans accepter",
    "Tout refuser",
    "Refuser et continuer",
    "Je refuse",
    "Refuser",
    "Continue without accepting",
    "Reject all",
    "Decline all",
    "Reject",
)


def _looks_like_consent_wall(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return False
    strong_markers = (
        "contenu de la fenêtre de consentement",
        "contenu de la fenetre de consentement",
        "continuer sans accepter",
        "continue without accepting",
        "centre de préférences de la confidentialité",
        "privacy preference center",
    )
    if any(marker in normalized for marker in strong_markers):
        return True
    marker_groups = (
        ("nous utilisons des cookies", "personnaliser", "accepter"),
        ("utilisation de cookies", "personnaliser", "refuser"),
        ("we use cookies", "customize", "accept"),
        ("we use cookies", "manage preferences", "reject"),
    )
    return any(all(marker in normalized for marker in group) for group in marker_groups)


async def _dismiss_consent_wall(page: Any) -> str | None:
    """Dismiss a detected CMP through an explicit privacy-preserving control."""
    try:
        visible_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        visible_text = ""
    if not _looks_like_consent_wall(visible_text):
        return None

    for label in _CONSENT_REJECTION_LABELS:
        try:
            button = page.get_by_role(
                "button",
                name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I),
            )
            count = await button.count()
        except Exception:
            continue
        for index in range(min(count, 5)):
            candidate = button.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=5000)
                await page.wait_for_timeout(1500)
                return label
            except Exception:
                continue
    return None


async def _page_visible_text(page: Any) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


async def _launch_context(
    *,
    profile_dir: str,
    launch_kwargs: dict[str, Any],
    launch_context_async: Any,
    launch_persistent_context_async: Any,
) -> tuple[Any, str]:
    """Launch a Cloak context, returning ``(context, method)``."""
    if profile_dir.strip():
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context = await launch_persistent_context_async(profile_dir.strip(), **launch_kwargs)
        return context, "cloak-profile"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        context = await launch_context_async(**launch_kwargs)
    return context, "cloak"


async def fetch_with_cloak_async(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    post_load_wait_ms: int = 8000,
    profile_dir: str = "",
    headless: bool | None = None,
) -> BrowserRenderedPage:
    os.environ.setdefault("CLOAKBROWSER_SUPPRESS_FONT_WARNING", "1")
    resolved_headless = resolve_headless(headless)
    _ensure_display_for_headed(resolved_headless)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from cloakbrowser import ensure_binary, launch_context_async, launch_persistent_context_async
    except Exception as exc:  # pragma: no cover - depends on optional extra
        raise BrowserFetchError("Install the browser extra: pip install 'supersocks-url-scraper[browser]'") from exc

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        await asyncio.to_thread(ensure_binary)
    launch_kwargs: dict[str, Any] = {
        "headless": resolved_headless,
        "locale": "fr-FR",
        "timezone": "Europe/Paris",
        "humanize": True,
        "stealth_args": True,
        "viewport": {"width": 1366, "height": 768},
    }

    # A bounded persistent retry: only when a profile directory is provided, and
    # only ever one relaunch. No retry without a profile, no loop, no CAPTCHA
    # clicking/solving, no proxy, no fingerprint rotation.
    has_profile = bool(profile_dir.strip())
    max_attempts = 2 if has_profile else 1
    last_classification: ChallengeResult | None = None

    for attempt in range(max_attempts):
        context, method = await _launch_context(
            profile_dir=profile_dir,
            launch_kwargs=launch_kwargs,
            launch_context_async=launch_context_async,
            launch_persistent_context_async=launch_persistent_context_async,
        )
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            if post_load_wait_ms > 0:
                await page.wait_for_timeout(post_load_wait_ms)
            consent_action = await _dismiss_consent_wall(page)
            html = await page.content()
            if not html.strip():
                raise BrowserFetchError("cloak rendered an empty page")
            final_url = page.url
            status_code = response.status if response is not None else 0
            title = (await page.title()) or None
            visible_text = await _page_visible_text(page)
            classification = classify_challenge(
                status_code=status_code,
                title=title,
                final_url=final_url,
                visible_text=visible_text,
                html=html,
            )
            if not classification.is_challenge:
                return BrowserRenderedPage(
                    final_url=final_url,
                    status_code=status_code,
                    html=html,
                    title=title,
                    method=method,
                    consent_action=consent_action,
                )
            last_classification = classification
            last_status = status_code
            # Challenge detected: with a profile, relaunch once with the SAME
            # profile (context closes cleanly in the finally below). Without a
            # profile this is already the last attempt and we fall through to
            # the persistent-challenge error below.
            if attempt < max_attempts - 1:
                continue
        finally:
            await context.close()

    assert last_classification is not None
    warning = (
        f"browser render blocked by {last_classification.label}. "
        f"No CAPTCHA solving, no proxy, no fingerprint rotation attempted."
    )
    raise BrowserChallengeError(
        url,
        kind=last_classification.kind,
        reason=last_classification.reason,
        attempts=max_attempts,
        retried=has_profile and max_attempts > 1,
        warning=warning,
    )


def fetch_with_cloak(
    url: str,
    *,
    timeout_seconds: float = 60.0,
    post_load_wait_ms: int = 8000,
    profile_dir: str = "",
    max_concurrency: int = 1,
    headless: bool | None = None,
) -> BrowserRenderedPage:
    semaphore = _browser_semaphore(max_concurrency)
    acquired = semaphore.acquire(timeout=max(1.0, float(timeout_seconds)))
    if not acquired:
        raise BrowserFetchError(f"browser concurrency limit reached ({max_concurrency})")
    # Serialize in-process use of the same profile directory: a persistent
    # profile must not be opened by two concurrent Cloak contexts.
    profile_lock = _profile_lock(profile_dir) if profile_dir.strip() else None
    if profile_lock is not None:
        profile_lock.acquire()
    try:
        try:
            return asyncio.run(
                fetch_with_cloak_async(
                    url,
                    timeout_seconds=timeout_seconds,
                    post_load_wait_ms=post_load_wait_ms,
                    profile_dir=profile_dir,
                    headless=headless,
                )
            )
        except BrowserFetchError:
            raise
        except RuntimeError as exc:
            # If a future embedding calls this while an event loop is already
            # running, fail clearly rather than deadlocking.
            raise BrowserFetchError(str(exc)) from exc
    finally:
        if profile_lock is not None:
            profile_lock.release()
        semaphore.release()
