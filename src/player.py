"""Launching mpv — the one part that genuinely needs the shell/subprocess.

Everything else (pause/seek/volume/info) goes straight to the IPC socket via
:mod:`src.mpv_ipc`. Here we kill any running mpv, optionally switch the i3
workspace, and start a detached mpv on the chosen playlist.
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


def build_launch_command(settings: Settings, playlist: Path) -> list[str]:
    """Construct the argv for launching mpv on ``playlist``.

    Uses ``MPV_RUNNER`` if it exists (the original ``/tmp/mpv-runner.sh``
    wrapper), otherwise falls back to ``mpv`` directly so the bot works even
    when the wrapper hasn't been recreated after a reboot.
    """
    runner = settings.mpv_runner
    base = [runner] if runner and Path(runner).exists() else ["mpv"]
    return [
        "setsid",
        *base,
        f"--playlist={playlist}",
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
    ]


def _switch_workspace(settings: Settings) -> None:
    if not settings.i3_socket or not Path(settings.i3_socket).exists():
        return
    if shutil.which("i3-msg") is None:
        return
    env = {**os.environ, "I3SOCK": settings.i3_socket}
    subprocess.run(
        ["i3-msg", "workspace", settings.i3_workspace],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def play(settings: Settings, playlist: Path) -> None:
    """Stop any current mpv, switch workspace, and launch the playlist detached."""
    # Exact-match kill so we don't take down unrelated processes (mpv-runner etc).
    subprocess.run(["pkill", "-x", "mpv"], check=False)
    time.sleep(0.3)

    _switch_workspace(settings)

    cmd = build_launch_command(settings, playlist)
    env = {**os.environ, "DISPLAY": settings.display}
    logger.info("Launching: %s", " ".join(cmd))
    subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
