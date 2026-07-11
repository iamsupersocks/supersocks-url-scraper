#!/usr/bin/env python3
"""Warm or inspect a persistent CloakBrowser profile for protected sites.

Use case: some sites present CAPTCHA/consent/session walls to ephemeral headless
browsers. A persistent profile lets an operator solve consent/login/challenge
once in a real/headful browser, then the reader can reuse that browser profile
with BROWSER_PROFILE_DIR.

Examples:
    # Headless diagnostic, no manual interaction:
    python scripts/browser_profile_probe.py \
      --url https://example.com/article \
      --profile-dir ./browser-profiles/default \
      --headless

    # Manual warm-up on an existing X11/VNC display:
    DISPLAY=:91 python scripts/browser_profile_probe.py \
      --url https://example.com/article \
      --profile-dir ./browser-profiles/default \
      --no-headless --wait-seconds 180

This script writes diagnostics only to explicit /tmp output paths by default.
Never commit browser profiles, screenshots, HTML dumps, cookies, or sessions.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supersocks_url_scraper.reader import extract_article  # noqa: E402


def _markers(html: str) -> dict[str, bool]:
    low = html.lower()
    return {
        "datadome": "datadome" in low or "captcha-delivery.com" in low,
        "captcha": "captcha" in low,
        "articlebody": "articlebody" in low,
        "paywall": "paywall" in low or "abonne" in low or "subscriber" in low,
        "cookie_consent": "cookie" in low and ("consent" in low or "privacy" in low),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Warm/inspect a persistent CloakBrowser profile.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--no-headless", action="store_true", default=False)
    parser.add_argument("--wait-seconds", type=float, default=15.0)
    parser.add_argument("--screenshot", default="/tmp/supersocks-browser-profile-probe.png")
    parser.add_argument("--html-out", default="/tmp/supersocks-browser-profile-probe.html")
    args = parser.parse_args()

    headless = args.headless and not args.no_headless
    profile_dir = str(Path(args.profile_dir).expanduser())
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    try:
        from cloakbrowser import ensure_binary, launch_persistent_context_async
    except ImportError as exc:
        raise SystemExit("cloakbrowser is not installed. Install with: pip install 'supersocks-url-scraper[browser]'") from exc

    await asyncio.to_thread(ensure_binary)
    context = await launch_persistent_context_async(
        profile_dir,
        headless=headless,
        locale="fr-FR",
        timezone="Europe/Paris",
        humanize=True,
        stealth_args=True,
        viewport={"width": 1366, "height": 768},
    )
    try:
        page = await context.new_page()
        response = await page.goto(args.url, wait_until="domcontentloaded", timeout=90_000)
        if args.wait_seconds > 0:
            print(f"waiting_seconds={args.wait_seconds}")
            print("if headful, solve/login/consent in the browser now")
            await page.wait_for_timeout(int(args.wait_seconds * 1000))
        html = await page.content()
        Path(args.html_out).write_text(html, encoding="utf-8")
        try:
            await page.screenshot(path=args.screenshot, full_page=True)
        except Exception as exc:
            print(f"screenshot_error={exc!r}")
        visible = " ".join(html.split())
        article = extract_article(html, args.url)
        print(f"profile_dir={profile_dir}")
        print(f"status={response.status if response else 0}")
        print(f"final_url={page.url}")
        print(f"title={await page.title()!r}")
        print(f"html_len={len(html)} visible_rough_len={len(visible)}")
        print(f"markers={_markers(html)}")
        print(f"article_method={article.method} article_title={article.title!r} article_text_len={len(article.text)}")
        print(f"article_sample={article.text[:500]!r}")
        print(f"html_out={args.html_out}")
        print(f"screenshot={args.screenshot}")
        return 0
    finally:
        await context.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
