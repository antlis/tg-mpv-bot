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
import sys
import tempfile
import time
import uuid
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


def _ytdlp_bin() -> str | None:
    """Prefer the venv's yt-dlp: YouTube breaks faster than distro releases,
    so a nightly is pip-installed next to the bot's interpreter."""
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
    return str(venv_ytdlp) if venv_ytdlp.exists() else _which("yt-dlp")


def build_pipe_commands(
    settings: Settings, url: str, title_file: Path | None = None
) -> tuple[list[str], list[str]] | None:
    """argv pair for ``yt-dlp -o - <url> | mpv -``.

    yt-dlp does ALL the network fetching (exactly like a plain download), so
    every quirk of modern CDNs — IP-locked URLs, client-bound User-Agents,
    PO-token formats — is handled by the one tool that keeps up with them.
    Handing mpv pre-resolved stream URLs looks cleaner but breaks whenever
    the CDN treats mpv's fetch differently from yt-dlp's (googlevideo does).

    The pipe isn't seekable beyond mpv's cache, so generous demuxer buffers
    keep back-seeking and ~minutes of forward-seeking working.
    """
    ytdlp = _ytdlp_bin()
    if ytdlp is None:
        return None
    ytdl_cmd = [
        ytdlp, "--no-warnings", "--no-playlist",
        "-f", settings.ytdl_format,
        "-o", "-",
        *_ytdl_cli_args(settings, url),
    ]
    if title_file is not None:
        # Written during the same invocation — no extra metadata request.
        ytdl_cmd += ["--print-to-file", "%(title)s", str(title_file)]
    ytdl_cmd += ["--", url]
    mpv_cmd = [
        _mpv_base(settings),
        "-",  # read the media from stdin
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        "--cache=yes",
        "--demuxer-max-bytes=600MiB",
        "--demuxer-max-back-bytes=600MiB",
    ]
    return ytdl_cmd, mpv_cmd


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


def play_url(settings: Settings, url: str) -> str | None:
    """Stream a URL via ``yt-dlp -o - | mpv -``, same kill→hooks→spawn path.

    Returns the media title if yt-dlp wrote it in time (best-effort), else
    ``None``. Playback continues regardless.
    """
    title_file = Path(tempfile.gettempdir()) / f"tg-mpv-title-{uuid.uuid4().hex}"
    cmds = build_pipe_commands(settings, url, title_file)
    if cmds is None:
        logger.warning("yt-dlp not found — falling back to mpv's ytdl hook")
        title = None
        env = _hook_env(settings, url, url)
        _kill_and_launch(settings, build_url_command(settings, url), env)
    else:
        ytdl_cmd, mpv_cmd = cmds
        env = _hook_env(settings, url, url)
        _stop_current(settings)
        _run_hook("pre-play", settings.pre_play_hook, env)
        logger.info("Launching pipe: %s | %s", " ".join(ytdl_cmd), " ".join(mpv_cmd))
        ytdl_log, mpv_log = _log_file("tg-mpv-bot-ytdl.log"), _log_file("tg-mpv-bot-mpv.log")
        fetcher = subprocess.Popen(
            ytdl_cmd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=ytdl_log, start_new_session=True,
        )
        subprocess.Popen(
            mpv_cmd, env=env, stdin=fetcher.stdout,
            stdout=mpv_log, stderr=mpv_log, start_new_session=True,
        )
        fetcher.stdout.close()  # mpv's exit must SIGPIPE yt-dlp, not us
        for f in (ytdl_log, mpv_log):
            if f is not subprocess.DEVNULL:
                f.close()  # children hold their own duplicates
        _run_hook("post-play", settings.post_play_hook, env)
        title = _read_title(title_file)
    state.record_last_played(settings.state_file, url)  # for /mpv_last
    return title


def _read_title(title_file: Path, wait: float = 10.0) -> str | None:
    """Poll briefly for the title yt-dlp prints at extraction time."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            text = title_file.read_text().strip()
        except OSError:
            text = ""
        if text:
            title_file.unlink(missing_ok=True)
            return text
        time.sleep(0.5)
    return None
