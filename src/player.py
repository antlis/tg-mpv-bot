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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
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


_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS)


def _ytdl_cli_args(settings: Settings, url: str) -> list[str]:
    """Translate the YTDL_* settings into yt-dlp CLI flags.

    Network-pinning options (force-ipv4 etc.) apply to **YouTube only**:
    they exist to keep the pipe's fetches on the proven proxy path. For
    everything else the probe must use the same default network stack mpv
    will fetch with — some CDNs embed the requesting IP in the minted URL
    and reject a fetch whose egress differs from the minting request.
    """
    args: list[str] = []
    youtube = _is_youtube_url(url)
    for opt in filter(None, settings.ytdl_options.split(",")):
        key, _, value = opt.partition("=")
        if not youtube and key in _NETWORK_KEYS:
            continue
        args.append(f"--{key}")
        if value:
            args.append(value)
    if not youtube and settings.media_proxy:
        # mint stream URLs from the same egress mpv will fetch them over
        args += ["--proxy", settings.media_proxy]
    if settings.ytdl_cookies_browser and _is_gated_host(url):
        args += ["--cookies-from-browser", settings.ytdl_cookies_browser]
    return args


# YTDL_OPTIONS keys that describe how to *reach* the network rather than how
# to extract — these must survive into escalation retries (falling back to a
# dead address family would just trade one failure for another).
_NETWORK_KEYS = {"force-ipv4", "force-ipv6", "proxy", "source-address", "socket-timeout"}


def _network_cli_args(settings: Settings) -> list[str]:
    args: list[str] = []
    for opt in filter(None, settings.ytdl_options.split(",")):
        key, _, value = opt.partition("=")
        if key in _NETWORK_KEYS:
            args.append(f"--{key}")
            if value:
                args.append(value)
    return args


def _ytdlp_bin() -> str | None:
    """Prefer the venv's yt-dlp: YouTube breaks faster than distro releases,
    so a nightly is pip-installed next to the bot's interpreter."""
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
    return str(venv_ytdlp) if venv_ytdlp.exists() else _which("yt-dlp")


class UrlPlaybackError(Exception):
    """Raised when a URL cannot be prepared for playback (user-facing msg)."""


# probe_url raises this reason when the URL is a playlist/channel/listing —
# callers can then offer probe_listing()'s entries instead of an error.
PLAYLIST_URL = "__playlist_url__"


# YouTube breaks extraction every few months; stable releases lag the fix.
NIGHTLY_URL = (
    "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"
)


def ytdlp_version() -> str | None:
    """Installed yt-dlp version (venv copy preferred), or ``None``."""
    binary = _ytdlp_bin()
    if binary is None:
        return None
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _installer_command() -> list[str] | None:
    """How to install into the bot's venv: ``uv pip`` (uv venvs ship no pip)
    or the venv's own pip as a fallback."""
    uv = _which("uv") or (
        str(p) if (p := Path.home() / ".local/bin/uv").exists() else None
    )
    if uv:
        return [uv, "pip", "install", "--python", sys.executable]
    pip = Path(sys.executable).parent / "pip"
    if pip.exists():
        return [str(pip), "install"]
    return None


