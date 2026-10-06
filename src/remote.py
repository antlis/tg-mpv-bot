"""Remote play API: ``POST /play`` starts a link on this machine's mpv.

For apps that want to send a video to the TV box (a "cast" button) without going through
Telegram. Off unless ``REMOTE_PLAY_TOKEN`` is set; it is the one place the bot listens for
connections (everything else is outbound polling), so it binds to localhost by default and
wants a token on every request. It does what a link sent in a chat does: the same
:func:`src.player.play_url`, so hooks, history and resume are the same.

    curl -H "Authorization: Bearer $REMOTE_PLAY_TOKEN" \\
         -d '{"url": "https://www.youtube.com/watch?v=...", "start": 83.5}' \\
         http://tv-box:8085/play
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
from typing import Any

from aiohttp import web

from src import player
from src.config import Settings

logger = logging.getLogger("tg-mpv-bot.remote")

MAX_URL_LEN = 2048
# Only web links: mpv also opens files, ``ytdl://`` and ``edl://`` urls, and takes options.
_URL_RE = re.compile(r"^https?://[^\s]+$")

SETTINGS_KEY = web.AppKey("settings", Settings)

# Casting twice at once would have two launches fighting over the single mpv.
_play_lock = asyncio.Lock()


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _authorized(request: web.Request, token: str) -> bool:
    header = request.headers.get("Authorization", "")
    scheme, _, given = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token.encode())


def parse_play_request(body: Any) -> tuple[str, float | None]:
    """The url and start position (seconds, or None) of a /play body; ValueError when invalid."""
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    url = body.get("url")
    if not isinstance(url, str) or len(url) > MAX_URL_LEN or not _URL_RE.match(url):
        raise ValueError("url must be an http(s) link")
    start = body.get("start")
    if start is None:
        return url, None
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not 0 <= start < 86400 * 7:
        raise ValueError("start must be a number of seconds")
    return url, float(start) if start > 0 else None


async def _play(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    if not _authorized(request, settings.remote_play_token):
        logger.warning("Rejected /play from %s: bad or missing token", request.remote)
        return _error(401, "unauthorized")
    try:
        url, start = parse_play_request(await request.json())
    except ValueError as exc:  # includes json.JSONDecodeError
        return _error(400, str(exc))
    logger.info("Remote play from %s: %s (start %s)", request.remote, url, start)
    async with _play_lock:
        try:
            title = await asyncio.to_thread(player.play_url, settings, url, None, start)
        except player.UrlPlaybackError as exc:
            return _error(422, str(exc))
        except Exception:
            logger.exception("Remote play failed")
            return _error(500, "playback failed")
    return web.json_response({"ok": True, "title": title})


def make_app(settings: Settings) -> web.Application:
    app = web.Application(client_max_size=4096)
    app[SETTINGS_KEY] = settings
    app.router.add_post("/play", _play)
    return app


async def start(settings: Settings) -> web.AppRunner | None:
    """Start the API when it is enabled; the runner (for cleanup) or None."""
    if not settings.remote_play_token:
        return None
    runner = web.AppRunner(make_app(settings))
    await runner.setup()
    host, _, port = settings.remote_play_bind.rpartition(":")
    await web.TCPSite(runner, host or "127.0.0.1", int(port)).start()
    logger.info("Remote play API on %s (POST /play, bearer token)", settings.remote_play_bind)
    return runner
