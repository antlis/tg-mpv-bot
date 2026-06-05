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

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from . import state
from .config import Settings
from .mpv_ipc import MpvClient

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


def _mpv_base(settings: Settings) -> str:
    """The mpv binary (or wrapper) to launch.

    Uses ``MPV_RUNNER`` if it exists (the original ``/tmp/mpv-runner.sh``
    wrapper), otherwise falls back to ``mpv`` (resolved absolutely) so the bot
    works even when the wrapper hasn't been recreated after a reboot.
    """
    runner = settings.mpv_runner
    return runner if runner and Path(runner).exists() else (_which("mpv") or "mpv")


def build_launch_command(settings: Settings, playlist: Path) -> list[str]:
    """Construct the argv for launching mpv on ``playlist``.

    No ``setsid`` prefix is needed — ``Popen(start_new_session=True)`` detaches.
    """
    return [
        _mpv_base(settings),
        f"--playlist={playlist}",
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        "--save-position-on-quit",  # resume each file where you left off
    ]


# Hosts that require a logged-in session for most content. Browser cookies
# are applied ONLY here: with YouTube, account cookies make yt-dlp stall on
# bot checks (observed as mpv hanging at "? / ?" then dying), so a global
# cookies option would break the common case to serve the rare one.
GATED_HOSTS = ("instagram.com", "facebook.com", "fb.watch")


def _is_gated_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in GATED_HOSTS)


def build_url_command(settings: Settings, url: str) -> list[str]:
    """Fallback argv: stream ``url`` via mpv's *built-in* ytdl hook.

    Only used when :func:`resolve_stream` fails — the hook is unreliable on
    some mpv/ffmpeg combinations ("EDL: Could not open source file" even for
    URLs that play fine directly), which is why the bot normally resolves
    URLs itself.
    """
    cmd = [
        _mpv_base(settings),
        url,
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        "--save-position-on-quit",
    ]
    raw = [o.strip() for o in settings.ytdl_options.split(",") if o.strip()]
    if settings.ytdl_cookies_browser and _is_gated_host(url):
        raw.append(f"cookies-from-browser={settings.ytdl_cookies_browser}")
    if raw:
        # mpv requires every raw option to be key=value — a bare flag like
        # "force-ipv6" must become "force-ipv6=" or mpv exits with a fatal
        # option-parse error before playing anything.
        normalized = [o if "=" in o else f"{o}=" for o in raw]
        cmd.append(f"--ytdl-raw-options={','.join(normalized)}")
    return cmd


def _ytdl_cli_args(settings: Settings, url: str) -> list[str]:
    """Translate the YTDL_* settings into yt-dlp CLI flags."""
    args: list[str] = []
    for opt in filter(None, settings.ytdl_options.split(",")):
        key, _, value = opt.partition("=")
        args.append(f"--{key}")
        if value:
            args.append(value)
    if settings.ytdl_cookies_browser and _is_gated_host(url):
        args += ["--cookies-from-browser", settings.ytdl_cookies_browser]
    return args


