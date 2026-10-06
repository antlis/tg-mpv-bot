"""Direct JSON-IPC client for mpv over its Unix socket.

Replaces the previous bash → inline-python → socket indirection: the bot is
already Python, so it talks to ``--input-ipc-server`` directly. Pure I/O with
an injectable socket path, which makes it unit-testable against a fake server.

See: https://mpv.io/manual/master/#json-ipc
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any


class MpvNotRunning(Exception):
    """Raised when the mpv IPC socket cannot be reached."""


class MpvError(Exception):
    """Raised when mpv replies with a non-success error to a command."""


class MpvClient:
    """Thin synchronous client around a single mpv IPC socket.

    Each call opens a short-lived connection — mpv handles one command per
    request and we never need a persistent event stream here.
    """

    def __init__(self, socket_path: str, timeout: float = 2.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    # ── low level ────────────────────────────────────────────────────
    def command(self, *args: Any) -> Any:
        """Send a command (e.g. ``("set_property", "pause", True)``).

        Returns the ``data`` field of the reply (often ``None``). Raises
        :class:`MpvNotRunning` if the socket is dead, :class:`MpvError` if mpv
        reports failure (e.g. querying a property while nothing is playing).
        """
        request_id = 1
        payload = json.dumps({"command": list(args), "request_id": request_id})

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            raise MpvNotRunning("mpv is not running") from exc

        try:
            sock.sendall(payload.encode() + b"\n")
            reply = self._read_reply(sock, request_id)
        finally:
            sock.close()

        if reply.get("error") != "success":
            raise MpvError(reply.get("error", "unknown error"))
        return reply.get("data")

    @staticmethod
    def _read_reply(sock: socket.socket, request_id: int) -> dict:
        """Read newline-delimited JSON until the matching reply arrives.

        mpv interleaves async ``event`` messages with command replies; we skip
        events and return the object carrying our ``request_id``.
        """
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise MpvError("connection closed before reply")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line.decode())
                if msg.get("request_id") == request_id and "error" in msg:
                    return msg

    # ── high level helpers ───────────────────────────────────────────
    def get_property(self, name: str) -> Any:
        return self.command("get_property", name)

    def _safe_get(self, name: str) -> Any:
        try:
            return self.get_property(name)
        except MpvError:
            return None

    def read_status(self) -> dict[str, Any]:
        """Now-playing as JSON-friendly data (the remote API's ``GET /status``).

        Raises :class:`MpvNotRunning` when the socket is dead; every field is
        always present, with ``None`` where mpv has no value yet. ``playing`` is
        False when mpv is up but nothing is loaded — then the fields that only
        exist during playback are None too.
        """
        title = self._safe_get("media-title") or self._safe_get("filename")
        icy = self._safe_get("metadata/icy-title")
        return {
            "playing": bool(title),
            "title": title,
            "icy": None if not icy or icy == title else icy,
            "path": self._safe_get("path"),
            "position": self._safe_get("time-pos"),
            "duration": self._safe_get("duration"),
            "percent": self._safe_get("percent-pos"),
            "paused": self._safe_get("pause"),
            "volume": self._safe_get("volume"),
            "mute": self._safe_get("mute"),
            "speed": self._safe_get("speed"),
            "playlist_pos": self._safe_get("playlist-pos"),
            "playlist_count": self._safe_get("playlist-count"),
            "loop": self._safe_get("loop-playlist") not in (False, "no", None),
        }

    def _track_label(self) -> str:
        """Reliable playlist-position label (title isn't loaded yet right after a switch)."""
        pos = self._safe_get("playlist-pos-1")
        cnt = self._safe_get("playlist-count")
        if isinstance(pos, int) and isinstance(cnt, int) and pos > 0 and cnt > 1:
            return f"Track {pos}/{cnt}"
        return "Playing"

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)

    # IPC commands change state silently — mpv only shows its on-screen display
    # for keyboard input. So each user-facing action also pushes an OSD message
    # (best-effort; never let OSD failure mask a successful action).
    def show_text(self, text: str, duration_ms: int = 1500) -> None:
        try:
            self.command("show-text", text, duration_ms)
        except MpvError:
            pass

    def show_progress(self) -> None:
        try:
            self.command("show-progress")  # seek bar + time/duration
        except MpvError:
            pass

    def set_pause(self, paused: bool) -> None:
        self.set_property("pause", paused)
        self.show_text("Paused" if paused else "Playing")

    def toggle_pause(self) -> bool:
        """Flip pause state; returns the new value (True = now paused)."""
        self.command("cycle", "pause")
        paused = bool(self.get_property("pause"))
        self.show_text("Paused" if paused else "Playing")
        return paused

    def cycle_mute(self) -> None:
        self.command("cycle", "mute")
        self.show_text("Muted" if self.get_property("mute") else "Unmuted")

    def set_mute(self, muted: bool) -> None:
        self.set_property("mute", muted)
        self.show_text("Muted" if muted else "Unmuted")

    def seek(self, seconds: float) -> None:
        self.command("seek", seconds)
        self.show_progress()

    def seek_absolute(self, seconds: float) -> None:
        """Seek to an absolute position in the current file."""
        self.command("seek", max(0.0, seconds), "absolute")
        self.show_progress()

    def seek_percent(self, percent: float) -> None:
        """Seek to a percentage of the current file (clamped to 0–100)."""
        self.command("seek", max(0.0, min(100.0, percent)), "absolute-percent")
        self.show_progress()

    def quit(self) -> None:
        self.command("quit")

    def playlist_next(self) -> None:
        self.command("playlist-next")
        self.show_text(self._track_label())

    def playlist_prev(self) -> None:
        self.command("playlist-prev")
        self.show_text(self._track_label())

    def screenshot_to_file(self, path: str, include_subs: bool = True) -> None:
        """Save the current frame to ``path``.

        ``subtitles`` renders the frame as seen (with subs); ``video`` is the
        raw frame. Note the command may complete asynchronously in newer mpv —
        callers should wait for the file to appear.
        """
        self.command("screenshot-to-file", path, "subtitles" if include_subs else "video")

    def get_playlist(self) -> list[dict]:
        """The current playlist: ``{filename, current?, title?}`` dicts."""
        items = self.get_property("playlist")
        return items if isinstance(items, list) else []

    def set_playlist_pos(self, index0: int) -> None:
        """Jump to a 0-based position in the current playlist."""
        self.set_property("playlist-pos", index0)
        self.show_text(self._track_label())

    def shuffle(self) -> None:
        self.command("playlist-shuffle")
        self.show_text("Shuffled")

    def toggle_loop(self) -> bool:
        """Toggle looping the whole playlist; returns True if now looping."""
        looping = self.get_property("loop-playlist") not in (False, "no", None)
        self.set_property("loop-playlist", "no" if looping else "inf")
        now = not looping
        self.show_text("Loop: on" if now else "Loop: off")
        return now

    def cycle_sub(self) -> None:
        """Switch to the next subtitle track (cycles through tracks and 'no')."""
        self.command("cycle", "sub")
        sid = self._safe_get("sid")
        if not sid:
            self.show_text("Subtitle: off")
        else:
            label = (
                self._safe_get("current-tracks/sub/title")
                or self._safe_get("current-tracks/sub/lang")
                or f"#{sid}"
            )
            self.show_text(f"Subtitle: {label}")

    def toggle_sub_visibility(self) -> None:
        self.command("cycle", "sub-visibility")
        self.show_text("Subtitles: on" if self._safe_get("sub-visibility") else "Subtitles: off")

    def cycle_audio(self) -> None:
        """Switch to the next audio track (e.g. Spanish → English)."""
        self.command("cycle", "aid")
        aid = self._safe_get("aid")
        if not aid:
            self.show_text("Audio: off")
        else:
            label = (
                self._safe_get("current-tracks/audio/title")
                or self._safe_get("current-tracks/audio/lang")
                or f"#{aid}"
            )
            self.show_text(f"Audio: {label}")

    def set_speed(self, speed: float, lo: float = 0.1, hi: float = 5.0) -> float:
        """Set playback speed (clamped); returns the value actually set."""
        speed = max(lo, min(hi, speed))
        self.set_property("speed", speed)
        self.show_text(f"Speed: {speed:g}x")
        return speed

    def get_chapters(self) -> tuple[list[dict], int | None]:
        """``(chapter-list, current chapter index)``; ``([], None)`` if none."""
        chapters = self._safe_get("chapter-list")
        if not isinstance(chapters, list) or not chapters:
            return [], None
        current = self._safe_get("chapter")
        return chapters, current if isinstance(current, int) else None

    def set_chapter(self, index0: int) -> None:
        self.set_property("chapter", index0)
        self.show_text(self._safe_get("chapter-metadata/title") or f"Chapter {index0 + 1}")

    def toggle_night(self) -> bool:
        """Toggle loudness normalization (night mode); True = now on.

        Adds/removes a labelled ``loudnorm`` audio filter: quiet dialogue up,
        explosions down — so late-night viewing doesn't wake the house.
        """
        filters = self._safe_get("af") or []
        is_on = any(
            isinstance(f, dict) and f.get("label") == "night" for f in filters
        )
        if is_on:
            self.command("af", "remove", "@night")
            self.show_text("Night mode: off")
            return False
        self.command("af", "add", "@night:loudnorm=I=-24:LRA=7:TP=-2")
        self.show_text("Night mode: on")
        return True

    def adjust_volume(self, delta: float, lo: float = 0, hi: float = 130) -> float:
        """Read volume, clamp ``current + delta`` to [lo, hi], write it back."""
        current = self.get_property("volume")
        new_vol = max(lo, min(hi, current + delta))
        self.set_property("volume", new_vol)
        self.show_text(f"Volume: {new_vol:.0f}")
        return new_vol


# One name → client-call table, shared by the Telegram now-playing panel
# (``ctl:<action>`` callbacks) and the remote API's ``POST /ctl``: a control
# added here is callable from both without a second mapping to keep in sync.
# ``refresh`` re-renders the panel and is a no-op for the API.
CTL_ACTIONS: dict[str, Callable[[MpvClient], Any]] = {
    "toggle": lambda c: c.toggle_pause(),
    "pause": lambda c: c.set_pause(True),
    "resume": lambda c: c.set_pause(False),
    "back": lambda c: c.seek(-10),
    "fwd": lambda c: c.seek(30),
    "prev": lambda c: c.playlist_prev(),
    "next": lambda c: c.playlist_next(),
    "volup": lambda c: c.adjust_volume(10),
    "voldown": lambda c: c.adjust_volume(-10),
    "mute": lambda c: c.cycle_mute(),
    "unmute": lambda c: c.set_mute(False),
    "sub": lambda c: c.cycle_sub(),
    "audio": lambda c: c.cycle_audio(),
    "p0": lambda c: c.seek_percent(0),
    "p25": lambda c: c.seek_percent(25),
    "p50": lambda c: c.seek_percent(50),
    "p75": lambda c: c.seek_percent(75),
    "shuffle": lambda c: c.shuffle(),
    "loop": lambda c: c.toggle_loop(),
    "stop": lambda c: c.quit(),
    "refresh": lambda c: None,
}
