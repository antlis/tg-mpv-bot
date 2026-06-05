"""Launching mpv — the one part that genuinely needs the shell/subprocess.

Everything else (pause/seek/volume/info) goes straight to the IPC socket via
:mod:`src.mpv_ipc`. Here we kill any running mpv, run the user's pre-play
hook (window-manager glue like ``i3-msg workspace 10`` lives there, not in
the bot), start a detached mpv on the chosen playlist, then run the
post-play hook.

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

from . import state
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
        "--save-position-on-quit",  # resume each file where you left off
    ]


HOOK_TIMEOUT = 15  # seconds — a hung hook must not block playback for long


def _hook_env(settings: Settings, playlist: Path) -> dict[str, str]:
    """Environment for hooks and mpv: X11 display, sane PATH, playlist info."""
    return {
        **os.environ,
        "DISPLAY": settings.display,
        "PATH": _augmented_path(),
        "PLAYLIST": str(playlist),
        "PLAYLIST_NAME": playlist.stem,
        "MPV_SOCKET": settings.mpv_socket,
    }


def _run_hook(label: str, command: str, env: dict[str, str]) -> None:
    """Run a user hook (shell command). Failures are logged, never fatal."""
    if not command:
        return
    try:
        result = subprocess.run(
            command,
            shell=True,
            env=env,
            timeout=HOOK_TIMEOUT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "%s hook exited %d: %s", label, result.returncode,
                (result.stderr or result.stdout).strip()[:200],
            )
    except subprocess.TimeoutExpired:
        logger.warning("%s hook timed out after %ds: %s", label, HOOK_TIMEOUT, command)
    except OSError as exc:
        logger.warning("%s hook failed: %s", label, exc)


def play(settings: Settings, playlist: Path) -> None:
    """Stop any current mpv, run hooks around a detached launch of ``playlist``."""
    # Exact-match kill so we don't take down unrelated processes (mpv-runner etc).
    pkill = _which("pkill")
    if pkill:
        try:
            subprocess.run([pkill, "-x", "mpv"], check=False)
            time.sleep(0.3)
        except OSError as exc:  # don't let a kill failure abort playback
            logger.warning("pkill failed: %s", exc)

    env = _hook_env(settings, playlist)
    _run_hook("pre-play", settings.pre_play_hook, env)

    cmd = build_launch_command(settings, playlist)
    logger.info("Launching: %s", " ".join(cmd))
    subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    _run_hook("post-play", settings.post_play_hook, env)
    state.record_last_played(settings.state_file, playlist)  # for /mpv_last
