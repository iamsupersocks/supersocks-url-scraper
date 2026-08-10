from __future__ import annotations

import asyncio
import sys
import threading
import types
from typing import Any

import pytest

from supersocks_url_scraper.browser_fetcher import (
    BrowserChallengeError,
    BrowserRenderedPage,
    ChallengeKind,
    classify_challenge,
    fetch_with_cloak,
    fetch_with_cloak_async,
    _profile_lock,
)


# ---------------------------------------------------------------------------
# Pure classification tests (no browser, no network, no real site names).
# ---------------------------------------------------------------------------


def test_classify_none_on_normal_article() -> None:
    result = classify_challenge(
        status_code=200,
        title="Article about economics",
        final_url="https://example.test/article/123",
        visible_text="A long interesting article body with useful content.",
        html="<html><title>Article about economics</title><p>body</p></html>",
    )
    assert result.is_challenge is False
    assert result.kind is ChallengeKind.NONE


def test_classify_captcha_like_from_html() -> None:
    result = classify_challenge(
        status_code=200,
        title="Access verification",
        final_url="https://example.test/",
        visible_text="",
        html='<html><body><script src="https://geo.captcha-delivery.com/x.js"></script></body></html>',
    )
    assert result.is_challenge is True
    assert result.kind is ChallengeKind.CAPTCHA


def test_classify_captcha_like_from_text() -> None:
    result = classify_challenge(
        status_code=200,
        title="Verify you are a human",
        final_url="https://example.test/",
        visible_text="Enter the characters you see below.",
        html="<html></html>",
    )
    assert result.kind is ChallengeKind.CAPTCHA


def test_classify_cloudflare_like() -> None:
    result = classify_challenge(
        status_code=403,
        title="Just a moment...",
        final_url="https://example.test/",
        visible_text="Checking your browser before accessing.",
        html='<html><script src="/cdn-cgi/challenge-platform/"></script></html>',
    )
    assert result.is_challenge is True
    assert result.kind is ChallengeKind.CLOUDFLARE


def test_classify_access_denied() -> None:
    result = classify_challenge(
        status_code=403,
        title="403 Forbidden",
        final_url="https://example.test/private",
        visible_text="Access denied. You don't have permission.",
        html="<html><body>Access denied</body></html>",
    )
    assert result.is_challenge is True
    assert result.kind is ChallengeKind.ACCESS_DENIED


def test_classify_generic_challenge() -> None:
    result = classify_challenge(
        status_code=200,
        title="Security Check",
        final_url="https://example.test/",
        visible_text="Unusual traffic from your network has been detected.",
        html="<html><title>Security Check</title></html>",
    )
    assert result.is_challenge is True
    assert result.kind is ChallengeKind.GENERIC_CHALLENGE


def test_classify_block_status_with_denial_prefers_access_denied() -> None:
    result = classify_challenge(
        status_code=403,
        title="",
        final_url="",
        visible_text="Access denied",
        html="",
    )
    assert result.kind is ChallengeKind.ACCESS_DENIED


def test_classify_missing_inputs_is_not_challenge() -> None:
    result = classify_challenge(status_code=0, title=None, final_url=None, visible_text=None, html=None)
    assert result.is_challenge is False
    assert result.kind is ChallengeKind.NONE


# ---------------------------------------------------------------------------
# Fake Cloak browser plumbing for retry tests (no real browser, no network).
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    def __init__(self, *, html: str, title: str | None, final_url: str, status: int) -> None:
        self._html = html
        self._title = title or ""
        self.url = final_url
        self._status = status

    async def goto(self, url: str, **_: Any) -> FakeResponse:
        return FakeResponse(self._status)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def content(self) -> str:
        return self._html

    async def title(self) -> str:
        return self._title

    def locator(self, _selector: str) -> Any:
        return FakeLocator(self._html)

    def get_by_role(self, _role: str, **_: Any) -> Any:
        return FakeRoleList()


class FakeLocator:
    def __init__(self, html: str) -> None:
        self._html = html

    async def inner_text(self, **_: Any) -> str:
        # crude strip of tags so visible text is non-empty for normal pages
        return self._html.replace("<html>", "").replace("</html>", "").replace("<body>", "").replace("</body>", "")


class FakeRoleList:
    def __init__(self) -> None:
        self._count = 0

    async def count(self) -> int:
        return self._count

    def nth(self, _index: int) -> Any:
        return self


class FakeContext:
    def __init__(self, page_spec: dict[str, Any]) -> None:
        self._page_spec = page_spec
        self.closed = False

    async def new_page(self) -> FakePage:
        return FakePage(**self._page_spec)

    async def close(self) -> None:
        self.closed = True


