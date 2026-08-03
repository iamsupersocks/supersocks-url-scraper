from __future__ import annotations

import asyncio
import re

from supersocks_url_scraper.browser_fetcher import (
    _dismiss_consent_wall,
    _looks_like_consent_wall,
)


class FakeBody:
    def __init__(self, text: str):
        self.text = text

    async def inner_text(self, *, timeout: int) -> str:
        return self.text


class FakeButton:
    def __init__(self, label: str, expected: re.Pattern[str], page: "FakePage"):
        self.label = label
        self.expected = expected
        self.page = page

    async def count(self) -> int:
        return 1 if self.expected.search(self.label) else 0

    def nth(self, index: int) -> "FakeButton":
        return self

    async def is_visible(self) -> bool:
        return True

    async def click(self, *, timeout: int) -> None:
        self.page.clicked = self.label


class FakePage:
    def __init__(self, text: str, labels: list[str]):
        self.text = text
        self.labels = labels
        self.clicked: str | None = None

    def locator(self, selector: str) -> FakeBody:
        assert selector == "body"
        return FakeBody(self.text)

    def get_by_role(self, role: str, *, name: re.Pattern[str]) -> FakeButton:
        assert role == "button"
        label = next((candidate for candidate in self.labels if name.search(candidate)), "")
        return FakeButton(label, name, self)

    async def wait_for_timeout(self, milliseconds: int) -> None:
        return None


def test_looks_like_french_consent_wall() -> None:
    text = (
        "Contenu de la fenêtre de consentement. Pour ce site, votre expérience "
        "est une priorité. Continuer sans accepter."
    )
    assert _looks_like_consent_wall(text)


def test_dismiss_consent_wall_prefers_continue_without_accepting() -> None:
    page = FakePage(
        "Nous utilisons des cookies. Vous pouvez Personnaliser, Accepter, "
        "ou Continuer sans accepter.",
        ["Accepter", "Personnaliser", "Continuer sans accepter"],
    )

    action = asyncio.run(_dismiss_consent_wall(page))

    assert action == "Continuer sans accepter"
    assert page.clicked == "Continuer sans accepter"


def test_dismiss_consent_wall_does_not_click_without_cmp_markers() -> None:
    page = FakePage(
        "Article normal avec un bouton Refuser une offre.",
        ["Refuser"],
    )

    action = asyncio.run(_dismiss_consent_wall(page))

    assert action is None
    assert page.clicked is None
