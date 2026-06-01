"""Direct JSON-IPC client for mpv over its Unix socket.

Replaces the previous bash → inline-python → socket indirection: the bot is
already Python, so it talks to ``--input-ipc-server`` directly. Pure I/O with
an injectable socket path, which makes it unit-testable against a fake server.

See: https://mpv.io/manual/master/#json-ipc
"""

from __future__ import annotations

import json
import socket
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

    def seek(self, seconds: float) -> None:
        self.command("seek", seconds)
        self.show_progress()

    def quit(self) -> None:
        self.command("quit")

    def playlist_next(self) -> None:
        self.command("playlist-next")
        self.show_text("${media-title}")  # mpv expands to the now-current title

    def playlist_prev(self) -> None:
        self.command("playlist-prev")
        self.show_text("${media-title}")

    def set_playlist_pos(self, index0: int) -> None:
        """Jump to a 0-based position in the current playlist."""
        self.set_property("playlist-pos", index0)
        self.show_text("${media-title}")

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
        self.show_text("Subtitle: ${sub}")

    def toggle_sub_visibility(self) -> None:
        self.command("cycle", "sub-visibility")
        self.show_text("Subtitles: ${sub-visibility}")

    def adjust_volume(self, delta: float, lo: float = 0, hi: float = 130) -> float:
        """Read volume, clamp ``current + delta`` to [lo, hi], write it back."""
        current = self.get_property("volume")
        new_vol = max(lo, min(hi, current + delta))
        self.set_property("volume", new_vol)
        self.show_text(f"Volume: {new_vol:.0f}")
        return new_vol
