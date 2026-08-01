from __future__ import annotations

import asyncio

from supersocks_url_scraper.browser_fetcher import click_safe_consent_control


class FakeLocator:
    def __init__(self, nodes: list[dict[str, object]]):
        self.nodes = nodes

    async def count(self) -> int:
        return len(self.nodes)

    def nth(self, index: int) -> "FakeNode":
        return FakeNode(self.nodes[index])


class FakeNode:
    def __init__(self, node: dict[str, object]):
        self.node = node

    async def is_visible(self) -> bool:
        return bool(self.node.get("visible", True))

    async def inner_text(self, timeout: int = 0) -> str:
        return str(self.node.get("text", ""))

    def locator(self, selector: str) -> FakeLocator:
        buttons = self.node.get("buttons", [])
        return FakeLocator(buttons if isinstance(buttons, list) else [])

    async def click(self, timeout: int = 0) -> None:
        self.node["clicked"] = True


class FakePage:
    def __init__(self, dialogs: list[dict[str, object]]):
        self.dialogs = dialogs

    def locator(self, selector: str) -> FakeLocator:
        if selector == "[role='dialog']":
            return FakeLocator(self.dialogs)
        return FakeLocator([])


def test_click_safe_consent_control_clicks_explicit_reject_only() -> None:
    reject = {"text": "Tout refuser"}
    accept = {"text": "Accept all"}
    page = FakePage([{"text": "Cookie consent preferences", "buttons": [accept, reject]}])

    clicked = asyncio.run(click_safe_consent_control(page))

    assert clicked == "Tout refuser"
    assert reject["clicked"] is True
    assert "clicked" not in accept


def test_click_safe_consent_control_ignores_generic_or_accept_buttons() -> None:
    ok = {"text": "OK"}
    accept = {"text": "Accept all"}
    page = FakePage([{"text": "Cookie consent preferences", "buttons": [ok, accept]}])

    clicked = asyncio.run(click_safe_consent_control(page))

    assert clicked is None
    assert "clicked" not in ok
    assert "clicked" not in accept


def test_click_safe_consent_control_requires_recognized_window_text() -> None:
    reject = {"text": "Reject all"}
    page = FakePage([{"text": "Newsletter signup", "buttons": [reject]}])

    clicked = asyncio.run(click_safe_consent_control(page))

    assert clicked is None
    assert "clicked" not in reject


def test_click_safe_consent_control_supports_privacy_container() -> None:
    class PrivacyPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            if selector == "[id*='privacy' i]":
                return FakeLocator(self.dialogs)
            return super().locator(selector)

    reject = {"text": "Continuer sans accepter"}
    page = PrivacyPage([{"text": "Privacy preferences", "buttons": [reject]}])

    clicked = asyncio.run(click_safe_consent_control(page))

    assert clicked == "Continuer sans accepter"
    assert reject["clicked"] is True
