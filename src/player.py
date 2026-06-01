"""Launching mpv — the one part that genuinely needs the shell/subprocess.

Everything else (pause/seek/volume/info) goes straight to the IPC socket via
:mod:`src.mpv_ipc`. Here we kill any running mpv, optionally switch the i3
workspace, and start a detached mpv on the chosen playlist.

Binaries are resolved to absolute paths and a sane PATH is handed to the child
process: under a systemd user service the inherited PATH can be minimal (it
may not contain ``/usr/bin`` at all), so relying on it would break ``pkill`` /
``mpv`` / ``i3-msg`` lookups.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import Settings

logger = logging.getLogger(__name__)

_SEARCH_DIRS = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]


def _which(name: str) -> str | None:
    """Locate a binary, falling back to standard dirs when PATH is minimal."""
    found = shutil.which(name)
    if found:
        return found
    for d in _SEARCH_DIRS:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    return None


def _augmented_path() -> str:
    """Current PATH plus the standard bin dirs (so the runner can find mpv)."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in _SEARCH_DIRS:
        if d not in parts and os.path.isdir(d):
            parts.append(d)
    return os.pathsep.join(parts)


def build_launch_command(settings: Settings, playlist: Path) -> list[str]:
    """Construct the argv for launching mpv on ``playlist``.

    Uses ``MPV_RUNNER`` if it exists (the original ``/tmp/mpv-runner.sh``
    wrapper), otherwise falls back to ``mpv`` (resolved absolutely) so the bot
    works even when the wrapper hasn't been recreated after a reboot. No
    ``setsid`` prefix is needed — ``Popen(start_new_session=True)`` detaches.
    """
    runner = settings.mpv_runner
    base = runner if runner and Path(runner).exists() else (_which("mpv") or "mpv")
    return [
        base,
        f"--playlist={playlist}",
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
    ]


def _switch_workspace(settings: Settings) -> None:
    if not settings.i3_socket or not Path(settings.i3_socket).exists():
        return
    i3 = _which("i3-msg")
    if i3 is None:
        return
    env = {**os.environ, "I3SOCK": settings.i3_socket, "PATH": _augmented_path()}
    subprocess.run(
        [i3, "workspace", settings.i3_workspace],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def play(settings: Settings, playlist: Path) -> None:
    """Stop any current mpv, switch workspace, and launch the playlist detached."""
    # Exact-match kill so we don't take down unrelated processes (mpv-runner etc).
    pkill = _which("pkill")
    if pkill:
        try:
            subprocess.run([pkill, "-x", "mpv"], check=False)
            time.sleep(0.3)
        except OSError as exc:  # don't let a kill failure abort playback
            logger.warning("pkill failed: %s", exc)

    _switch_workspace(settings)

    cmd = build_launch_command(settings, playlist)
    env = {**os.environ, "DISPLAY": settings.display, "PATH": _augmented_path()}
    logger.info("Launching: %s", " ".join(cmd))
    subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