class FakeCloakModule:
    """Replaces ``cloakbrowser`` in sys.modules during retry tests.

    ``page_specs`` is a list of page-spec dicts consumed in order; the last one
    repeats for any extra launch (so a persistent challenge repeats forever,
    and a success-after-fail scenario yields exactly two distinct launches).
    """

    def __init__(self, page_specs: list[dict[str, Any]], *, persistent: bool = True) -> None:
        self.page_specs = page_specs
        self.persistent = persistent
        self.launches: list[dict[str, Any]] = []
        self.ensure_calls = 0

    def ensure_binary(self) -> None:
        self.ensure_calls += 1

    def _next_spec(self) -> dict[str, Any]:
        if not self.page_specs:
            raise RuntimeError("fake has no page specs")
        # launches is appended before _next_spec is called, so the current
        # launch index is the count after the append.
        index = min(len(self.launches) - 1, len(self.page_specs) - 1)
        return self.page_specs[max(0, index)]

    async def launch_context_async(self, **kwargs: Any) -> FakeContext:
        self.launches.append({"persistent": False, "kwargs": kwargs})
        return FakeContext(self._next_spec())

    async def launch_persistent_context_async(self, profile_dir: str, **kwargs: Any) -> FakeContext:
        self.launches.append({"persistent": True, "profile_dir": profile_dir, "kwargs": kwargs})
        return FakeContext(self._next_spec())


@pytest.fixture
def fake_cloak(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, FakeCloakModule] = {}

    def _install(page_specs: list[dict[str, Any]], *, persistent: bool = True) -> FakeCloakModule:
        module = FakeCloakModule(page_specs, persistent=persistent)
        fake = types.ModuleType("cloakbrowser")
        fake.ensure_binary = module.ensure_binary
        fake.launch_context_async = module.launch_context_async
        fake.launch_persistent_context_async = module.launch_persistent_context_async
        monkeypatch.setitem(sys.modules, "cloakbrowser", fake)
        created["module"] = module
        return module

    _install.installed = created
    yield _install


def _challenge_spec(kind: str) -> dict[str, Any]:
    if kind == "captcha":
        return {
            "html": '<html><body><script src="https://geo.captcha-delivery.com/x.js"></script></body></html>',
            "title": "Access verification",
            "final_url": "https://example.test/",
            "status": 200,
        }
    if kind == "cloudflare":
        return {
            "html": '<html><body><script src="/cdn-cgi/challenge-platform/"></script></body></html>',
            "title": "Just a moment...",
            "final_url": "https://example.test/",
            "status": 403,
        }
    if kind == "access":
        return {
            "html": "<html><body>Access denied</body></html>",
            "title": "403 Forbidden",
            "final_url": "https://example.test/private",
            "status": 403,
        }
    raise ValueError(kind)


def _success_spec() -> dict[str, Any]:
    return {
        "html": "<html><title>Good</title><body>Real content here.</body></html>",
        "title": "Good",
        "final_url": "https://example.test/ok",
        "status": 200,
    }


# ---------------------------------------------------------------------------
# Retry semantics: 0 retries without a profile, exactly 1 with a profile.
# ---------------------------------------------------------------------------


def test_no_profile_challenge_raises_and_never_retries(fake_cloak) -> None:
    module = fake_cloak([_challenge_spec("captcha")])
    with pytest.raises(BrowserChallengeError) as excinfo:
        fetch_with_cloak("https://example.test/", timeout_seconds=5, post_load_wait_ms=0)
    err = excinfo.value
    assert err.kind is ChallengeKind.CAPTCHA
    assert err.retried is False
    assert err.attempts == 1
    assert len(module.launches) == 1
    assert module.launches[0]["persistent"] is False
    assert "No CAPTCHA solving" in err.warning


def test_profile_persistent_challenge_raises_after_exactly_one_retry(fake_cloak) -> None:
    module = fake_cloak([_challenge_spec("cloudflare"), _challenge_spec("cloudflare")], persistent=True)
    with pytest.raises(BrowserChallengeError) as excinfo:
        fetch_with_cloak(
            "https://example.test/",
            timeout_seconds=5,
            post_load_wait_ms=0,
            profile_dir="/tmp/profile-test-1",
        )
    err = excinfo.value
    assert err.kind is ChallengeKind.CLOUDFLARE
    assert err.retried is True
    assert err.attempts == 2
    assert len(module.launches) == 2
    assert all(l["persistent"] is True for l in module.launches)
    assert module.launches[0]["profile_dir"] == module.launches[1]["profile_dir"]


