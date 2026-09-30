"""Headless-browser fallback for pages whose player builds the media URL in
JavaScript (ported from tg-media-bot).

yt-dlp only parses the static HTML a page ships. Some sites expose no stream
there — the player fetches the media URL at runtime, often only after the
viewer clicks play or picks a source. This loads such a page in headless
Chromium, clicks play/source controls across the page and nested iframes,
watches network traffic for the media request, and returns that URL for mpv.

Playwright + Chromium are optional: without them the resolver quietly returns
``None`` and the caller reports the original failure. The Docker image bakes
Chromium in with ``--build-arg INSTALL_BROWSER=true``.
"""

from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Requests that carry (or point at) playable media.
_MEDIA_RE = re.compile(
    r"\.(m3u8|mpd|mp4|m4s|ts|webm)(\?|$)|/get_file/|videoplayback|mediadelivery|videodelivery",
    re.I,
)
# Junk we never want even if it matches: previews, trailers, thumbs, ads.
_JUNK_RE = re.compile(r"preview|trailer|sample|/thumb|_tr\.|sprite|/ads?/", re.I)

# Common cross-site controls that make a player request its stream (or that
# reveal the real player iframe after a "server"/"source" tab is activated).
_CLICK_SELECTORS = (
    ".vjs-big-play-button", ".jw-icon-display", ".jwplayer",
    ".plyr__control--overlaid", "[aria-label='Play']", "button[title='Play']",
    ".play-button", ".btn-play", ".play", "#player", "video",
    ".server-item", ".server", "[data-server-id]", ".servers .item",
    ".item.server", ".source-item",
)

_ROUNDS = 5  # click-wait-recheck rounds before giving up

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _score(url: str) -> int:
    """Rank captured URLs: manifests beat progressive files; junk sinks."""
    s = 0
    if re.search(r"\.(m3u8|mpd)(\?|$)", url, re.I):
        s += 100  # a manifest lets the player choose the best quality itself
    elif re.search(r"\.(mp4|webm|m4s|ts)(\?|$)|/get_file/", url, re.I):
        s += 50
    if _JUNK_RE.search(url):
        s -= 200
    return s


def pick_best(captured: dict[str, str]) -> tuple[str, str] | None:
    """Best ``(media_url, referer)`` from ``{url: referer}``, or None."""
    candidates = [u for u in captured if _score(u) > 0]
    if not candidates:
        return None
    best = max(candidates, key=_score)
    return best, captured[best]


async def _poke(page) -> None:
    """Best-effort clicks on play/source controls in the page and every frame."""
    for frame in page.frames:
        for sel in _CLICK_SELECTORS:
            try:
                els = await frame.query_selector_all(sel)
            except Exception:  # noqa: BLE001
                continue
            for el in els[:3]:
                try:
                    await el.click(timeout=1500, force=True)
                    await page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001
                    pass


async def _resolve_async(page_url: str, timeout: int) -> tuple[str, str] | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping headless-browser fallback")
        return None

    captured: dict[str, str] = {}  # media url -> referer

    def _on_request(r) -> None:
        if _MEDIA_RE.search(r.url) and r.url not in captured:
            captured[r.url] = r.headers.get("referer") or page_url

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                user_agent=_UA, viewport={"width": 1280, "height": 720}, locale="en-US"
            )
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await ctx.new_page()
            page.on("request", _on_request)
            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as exc:  # noqa: BLE001 — we may still have caught media
                logger.info("Headless-browser navigation issue (continuing): %s", exc)
            await page.wait_for_timeout(4000)  # let the initial player scripts settle
            for _ in range(_ROUNDS):
                if any(_score(u) > 0 for u in captured):
                    break
                await _poke(page)
                await page.wait_for_timeout(3000)
            await browser.close()
    except Exception as exc:  # noqa: BLE001 — missing Chromium etc.
        logger.warning("Headless-browser fallback failed to run: %s", exc)
        return None
    return pick_best(captured)


def resolve_media_url(page_url: str, timeout: int = 45) -> tuple[str, str] | None:
    """Blocking: ``(media_url, referer)`` sniffed from the page's player, or None."""
    return asyncio.run(_resolve_async(page_url, timeout))
