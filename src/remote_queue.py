"""A queue behind the remote API: ``POST /play`` with ``urls`` plays them one after another.

The bot streams one link per mpv (yt-dlp pipes into it), so there is no mpv playlist to
lean on: this module starts the next link when mpv exits at the end of a video. It is not
a second player, only a watcher around :func:`src.player.play_url`, so every item gets the
same hooks, history and resume as a link sent in chat.

The queue gives way to anything else: another launch (a chat link, ``/play``, a new
queue) or an mpv that went away before the video's end (``/quit``, ``stop``) ends it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src import mpv_ipc, player
from src.config import Settings
from src.mpv_ipc import MpvClient, MpvError, MpvNotRunning

logger = logging.getLogger("tg-mpv-bot.queue")

MAX_ITEMS = 200  # links in one request
MAX_TOTAL = 2000  # links one queue may hold, however many requests filled it
POLL = 1.0  # seconds between questions to mpv
START_GRACE = 45.0  # how long a launched video may take to show up in mpv
NEAR_END = 5.0  # a video that vanished this close to its end finished, it was not stopped

# Two launches at once would fight over the single mpv: /play and the queue share this.
play_lock = asyncio.Lock()

_current: PlayQueue | None = None


class PlayQueue:
    def __init__(self, settings: Settings, urls: list[str], index: int) -> None:
        self.settings = settings
        self.urls = urls
        self.pos = index
        self.title: str | None = None
        self.error: str | None = None
        self._skip: int | None = None
        self._wake = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"position": self.pos, "count": len(self.urls), "error": self.error}

    def append(self, urls: list[str]) -> int:
        """Add links to the end (on the event loop); the new length. ValueError past ``MAX_TOTAL``."""
        if len(self.urls) + len(urls) > MAX_TOTAL:
            raise ValueError(f"a queue holds at most {MAX_TOTAL} links")
        self.urls.extend(urls)
        return len(self.urls)

    def skip(self, delta: int) -> None:
        """Move ``delta`` items along (from any thread); the end of the list is not left."""
        self._loop.call_soon_threadsafe(self._set_skip, delta)

    def _set_skip(self, delta: int) -> None:
        self._skip = (self._skip or 0) + delta
        self._wake.set()

    def cancel(self) -> None:
        if self._task:
            self._task.cancel()

    async def _launch(self, start: float | None) -> int:
        """Start the current item; the player epoch it started in. Raises UrlPlaybackError."""
        async with play_lock:
            self.title = await asyncio.to_thread(player.play_url, self.settings, self.urls[self.pos], None, start)
            return player.playback_epoch()

    async def begin(self, start: float | None) -> str:
        """Play the first item now (so its failure reaches the caller), then watch in the background."""
        epoch = await self._launch(start)
        self._task = asyncio.create_task(self._run(epoch))
        return self.title or self.urls[self.pos]

    async def _run(self, epoch: int) -> None:
        global _current
        try:
            while True:
                outcome = await self._watch(epoch)
                if outcome == "gone":
                    break
                # "ended" and "failed" go on to the next item, a skip by as many as it says.
                self.pos += 1 if outcome in ("ended", "failed") else int(outcome)
                # A bad item is passed over.
                while True:
                    if self.pos >= len(self.urls):
                        return
                    try:
                        epoch = await self._launch(None)
                        break
                    except player.UrlPlaybackError as exc:
                        self.error = str(exc)
                        logger.warning("Queue item %d skipped: %s", self.pos, exc)
                        self.pos += 1
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Queue stopped")
        finally:
            if _current is self:
                _current = None

    async def _watch(self, epoch: int) -> str:
        """Wait out the item playing in ``epoch``: "ended", "failed", "gone" or a skip as a number."""
        client = MpvClient(self.settings.mpv_socket)
        started = time.monotonic()
        last: dict[str, Any] | None = None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), POLL)
            except TimeoutError:
                pass
            if self._skip:
                delta, self._skip = self._skip, None
                self._wake.clear()
                if 0 <= self.pos + delta < len(self.urls) and delta:
                    return str(delta)
                continue
            self._wake.clear()
            if player.playback_epoch() != epoch:
                return "gone"  # something else was started
            try:
                status = await asyncio.to_thread(client.read_status)
            except MpvNotRunning:
                if last is not None:
                    near = last["duration"] and last["position"] >= last["duration"] - NEAR_END
                    return "ended" if near else "gone"
                if time.monotonic() - started > START_GRACE:
                    self.error = "the video did not start"
                    return "failed"
                continue
            except (MpvError, OSError):
                continue
            if status.get("playing") and status.get("position") is not None:
                last = {"position": status["position"], "duration": status.get("duration") or 0}


async def start(settings: Settings, urls: list[str], index: int, start_at: float | None) -> tuple[str, PlayQueue]:
    """Replace any queue with ``urls``, playing from ``index``; the title of the first item."""
    global _current
    if _current:
        _current.cancel()
        _current = None
    queue = PlayQueue(settings, urls, index)
    title = await queue.begin(start_at)
    _current = queue
    return title, queue


def current() -> PlayQueue | None:
    return _current


def cancel() -> None:
    global _current
    if _current:
        _current.cancel()
        _current = None


def skip(delta: int) -> bool:
    """next / previous inside the queue, when there is one; False = nothing was done."""
    if _current is None:
        return False
    _current.skip(delta)
    return True


mpv_ipc.queue_skip = skip