def resolve_stream(
    settings: Settings, url: str, timeout: float = 180
) -> tuple[str, list[str], dict[str, str]] | None:
    """Resolve ``url`` with yt-dlp to ``(title, stream URL(s), http headers)``.

    One URL for muxed formats, two (video + audio) when yt-dlp picks separate
    streams. The headers matter: CDNs like googlevideo reject fetches whose
    User-Agent doesn't match the client yt-dlp minted the URL for — that's
    why yt-dlp downloads the same video fine while a header-less player 403s.
    Returns ``None`` when yt-dlp is missing/fails (caller falls back to mpv's
    hook).
    """
    # Prefer the venv's yt-dlp: YouTube breaks faster than distro releases,
    # so a nightly is pip-installed next to the bot's interpreter.
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
    ytdlp = str(venv_ytdlp) if venv_ytdlp.exists() else _which("yt-dlp")
    if ytdlp is None:
        return None
    cmd = [
        ytdlp, "--no-warnings", "--no-playlist", "-j",
        "-f", "bv*+ba/b",
        *_ytdl_cli_args(settings, url),
        "--", url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("yt-dlp resolution failed for %s: %s", url, exc)
        return None
    try:
        info = json.loads(result.stdout)
    except ValueError:
        logger.warning(
            "yt-dlp could not resolve %s: %s", url, result.stderr.strip()[:300]
        )
        return None
    formats = info.get("requested_formats") or [info]
    urls = [f["url"] for f in formats if f.get("url")]
    if not urls:
        logger.warning("yt-dlp returned no stream URLs for %s", url)
        return None
    headers = formats[0].get("http_headers") or {}
    return info.get("title") or url, urls, headers


def build_direct_command(
    settings: Settings, urls: list[str], title: str, headers: dict[str, str]
) -> list[str]:
    """argv for playing pre-resolved stream URL(s) without the ytdl hook.

    The User-Agent/Referer from yt-dlp are forwarded so the CDN accepts the
    fetch. ``--save-position-on-quit`` is pointless here (resolved URLs
    expire after hours, so the position would be keyed to a dead URL);
    ``/mpv_last`` re-resolves the original page URL instead.
    """
    cmd = [
        _mpv_base(settings),
        urls[0],
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        f"--force-media-title={title}",  # /mpv_info shows the title, not a hash
    ]
    if len(urls) > 1:
        cmd.append(f"--audio-file={urls[1]}")
    if headers.get("User-Agent"):
        cmd.append(f"--user-agent={headers['User-Agent']}")
    if headers.get("Referer"):
        cmd.append(f"--referrer={headers['Referer']}")
    return cmd


HOOK_TIMEOUT = 15  # seconds — a hung hook must not block playback for long


def _hook_env(settings: Settings, target: str, name: str) -> dict[str, str]:
    """Environment for hooks and mpv: X11 display, sane PATH, playlist info."""
    return {
        **os.environ,
        "DISPLAY": settings.display,
        "PATH": _augmented_path(),
        "PLAYLIST": target,
        "PLAYLIST_NAME": name,
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


def _stop_current(settings: Settings) -> None:
    """Stop whatever is playing before launching the next thing.

    The bot's own instance is asked to quit over IPC first — a graceful exit,
    so ``--save-position-on-quit`` records the resume point reliably. Stray
    instances (started by hand, no IPC socket of ours) are then pkill'ed,
    unless ``KILL_STRAY_MPV=0`` opts out of that.
    """
    try:
        MpvClient(settings.mpv_socket, timeout=1.0).quit()
        time.sleep(0.3)  # let it release the window/audio device
    except Exception:  # noqa: BLE001 — dead socket / no mpv: nothing to quit
        pass

    if not settings.kill_stray_mpv:
        return
    # Exact-match kill so we don't take down unrelated processes (mpv-runner etc).
    pkill = _which("pkill")
    if pkill:
        try:
            subprocess.run([pkill, "-x", "mpv"], check=False)
            time.sleep(0.3)
        except OSError as exc:  # don't let a kill failure abort playback
            logger.warning("pkill failed: %s", exc)


def _kill_and_launch(settings: Settings, cmd: list[str], env: dict[str, str]) -> None:
    """Shared launch path: stop mpv, pre-hook, detached spawn, post-hook."""
    _stop_current(settings)

    _run_hook("pre-play", settings.pre_play_hook, env)

    logger.info("Launching: %s", " ".join(cmd))
    # mpv's output goes to a per-launch log, not /dev/null — when a stream
    # fails to open, this file is the only place the reason exists.
    log_path = Path(tempfile.gettempdir()) / "tg-mpv-bot-mpv.log"
    try:
        log_file = open(log_path, "wb")  # noqa: SIM115 — handed to Popen
    except OSError:
        log_file = subprocess.DEVNULL
    subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    if log_file is not subprocess.DEVNULL:
        log_file.close()  # the child holds its own duplicate

    _run_hook("post-play", settings.post_play_hook, env)


def play(settings: Settings, playlist: Path) -> None:
    """Stop any current mpv, run hooks around a detached launch of ``playlist``."""
    env = _hook_env(settings, str(playlist), playlist.stem)
    _kill_and_launch(settings, build_launch_command(settings, playlist), env)
    state.record_last_played(settings.state_file, playlist)  # for /mpv_last


def play_url(settings: Settings, url: str) -> str | None:
    """Stream a URL (YouTube/SoundCloud/…), same kill→hooks→spawn path.

    The bot resolves the page URL to direct stream URL(s) with yt-dlp itself
    and hands those to mpv — mpv's built-in ytdl hook is broken on some
    mpv/ffmpeg combos (EDL open failures) and hides errors. The hook remains
    as a fallback when resolution fails (e.g. yt-dlp not installed).

    Returns the resolved title, or ``None`` when the hook fallback was used.
    """
    resolved = resolve_stream(settings, url)
    if resolved:
        title, urls, headers = resolved
        cmd = build_direct_command(settings, urls, title, headers)
    else:
        title = None
        logger.warning("Falling back to mpv's ytdl hook for %s", url)
        cmd = build_url_command(settings, url)
    env = _hook_env(settings, url, url)
    _kill_and_launch(settings, cmd, env)
    state.record_last_played(settings.state_file, url)  # for /mpv_last
    return title
