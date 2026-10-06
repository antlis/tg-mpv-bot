"""Remote play API: ``POST /play``, ``GET /status`` and ``POST /ctl``.

For apps that want to send a video to the TV box (a "cast" button), or drive the
player without going through Telegram. Off unless ``REMOTE_PLAY_TOKEN`` is set;
it is the one place the bot listens for connections (everything else is outbound
polling), so it binds to localhost by default and wants a token on every request.

``/play`` does what a link sent in a chat does: the same :func:`src.player.play_url`,
so hooks, history and resume are the same. ``/ctl`` and ``/status`` talk to mpv's
IPC socket through :class:`src.mpv_ipc.MpvClient`, sharing the panel's
:data:`~src.mpv_ipc.CTL_ACTIONS` table.

    curl -H "Authorization: Bearer $REMOTE_PLAY_TOKEN" \\
         -d '{"url": "https://www.youtube.com/watch?v=...", "start": 83.5}' \\
         http://tv-box:8085/play
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from src import player, remote_queue
from src.config import Settings
from src.mpv_ipc import CTL_ACTIONS, MpvClient, MpvError, MpvNotRunning

logger = logging.getLogger("tg-mpv-bot.remote")

MAX_URL_LEN = 2048
# Only web links: mpv also opens files, ``ytdl://`` and ``edl://`` urls, and takes options.
_URL_RE = re.compile(r"^https?://[^\s]+$")

SETTINGS_KEY = web.AppKey("settings", Settings)

# Casting twice at once would have two launches fighting over the single mpv.
_play_lock = remote_queue.play_lock


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _authorized(request: web.Request, token: str) -> bool:
    header = request.headers.get("Authorization", "")
    scheme, _, given = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token.encode())


@web.middleware
async def _require_token(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """Every route is bearer-token gated; 401 without it, before anything runs."""
    settings = request.app[SETTINGS_KEY]
    if not _authorized(request, settings.remote_play_token):
        logger.warning(
            "Rejected %s %s from %s: bad or missing token", request.method, request.path, request.remote
        )
        return _error(401, "unauthorized")
    return await handler(request)


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


def _is_seconds(value: Any) -> bool:
    """A number of seconds in 0..7 days (bools are not numbers here)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 86400 * 7
    )


def parse_queue_request(body: Any) -> tuple[list[str], int, float | None]:
    """The links, the index to start at and the start position (seconds, None) of a ``urls`` body."""
    if not isinstance(body, dict):
        raise ValueError("expected JSON object")
    urls = body.get("urls")
    if not isinstance(urls, list) or not 0 < len(urls) <= remote_queue.MAX_ITEMS:
        raise ValueError(f"urls must be a list of 1 to {remote_queue.MAX_ITEMS} links")
    for url in urls:
        parse_play_request({"url": url})  # each one an http(s) link
    index = body.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(urls):
        raise ValueError("index must be a position in urls")
    start = parse_play_request({"url": urls[index], "start": body.get("start")})[1]
    return urls, index, start


def _client(settings: Settings) -> MpvClient:
    return MpvClient(settings.mpv_socket)


async def _play(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    try:
        body = await request.json()
        if isinstance(body, dict) and "urls" in body:
            return await _play_queue(request, settings, *parse_queue_request(body))
        url, start = parse_play_request(body)
    except ValueError as exc:  # includes json.JSONDecodeError
        return _error(400, str(exc))
    logger.info("Remote play from %s: %s (start %s)", request.remote, url, start)
    remote_queue.cancel()  # a single link replaces a queue
    async with _play_lock:
        try:
            title = await asyncio.to_thread(player.play_url, settings, url, None, start)
        except player.UrlPlaybackError as exc:
            return _error(422, str(exc))
        except Exception:
            logger.exception("Remote play failed")
            return _error(500, "playback failed")
    return web.json_response({"ok": True, "title": title})


async def _play_queue(
    request: web.Request, settings: Settings, urls: list[str], index: int, start: float | None
) -> web.Response:
    logger.info("Remote queue from %s: %d links from #%d (start %s)", request.remote, len(urls), index, start)
    try:
        title, _ = await remote_queue.start(settings, urls, index, start)
    except player.UrlPlaybackError as exc:
        return _error(422, str(exc))
    except Exception:
        logger.exception("Remote queue failed")
        return _error(500, "playback failed")
    return web.json_response({"ok": True, "title": title, "queued": len(urls) - index})


async def _status(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    queue = remote_queue.current()
    try:
        status = await asyncio.to_thread(_client(settings).read_status)
    except MpvNotRunning:
        if queue is None:
            return _error(503, "mpv is not running")
        # Between two items of a queue: still going, the next one is starting.
        return web.json_response({"ok": True, "playing": False, "title": queue.title, "queue": queue.snapshot()})
    except Exception:
        logger.exception("Remote status failed")
        return _error(500, "status failed")
    return web.json_response({"ok": True, **status, "queue": queue.snapshot() if queue else None})


async def _ctl(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "expected a JSON object")
    if not isinstance(body, dict):
        return _error(400, "expected a JSON object")
    action = body.get("action")
    if action == "seek":  # takes an argument, so it is not in the parameterless panel table
        position = body.get("position")
        if not _is_seconds(position):
            return _error(400, "position must be a number of seconds")

        def run(c: MpvClient) -> None:
            c.seek_absolute(float(position))

    elif isinstance(action, str) and action in CTL_ACTIONS:
        run = CTL_ACTIONS[action]
    else:
        valid = ", ".join(sorted([*CTL_ACTIONS, "seek"]))
        return _error(400, f"action must be one of: {valid}")
    if action == "stop":
        remote_queue.cancel()  # stopping is not the end of one item
    logger.info("Remote ctl from %s: %s", request.remote, action)
    try:
        await asyncio.to_thread(run, _client(settings))
    except MpvNotRunning:
        return _error(503, "mpv is not running")
    except MpvError as exc:
        return _error(422, str(exc))
    except Exception:
        logger.exception("Remote ctl failed")
        return _error(500, "control failed")
    return web.json_response({"ok": True, "action": action})


def make_app(settings: Settings) -> web.Application:
    app = web.Application(client_max_size=remote_queue.MAX_ITEMS * (MAX_URL_LEN + 16), middlewares=[_require_token])
    app[SETTINGS_KEY] = settings
    app.router.add_post("/play", _play)
    app.router.add_get("/status", _status)
    app.router.add_post("/ctl", _ctl)
    return app


async def start(settings: Settings) -> web.AppRunner | None:
    """Start the API when it is enabled; the runner (for cleanup) or None."""
    if not settings.remote_play_token:
        return None
    runner = web.AppRunner(make_app(settings))
    await runner.setup()
    host, _, port = settings.remote_play_bind.rpartition(":")
    await web.TCPSite(runner, host or "127.0.0.1", int(port)).start()
    logger.info(
        "Remote play API on %s (POST /play, GET /status, POST /ctl, bearer token)",
        settings.remote_play_bind,
    )
    return runner
