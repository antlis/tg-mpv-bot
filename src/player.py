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


def _ytdlp_bin() -> str | None:
    """Prefer the venv's yt-dlp: YouTube breaks faster than distro releases,
    so a nightly is pip-installed next to the bot's interpreter."""
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
    return str(venv_ytdlp) if venv_ytdlp.exists() else _which("yt-dlp")


class UrlPlaybackError(Exception):
    """Raised when a URL cannot be prepared for playback (user-facing msg)."""


_INFO_JSON = "tg-mpv-bot-info.json"  # one play at a time → fixed, self-cleaning


def probe_url(settings: Settings, url: str, timeout: float = 120) -> tuple[dict, Path]:
    """One yt-dlp extraction: returns the info dict + its JSON on disk.

    The saved JSON feeds ``--load-info-json`` downloads, so the actual
    streaming spawns do zero network extraction — they reuse the minted URLs
    immediately. Raises :class:`UrlPlaybackError` with a user-facing message
    on failure.
    """
    ytdlp = _ytdlp_bin()
    if ytdlp is None:
        raise UrlPlaybackError("yt-dlp is not installed on the host")
    cmd = [
        ytdlp, "--no-warnings", "--no-playlist", "-j",
        "-f", settings.ytdl_format,
        *_ytdl_cli_args(settings, url),
        "--", url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except subprocess.TimeoutExpired:
        raise UrlPlaybackError(
            f"site did not respond within {timeout:.0f}s (rate-limited?)"
        ) from None
    except OSError as exc:
        raise UrlPlaybackError(str(exc)) from exc
    try:
        info = json.loads(result.stdout)
    except ValueError:
        reason = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise UrlPlaybackError(reason[0][:200]) from None
    info_path = Path(tempfile.gettempdir()) / _INFO_JSON
    info_path.write_text(result.stdout)
    return info, info_path


def build_fetch_command(
    settings: Settings, info_path: Path, format_id: str | None = None
) -> list[str]:
    """argv for one yt-dlp stream-to-stdout download from saved info JSON.

    yt-dlp must do ALL the network fetching (exactly like a plain download):
    every quirk of modern CDNs — IP-locked URLs, client-bound User-Agents,
    PO-token formats — is handled by the one tool that keeps up with them.
    Handing mpv (or ffmpeg) stream URLs breaks whenever the CDN treats their
    fetch differently from yt-dlp's, which googlevideo on this host does.
    """
    cmd = [
        _ytdlp_bin() or "yt-dlp",
        "--no-warnings",
        "--load-info-json", str(info_path),
        "-o", "-",
    ]
    if format_id:
        cmd += ["-f", format_id]
    return cmd


def build_pipe_player_command(
    settings: Settings,
    title: str,
    video_fd: int | None = None,
    audio_fd: int | None = None,
) -> list[str]:
    """argv for mpv reading 1–2 piped streams.

    A single stream arrives on stdin. Split video+audio cannot share one
    pipe (yt-dlp can't merge to stdout — interleaved bytes are garbage), so
    each stream gets its own pipe fd (``fd://N`` + ``--audio-file=fd://M``)
    and mpv muxes them itself. Pipes aren't seekable beyond mpv's cache, so
    generous demuxer buffers keep seeking useful.
    """
    cmd = [
        _mpv_base(settings),
        f"fd://{video_fd}" if video_fd is not None else "-",
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        f"--force-media-title={title}",
        "--cache=yes",
        "--demuxer-max-bytes=600MiB",
        "--demuxer-max-back-bytes=600MiB",
    ]
    if audio_fd is not None:
        cmd.append(f"--audio-file=fd://{audio_fd}")
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
            result = subprocess.run([pkill, "-x", "mpv"], check=False)
            if result.returncode == 0:  # only wait if something was killed
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
    log_file = _log_file("tg-mpv-bot-mpv.log")
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


def _log_file(name: str):
    """Truncate-and-open a per-launch log in tmp (DEVNULL if that fails)."""
    try:
        return open(Path(tempfile.gettempdir()) / name, "wb")  # noqa: SIM115
    except OSError:
        return subprocess.DEVNULL


def play_url(settings: Settings, url: str) -> str:
    """Stream a URL: one yt-dlp extraction, then yt-dlp pipes into mpv.

    Single-stream media flows over stdin; split video+audio runs as two
    ``--load-info-json`` downloads (no re-extraction) into two pipe fds that
    mpv muxes itself. Returns the title; raises :class:`UrlPlaybackError`
    with a user-facing reason when the URL can't be prepared.
    """
    info, info_path = probe_url(settings, url)
    title = info.get("title") or url
    formats = info.get("requested_formats") or [info]

    env = _hook_env(settings, url, title)
    _stop_current(settings)
    _run_hook("pre-play", settings.pre_play_hook, env)

    ytdl_log = _log_file("tg-mpv-bot-ytdl.log")
    mpv_log = _log_file("tg-mpv-bot-mpv.log")
    common = dict(env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    if len(formats) >= 2:
        video_r, video_w = os.pipe()
        audio_r, audio_w = os.pipe()
        for fmt, write_end in ((formats[0], video_w), (formats[1], audio_w)):
            cmd = build_fetch_command(settings, info_path, fmt.get("format_id"))
            logger.info("Launching fetcher: %s", " ".join(cmd))
            subprocess.Popen(cmd, stdout=write_end, stderr=ytdl_log, **common)
            os.close(write_end)  # fetchers must own the only write ends
        mpv_cmd = build_pipe_player_command(settings, title, video_r, audio_r)
        logger.info("Launching player: %s", " ".join(mpv_cmd))
        subprocess.Popen(
            mpv_cmd, pass_fds=(video_r, audio_r),
            stdout=mpv_log, stderr=mpv_log, **common,
        )
        os.close(video_r)
        os.close(audio_r)
    else:
        cmd = build_fetch_command(settings, info_path)
        logger.info("Launching pipe: %s | mpv -", " ".join(cmd))
        fetcher = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ytdl_log, **common)
        mpv_cmd = build_pipe_player_command(settings, title)
        subprocess.Popen(
            mpv_cmd, env=env, stdin=fetcher.stdout,
            stdout=mpv_log, stderr=mpv_log, start_new_session=True,
        )
        fetcher.stdout.close()  # mpv's exit must SIGPIPE yt-dlp, not us
    for f in (ytdl_log, mpv_log):
        if f is not subprocess.DEVNULL:
            f.close()  # children hold their own duplicates

    _run_hook("post-play", settings.post_play_hook, env)
    state.record_last_played(settings.state_file, url)  # for /mpv_last
    return title