def update_ytdlp(timeout: float = 300) -> str:
    """Update the venv's yt-dlp to the latest nightly; returns a status line.

    Behind ``/mpv_update_ytdlp`` so the fix for the next YouTube breakage is
    one Telegram tap instead of a shell session. Installs into the bot's own
    venv (which :func:`_ytdlp_bin` prefers) — never touches the system one.
    Note: a later ``uv sync``/``uv run`` reverts to the locked stable
    release; just run this again after.
    """
    installer = _installer_command()
    if installer is None:
        return "❌ Neither uv nor a venv pip found — can't install"

    def version() -> str:
        return ytdlp_version() or "none"

    old = version()
    try:
        result = subprocess.run(
            [*installer, "--quiet", "--upgrade", NIGHTLY_URL],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"❌ pip timed out after {timeout:.0f}s"
    if result.returncode != 0:
        return f"❌ pip failed: {result.stderr.strip()[-300:]}"
    new = version()
    if new == old:
        return f"✅ yt-dlp already up to date ({new})"
    return f"✅ yt-dlp updated: {old} → {new}"


_INFO_JSON = "tg-mpv-bot-info.json"  # one play at a time → fixed, self-cleaning


# Errors that no retry can fix — fail fast, don't waste a slow second probe.
_TERMINAL_ERRORS = (
    "Video unavailable",
    "Private video",
    "This live event will begin",
    "has been removed",
    "is not a valid URL",
    "Unsupported URL",
    PLAYLIST_URL,  # sentinel: the URL is a listing, not a single video
)


def _should_escalate(error: str) -> bool:
    """Retry with stock client + cookies? Anything that smells like client
    degradation (bot checks, missing formats) qualifies — YouTube cycles
    failure modes on flagged IPs, so matching one exact message is a trap."""
    return not any(t in error for t in _TERMINAL_ERRORS)


def _run_probe(
    settings: Settings, url: str, extra_args: list[str], timeout: float
) -> tuple[dict | None, str]:
    """One yt-dlp -j attempt; returns ``(info, "")`` or ``(None, reason)``."""
    cmd = [
        _ytdlp_bin() or "yt-dlp",
        "--no-warnings", "--no-playlist", "-j",
        "-f", settings.ytdl_format,
        *extra_args,
        "--", url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except subprocess.TimeoutExpired:
        return None, f"site did not respond within {timeout:.0f}s (rate-limited?)"
    except OSError as exc:
        return None, str(exc)
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) > 1:
        # -j prints one JSON object per entry: this URL is a whole listing
        return None, PLAYLIST_URL
    try:
        return json.loads(result.stdout), ""
    except ValueError:
        reason = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        return None, reason[0][:200]


def probe_url(
    settings: Settings,
    url: str,
    timeout: float = 120,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, Path]:
    """One yt-dlp extraction: returns the info dict + its JSON on disk.

    The saved JSON feeds ``--load-info-json`` downloads, so the actual
    streaming spawns do zero network extraction — they reuse the minted URLs
    immediately. Raises :class:`UrlPlaybackError` with a user-facing message
    on failure.

    Escalation (same shape as tg-media-bot's geo-retry): the first attempt
    runs cookie-less with the fast YTDL_OPTIONS — logged-in YouTube cookies
    stall *normal* extraction, so they must not be the default. When that
    fails with anything non-terminal (bot-check demand, missing formats —
    YouTube cycles failure modes on flagged IPs), retry once with stock
    client args plus browser cookies if configured.
    """
    if _ytdlp_bin() is None:
        raise UrlPlaybackError("yt-dlp is not installed on the host")
    fast_args = _ytdl_cli_args(settings, url)
    info, reason = _run_probe(settings, url, fast_args, timeout)
    if info is None and _should_escalate(reason):
        if _is_youtube_url(url):
            stock_args = _network_cli_args(settings)
        else:
            stock_args = ["--proxy", settings.media_proxy] if settings.media_proxy else []
        if settings.ytdl_cookies_browser:
            stock_args += ["--cookies-from-browser", settings.ytdl_cookies_browser]
        if stock_args != fast_args:
            logger.info("Probe failed (%s) — escalating with stock args for %s",
                        reason[:80], url)
            if progress:
                progress("escalating")
            info, reason = _run_probe(settings, url, stock_args, timeout)
    if info is None:
        raise UrlPlaybackError(reason)
    info_path = Path(tempfile.gettempdir()) / _INFO_JSON
    info_path.write_text(json.dumps(info))
    return info, info_path


def build_file_command(settings: Settings, path: Path, title: str) -> list[str]:
    """argv for playing a single local file (not a playlist)."""
    return [
        _mpv_base(settings),
        str(path),
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        "--save-position-on-quit",
        f"--force-media-title={title}",
    ]


def play_file(settings: Settings, path: Path, title: str) -> None:
    """Play one local file (e.g. downloaded from a Telegram message)."""
    env = _hook_env(settings, str(path), title)
    _kill_and_launch(settings, build_file_command(settings, path, title), env)
    state.record_last_played(settings.state_file, path, name=title)


def search_youtube(settings: Settings, query: str, n: int = 5) -> list[dict]:
    """Top-``n`` YouTube results as ``{id, title, duration, channel}`` dicts.

    ``--flat-playlist`` keeps it to one cheap search request (no per-video
    extraction). Raises :class:`UrlPlaybackError` with a user-facing reason.
    """
    ytdlp = _ytdlp_bin()
    if ytdlp is None:
        raise UrlPlaybackError("yt-dlp is not installed on the host")
    cmd = [
        ytdlp, "--no-warnings", "-j", "--flat-playlist",
        *_network_cli_args(settings),
        f"ytsearch{n}:{query}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except subprocess.TimeoutExpired:
        raise UrlPlaybackError("YouTube search timed out") from None
    except OSError as exc:
        raise UrlPlaybackError(str(exc)) from exc
    results = []
    for line in result.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("id"):
            thumbs = e.get("thumbnails") or []
            results.append({
                "id": e["id"],
                "title": e.get("title") or e["id"],
                "duration": e.get("duration"),
                "channel": e.get("channel") or e.get("uploader") or "",
                "thumb": thumbs[-1]["url"] if thumbs else None,  # largest last
            })
    if not results:
        reason = result.stderr.strip().splitlines()[-1:] or ["no results"]
        raise UrlPlaybackError(reason[0][:200])
    return results


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
        # The probe minted the URLs over this network path; fetching over a
        # different one (e.g. v6 when the URLs are bound to the v4 proxy IP)
        # gets tarpitted by IP-locked CDNs.
        *_network_cli_args(settings),
    ]
    if format_id:
        cmd += ["-f", format_id]
    return cmd


_SUB_PREFIX = "tg-mpv-sub"  # subtitle files in tmp: tg-mpv-sub.<lang>.vtt


def build_subs_command(settings: Settings, info_path: Path) -> list[str]:
    """argv to fetch subtitles for the probed video (no re-extraction)."""
    return [
        _ytdlp_bin() or "yt-dlp",
        "--no-warnings",
        "--load-info-json", str(info_path),
        "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", settings.ytdl_sub_langs,
        *_network_cli_args(settings),
        *(["--proxy", settings.media_proxy] if settings.media_proxy else []),
        "-o", str(Path(tempfile.gettempdir()) / _SUB_PREFIX),
    ]


def fetch_subtitles(settings: Settings, info: dict, info_path: Path) -> list[Path]:
    """Download subtitle files for the probed stream; best-effort.

    Skipped entirely when the info dict advertises no subtitles (saves a
    spawn) or YTDL_SUB_LANGS is empty. mpv reads the resulting .vtt
    natively — no conversion step, no ffmpeg dependency.
    """
    if not settings.ytdl_sub_langs:
        return []
    if not (info.get("subtitles") or info.get("automatic_captions")):
        return []
    tmp = Path(tempfile.gettempdir())
    for old in tmp.glob(f"{_SUB_PREFIX}*"):
        old.unlink(missing_ok=True)
    try:
        subprocess.run(
            build_subs_command(settings, info_path),
            capture_output=True, timeout=45, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Subtitle fetch failed: %s", exc)
        return []
    return sorted(tmp.glob(f"{_SUB_PREFIX}*"))[:3]  # a few tracks is plenty


# Artwork for audio-only playback: a black TV is a wasted TV.
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_RADIO_PLACEHOLDER = _ASSETS_DIR / "radio-placeholder.png"
_COVER_PATH = Path(tempfile.gettempdir()) / "tg-mpv-cover.img"


def _station_art_url(stream_url: str) -> str | None:
    """Derive official channel art where the pattern is known (SomaFM)."""
    m = re.match(r"https?://somafm\.com/([a-z0-9]+)\.pls", stream_url)
    if m:
        # matches 44/46 channels; the rest 404 → placeholder fallback
        return f"https://api.somafm.com/logos/512/{m.group(1)}512.jpg"
    return None


def _fetch_cover(settings: Settings, art_url: str | None) -> Path | None:
    """Download station/track art (best-effort); fall back to the bundled
    placeholder so audio playback always has *something* on screen."""
    if art_url:
        from urllib.request import ProxyHandler, Request, build_opener

        handlers = (
            [ProxyHandler({"http": settings.media_proxy, "https": settings.media_proxy})]
            if settings.media_proxy else []
        )
        try:
            req = Request(art_url, headers={"User-Agent": "tg-mpv-bot/1.2"})
            with build_opener(*handlers).open(req, timeout=6) as resp:
                _COVER_PATH.write_bytes(resp.read())
            return _COVER_PATH
        except (OSError, ValueError) as exc:
            logger.warning("Cover fetch failed (%s) — using placeholder", exc)
    return _RADIO_PLACEHOLDER if _RADIO_PLACEHOLDER.is_file() else None


# Community radio database (radio-browser.info) — free, no auth, mirrored.
_RADIO_BROWSER_MIRRORS = (
    "https://de1.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
)


def _parse_radio_results(raw: list[dict], n: int) -> list[dict]:
    """Normalize radio-browser station entries; drop ones without a URL."""
    out = []
    for s in raw:
        url = s.get("url_resolved") or s.get("url")
        if not url:
            continue
        out.append({
            "name": s.get("name") or url,
            "url": url,
            "codec": s.get("codec") or "",
            "bitrate": s.get("bitrate") or 0,
            "country": s.get("countrycode") or "",
            "favicon": s.get("favicon") or "",
        })
        if len(out) >= n:
            break
    return out


def search_radio(settings: Settings, query: str, n: int = 8) -> list[dict]:
    """Search ~50k stations on radio-browser.info, best-voted first.

    Raises :class:`UrlPlaybackError` with a user-facing reason when all
    mirrors fail.
    """
    from urllib.error import URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    last_error: Exception | None = None
    for mirror in _RADIO_BROWSER_MIRRORS:
        api = (
            f"{mirror}/json/stations/byname/{quote(query)}"
            f"?limit={n * 2}&order=votes&reverse=true&hidebroken=true"
        )
        try:
            req = Request(api, headers={"User-Agent": "tg-mpv-bot/1.2"})
            with urlopen(req, timeout=10) as resp:
                results = _parse_radio_results(json.loads(resp.read()), n)
            if results:
                return results
            last_error = UrlPlaybackError(f"no stations matching '{query}'")
        except (URLError, OSError, ValueError) as exc:
            last_error = exc
    raise UrlPlaybackError(str(last_error or "radio search failed"))


def build_radio_command(
    settings: Settings, url: str, name: str, cover: Path | None = None
) -> list[str]:
    """argv for an internet-radio stream — straight to mpv, no probe.

    mpv parses ``.pls``/icecast natively, so stations start in ~a second.
    No resume/position flags: live streams have no meaningful position.
    ``cover`` becomes the video track via ``--cover-art-files`` — station
    art instead of a black screen.
    """
    cmd = [
        _mpv_base(settings),
        url,
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",  # something on the TV + icecast title via OSD/panel
        f"--force-media-title={name}",
    ]
    if cover is not None:
        cmd.append(f"--cover-art-files={cover}")
    if settings.media_proxy:
        cmd.append(f"--http-proxy={settings.media_proxy}")
    return cmd


def play_radio(
    settings: Settings, url: str, name: str, art_url: str | None = None
) -> None:
    """Tune the TV to an internet-radio stream (same kill→hooks→spawn path)."""
    cover = _fetch_cover(settings, art_url or _station_art_url(url))
    env = _hook_env(settings, url, name)
    _kill_and_launch(settings, build_radio_command(settings, url, name, cover), env)
    state.record_last_played(settings.state_file, url, name=name)


def build_listing_command(settings: Settings, url: str, limit: int = 12) -> list[str]:
    """argv for a cheap flat probe of a playlist/channel/listing page."""
    return [
        _ytdlp_bin() or "yt-dlp",
        "--no-warnings", "-J", "--flat-playlist",
        "--playlist-items", f"1:{limit}",
        *_network_cli_args(settings),
        "--", url,
    ]


def probe_listing(settings: Settings, url: str, limit: int = 12) -> list[dict]:
    """First ``limit`` entries of a listing URL: ``{title, url, duration}``.

    Returns ``[]`` when the page isn't a listing yt-dlp understands (e.g. a
    profile page with no extractor).
    """
    try:
        result = subprocess.run(
            build_listing_command(settings, url, limit),
            capture_output=True, text=True, timeout=90, check=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    entries = data.get("entries") or []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        target = e.get("url") or e.get("webpage_url")
        if target:
            out.append({
                "title": e.get("title") or target,
                "url": target,
                "duration": e.get("duration"),
            })
    return out


def needs_pipe(info: dict) -> bool:
    """Must yt-dlp do the fetching itself (pipe), or can mpv take the URL?

    googlevideo URLs are IP-locked and client-bound — an external player's
    fetch gets refused/tarpitted, so YouTube goes through the pipe. Everything
    else plays better from the direct URL: mpv can issue range requests,
    which full seeking needs — and which *progressive MP4s with a trailing
    moov atom require to start at all* (858 MB through a pipe and mpv still
    can't begin; that exact failure prompted this split).
    """
    if "youtube" in str(info.get("extractor") or "").lower():
        return True
    formats = info.get("requested_formats") or [info]
    return any("googlevideo" in str(f.get("url") or "") for f in formats)


def _is_audio_only(info: dict) -> bool:
    formats = info.get("requested_formats") or [info]
    return all((f.get("vcodec") or "none") == "none" for f in formats)


def build_direct_command(
    settings: Settings,
    info: dict,
    title: str,
    sub_files: list[Path] | None = None,
    start: float | None = None,
    cover: Path | None = None,
) -> list[str]:
    """argv for mpv playing pre-resolved stream URL(s) directly.

    yt-dlp's ``http_headers`` ride along (CDNs often check User-Agent /
    Referer). No ``--save-position-on-quit`` — resolved URLs expire, so the
    resume point would be keyed to a dead URL; the listener's checkpoints
    plus ``--start`` handle resume instead.
    """
    formats = info.get("requested_formats") or [info]
    urls = [f["url"] for f in formats if f.get("url")]
    headers = formats[0].get("http_headers") or {}
    cmd = [
        _mpv_base(settings),
        urls[0],
        f"--input-ipc-server={settings.mpv_socket}",
        "--force-window",
        f"--force-media-title={title}",
    ]
    if len(urls) > 1:
        cmd.append(f"--audio-file={urls[1]}")
    if headers.get("User-Agent"):
        cmd.append(f"--user-agent={headers['User-Agent']}")
    if headers.get("Referer"):
        cmd.append(f"--referrer={headers['Referer']}")
    if settings.media_proxy:
        # fetch from the same egress the probe minted the URL over
        cmd.append(f"--http-proxy={settings.media_proxy}")
    if cover is not None:
        cmd.append(f"--cover-art-files={cover}")
    for sub in sub_files or []:
        cmd.append(f"--sub-file={sub}")
    if start and start > 0:
        cmd.append(f"--start={int(start)}")
    return cmd


def build_pipe_player_command(
    settings: Settings,
    title: str,
    video_fd: int | None = None,
    audio_fd: int | None = None,
    sub_files: list[Path] | None = None,
    start: float | None = None,
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
    for sub in sub_files or []:
        cmd.append(f"--sub-file={sub}")
    if start and start > 0:
        # Resume point from the listener's checkpoints. Seeking a pipe means
        # reading up to the offset, so far-in resumes take a moment to catch up.
        cmd.append(f"--start={int(start)}")
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


def play_url(
    settings: Settings,
    url: str,
    progress: Callable[[str], None] | None = None,
    start: float | None = None,
) -> str:
    """Stream a URL: one yt-dlp extraction, then yt-dlp pipes into mpv.

    Single-stream media flows over stdin; split video+audio runs as two
    ``--load-info-json`` downloads (no re-extraction) into two pipe fds that
    mpv muxes itself. Returns the title; raises :class:`UrlPlaybackError`
    with a user-facing reason when the URL can't be prepared. ``progress``
    (called from this worker thread) receives stage names — "escalating"
    when the cookie retry kicks in, "starting" once the probe succeeded.
    """
    info, info_path = probe_url(settings, url, progress=progress)
    if progress:
        progress("subs")
    sub_files = fetch_subtitles(settings, info, info_path)
    if progress:
        progress("starting")
    title = info.get("title") or url
    formats = info.get("requested_formats") or [info]

    env = _hook_env(settings, url, title)
    _stop_current(settings)
    _run_hook("pre-play", settings.pre_play_hook, env)

    ytdl_log = _log_file("tg-mpv-bot-ytdl.log")
    mpv_log = _log_file("tg-mpv-bot-mpv.log")
    common = dict(env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    if not needs_pipe(info):
        # Direct URL: mpv fetches with range requests — full seeking, and
        # the only way moov-at-end progressive MP4s start at all.
        cover = (
            _fetch_cover(settings, info.get("thumbnail"))
            if _is_audio_only(info)  # track art (SoundCloud etc.) over black
            else None
        )
        mpv_cmd = build_direct_command(settings, info, title, sub_files, start, cover)
        logger.info("Launching direct: %s", " ".join(mpv_cmd))
        subprocess.Popen(mpv_cmd, stdout=mpv_log, stderr=mpv_log, **common)
    elif len(formats) >= 2:
        video_r, video_w = os.pipe()
        audio_r, audio_w = os.pipe()
        for fmt, write_end in ((formats[0], video_w), (formats[1], audio_w)):
            cmd = build_fetch_command(settings, info_path, fmt.get("format_id"))
            logger.info("Launching fetcher: %s", " ".join(cmd))
            subprocess.Popen(cmd, stdout=write_end, stderr=ytdl_log, **common)
            os.close(write_end)  # fetchers must own the only write ends
        mpv_cmd = build_pipe_player_command(
            settings, title, video_r, audio_r, sub_files, start=start
        )
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
        mpv_cmd = build_pipe_player_command(settings, title, sub_files=sub_files, start=start)
        subprocess.Popen(
            mpv_cmd, env=env, stdin=fetcher.stdout,
            stdout=mpv_log, stderr=mpv_log, start_new_session=True,
        )
        fetcher.stdout.close()  # mpv's exit must SIGPIPE yt-dlp, not us
    for f in (ytdl_log, mpv_log):
        if f is not subprocess.DEVNULL:
            f.close()  # children hold their own duplicates

    _run_hook("post-play", settings.post_play_hook, env)
    state.record_last_played(settings.state_file, url, name=title)  # /mpv_last|history
    return title
