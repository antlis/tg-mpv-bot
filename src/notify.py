"""End-of-playback notifications via mpv's IPC event stream.

A single long-lived connection to the mpv socket (mpv broadcasts events to
every IPC client) feeds a small state machine; the bot messages the chat
that last started playback when an episode advances or playback finishes.

Event semantics that make this work:
- a *natural* end of a file emits ``end-file`` with ``reason: "eof"``;
- user navigation (next/prev/jump) emits ``reason: "stop"`` and our own
  relaunch path quits mpv with ``reason: "quit"`` — both stay silent;
- when the whole playlist is done, mpv exits and the socket closes right
  after the final eof — so "eof then disconnect" means *finished*, while
  "eof then start-file" means *advanced to the next episode*.
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot

from . import state
from .config import Settings
from .mpv_ipc import MpvClient

logger = logging.getLogger(__name__)

RECONNECT_DELAY = 3.0  # mpv is down most of the time; connect attempts are cheap
CHECKPOINT_EVERY = 15.0  # seconds between position checkpoints (stream resume)


class PlaybackMonitor:
    """Pure event→action core (unit-testable, no I/O).

    Feed mpv event dicts to :meth:`on_event` and call :meth:`on_disconnect`
    when the socket closes; they return ``"advanced"`` / ``"finished"`` /
    ``"error"`` / ``None``.
    """

    def __init__(self) -> None:
        self._eof = False

    def on_event(self, msg: dict) -> str | None:
        event = msg.get("event")
        if event == "end-file":
            reason = msg.get("reason")
            self._eof = reason == "eof"
            if reason == "error":
                return "error"
        elif event == "start-file":
            if self._eof:
                self._eof = False
                return "advanced"
        return None

    def on_disconnect(self) -> str | None:
        if self._eof:
            self._eof = False
            return "finished"
        return None


async def _send(bot: Bot, settings: Settings, text: str) -> None:
    if not state.notify_enabled(settings.state_file):
        return
    chat = state.notify_chat(settings.state_file)
    if chat is None:
        return
    try:
        await bot.send_message(chat, text)
    except Exception:  # noqa: BLE001 — notifications must never kill the listener
        logger.exception("Failed to send playback notification")


async def _now_playing(settings: Settings, attempts: int = 6) -> str | None:
    """Title (+ position) of the new file; retries while mpv still loads it."""
    def query() -> str | None:
        client = MpvClient(settings.mpv_socket)
        title = client._safe_get("media-title") or client._safe_get("filename")
        if not title:
            return None
        pos, cnt = client._safe_get("playlist-pos-1"), client._safe_get("playlist-count")
        if isinstance(pos, int) and isinstance(cnt, int) and pos > 0 and cnt > 1:
            return f"{pos}/{cnt} — {title}"
        return str(title)

    for _ in range(attempts):
        try:
            if label := await asyncio.to_thread(query):
                return label
        except Exception:  # noqa: BLE001 — mpv may be mid-restart
            pass
        await asyncio.sleep(1.0)
    return None


async def _checkpoint_position(settings: Settings) -> None:
    """Save the current position onto the newest history entry (stream resume)."""
    try:
        pos = await asyncio.to_thread(
            lambda: MpvClient(settings.mpv_socket)._safe_get("time-pos")
        )
    except Exception:  # noqa: BLE001 — mpv may be mid-restart
        return
    if isinstance(pos, (int, float)) and pos > 0:
        state.update_position(settings.state_file, pos)


async def run(bot: Bot, settings: Settings) -> None:
    """Listen to mpv events forever (started as a task from bot.py)."""
    while True:
        try:
            reader, writer = await asyncio.open_unix_connection(settings.mpv_socket)
        except OSError:
            await asyncio.sleep(RECONNECT_DELAY)
            continue
        logger.info("Notification listener attached to mpv")
        monitor = PlaybackMonitor()
        # Whatever is on right now feeds the eventual "Finished" message
        # (events buffer in the socket while we look).
        last_title: str | None = await _now_playing(settings, attempts=2)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(), timeout=CHECKPOINT_EVERY
                    )
                except TimeoutError:
                    # quiet stretch = steady playback — checkpoint the position
                    await _checkpoint_position(settings)
                    continue
                if not line:  # mpv exited
                    if monitor.on_disconnect() == "finished":
                        state.update_position(settings.state_file, 0)  # start over next time
                        await _send(bot, settings, f"✅ Finished: {last_title or 'playback'}")
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                action = monitor.on_event(msg)
                if action == "advanced":
                    label = await _now_playing(settings)
                    if label:
                        last_title = label.rsplit("— ", 1)[-1]
                        await _send(bot, settings, f"⏭ Now playing: {label}")
                elif action == "error":
                    await _send(
                        bot, settings,
                        f"⚠️ Playback error{f' after: {last_title}' if last_title else ''}",
                    )
                elif msg.get("event") == "file-loaded" and last_title is None:
                    # remember what's on for the eventual "Finished" message
                    last_title = await _now_playing(settings, attempts=2)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
        await asyncio.sleep(1.0)