def test_profile_second_pass_succeeds(fake_cloak) -> None:
    module = fake_cloak([_challenge_spec("captcha"), _success_spec()], persistent=True)
    page = fetch_with_cloak(
        "https://example.test/",
        timeout_seconds=5,
        post_load_wait_ms=0,
        profile_dir="/tmp/profile-test-2",
    )
    assert isinstance(page, BrowserRenderedPage)
    assert page.method == "cloak-profile"
    assert page.status_code == 200
    assert "Real content here." in page.html
    assert len(module.launches) == 2
    assert all(l["persistent"] is True for l in module.launches)
    assert all(l["profile_dir"] == "/tmp/profile-test-2" for l in module.launches)


def test_success_first_pass_uses_ephemeral_context_without_profile(fake_cloak) -> None:
    module = fake_cloak([_success_spec()], persistent=False)
    page = fetch_with_cloak("https://example.test/", timeout_seconds=5, post_load_wait_ms=0)
    assert page.method == "cloak"
    assert len(module.launches) == 1
    assert module.launches[0]["persistent"] is False


# ---------------------------------------------------------------------------
# In-process per-profile concurrency protection.
# ---------------------------------------------------------------------------


def test_profile_lock_is_shared_per_directory() -> None:
    a1 = _profile_lock("/tmp/shared-prof")
    a2 = _profile_lock("/tmp/shared-prof")
    b = _profile_lock("/tmp/other-prof")
    assert a1 is a2
    assert a1 is not b


def test_fetch_holds_profile_lock_until_close(fake_cloak, monkeypatch: pytest.MonkeyPatch) -> None:
    """While a persistent render is running, a second call for the same profile
    must block (serialized), not open the profile concurrently."""
    module = fake_cloak([_success_spec()], persistent=True)
    lock = _profile_lock("/tmp/concurrent-prof")

    started = threading.Event()
    release = threading.Event()
    entered: list[str] = []

    async def _blocking_page(self: FakeContext) -> FakePage:
        started.set()
        release.wait(timeout=5)
        return FakePage(**_success_spec())

    original_new_page = FakeContext.new_page
    FakeContext.new_page = _blocking_page  # type: ignore[method-assign]

    result: list[Any] = []

    def _run() -> None:
        result.append(fetch_with_cloak("https://example.test/", timeout_seconds=10, post_load_wait_ms=0, profile_dir="/tmp/concurrent-prof"))

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(timeout=5)

    # While the first render holds the profile lock, acquiring it here must block.
    acquired = lock.acquire(timeout=0.2)
    assert acquired is False, "profile lock not held during render"

    release.set()
    t.join(timeout=10)
    FakeContext.new_page = original_new_page  # type: ignore[method-assign]

    assert len(result) == 1
    assert lock.acquire(timeout=0.2) is True
    lock.release()


# ---------------------------------------------------------------------------
# Secrets hygiene: error/warning must not leak page content, headers, or secrets.
# ---------------------------------------------------------------------------


def test_challenge_error_does_not_leak_page_content(fake_cloak) -> None:
    secret = "S3CR3T_TOKEN_XYZ"
    spec = _challenge_spec("captcha")
    spec["html"] += f"<div>super-secret {secret}</div>"
    fake_cloak([spec])
    with pytest.raises(BrowserChallengeError) as excinfo:
        fetch_with_cloak("https://example.test/", timeout_seconds=5, post_load_wait_ms=0)
    err = excinfo.value
    # Neither the message nor the warning nor the reason should embed raw HTML.
    for attr in (str(err), err.warning, err.reason, str(err.kind)):
        assert secret not in attr


def test_consent_wall_non_regression_dismisses_then_returns(fake_cloak) -> None:
    """A page that is a consent wall but NOT an anti-bot challenge must still
    dismiss the CMP and return real content (no retry, no challenge error)."""
    page_spec = {
        "html": (
            "<html><title>Article</title><body>"
            "Contenu de la fenêtre de consentement. Nous utilisons des cookies. "
            "Continuer sans accepter. Real article content."
            "</body></html>"
        ),
        "title": "Article",
        "final_url": "https://example.test/consent",
        "status": 200,
    }
    module = fake_cloak([page_spec], persistent=False)
    page = fetch_with_cloak("https://example.test/", timeout_seconds=5, post_load_wait_ms=0)
    assert page.status_code == 200
    assert "Real article content." in page.html
    assert len(module.launches) == 1
